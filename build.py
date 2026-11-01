#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — 把小火箭去广告模块的源码编译成可导入产物

用法:
    python build.py
    python build.py --script-url https://raw.githubusercontent.com/USER/REPO/main/dist/adclean.js
    python build.py --with-aggressive          # 把激进区规则也编进去

产物 (dist/):
    adblock-lite.module   纯规则版：无 MITM、无脚本、本地粘贴即可用
    adblock-pro.module    完整版：规则 + URL Rewrite + JS 清洗 + MITM
    adblock.list          纯规则集，供 RULE-SET 引用或 Clash 转换
    adclean.js            JS 清洗脚本（需要单独托管）
    manifest.txt          构建报告
"""

import argparse
import json
import os
import posixpath
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
DIST = os.path.join(ROOT, "dist")

GIST_RAW = ("https://gist.githubusercontent.com/"
            "suyanasg/115df6b1947d22f7d356f594f0ce1223/raw/adclean.js")

# 默认指向 Gist，构建出来直接能用手机导。本机临时推送用 serve.py --rebuild
# 覆盖成本机局域网地址。
DEFAULT_SCRIPT_URL = GIST_RAW

# DOMAIN-SET 外链：这层规则由外链自己更新，小火箭「自动更新」会刷新它
DEFAULT_DOMAIN_SETS = ["https://anti-ad.net/domains.txt"]

VALID_RULE_KINDS = {
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-SET",
    "IP-CIDR", "IP-CIDR6", "USER-AGENT", "URL-REGEX", "PROCESS-NAME",
}

DOMAIN_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-_]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-_]*[A-Za-z0-9])?)+$")

# (脚本名, 文件名, 对应的 MITM 源文件)
SCRIPTS = [
    ("AdClean", "adclean.js", "mitm.txt"),
    ("SplashClean", "splash.js", "mitm-splash.txt"),
]



def read_lines(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if line:
                out.append(line)
    return out


def load_rules(*files):
    rules, seen, bad = [], set(), []
    for path in files:
        for line in read_lines(path):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                bad.append((path, line, "缺少字段"))
                continue
            kind, value = parts[0].upper(), parts[1]
            if kind not in VALID_RULE_KINDS:
                bad.append((path, line, "未知规则类型 %s" % kind))
                continue
            if kind.startswith("DOMAIN") and kind != "DOMAIN-KEYWORD":
                if not DOMAIN_RE.match(value):
                    bad.append((path, line, "非法域名"))
                    continue
            key = "%s,%s" % (kind, value.lower())
            if key in seen:
                continue
            seen.add(key)
            # 统一丢弃策略字段，模块构建时再补 REJECT
            rules.append("%s,%s" % (kind, value))
    return rules, bad


GUARD_ACTIONS = {"DIRECT", "REJECT"}


def load_guard():
    """护栏规则：原样保留策略字段，不参与 prune、不参与去重合并。

    这批规则的唯一作用是在 DOMAIN-SET 之前把某些域名放行，
    所以必须保留 DIRECT 策略，且必须完整写在 [Rule] 最前面。
    """
    path = os.path.join(SRC, "rules", "guard.list")
    guard, seen, bad = [], set(), []
    for line in read_lines(path):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            bad.append((path, line, "需要 3 个字段：类型,值,策略"))
            continue
        kind, value, action = parts[0].upper(), parts[1], parts[2].upper()
        if kind not in VALID_RULE_KINDS:
            bad.append((path, line, "未知规则类型 %s" % kind))
            continue
        if kind.startswith("DOMAIN") and kind != "DOMAIN-KEYWORD":
            if not DOMAIN_RE.match(value):
                bad.append((path, line, "非法域名"))
                continue
        if action not in GUARD_ACTIONS:
            bad.append((path, line, "未知策略 %s" % action))
            continue
        full = "%s,%s,%s" % (kind, value, action)
        if full.lower() in seen:
            continue
        seen.add(full.lower())
        guard.append(full)
    return guard, bad


def guard_conflicts(guard, rules):
    """护栏想放行、精选区想拦截的域名。冲突时护栏优先（它在前面）。

    这种情况只会在 --with-risk 时出现：risk.list 里的推送/登录域名
    和 guard.list 里的白名单是同一批。不能两样都要。
    """
    blocked = {r.split(",", 1)[1].lower() for r in rules
               if r.startswith(("DOMAIN,", "DOMAIN-SUFFIX,"))}
    out = []
    for g in guard:
        kind, value, action = g.split(",", 2)
        if action != "DIRECT":
            continue
        v = value.lower()
        for b in blocked:
            if b == v or b.endswith("." + v):
                out.append((g, b))
                break
    return out


def fetch_text(url, timeout=180, retries=2, label=""):
    """拉远程文本。

    anti-ad.net 这条线在很多网络下只有 10-15 KB/s，1.3 MB 要两三分钟，
    所以超时给到 180s、重试 2 次。真要快就用 --audit-source 传本地文件。
    """
    if not url.startswith(("http://", "https://")):
        with open(url, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept-Encoding": "identity",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            if i + 1 < retries:
                print("      %s 第 %d 次失败（%s），重试…"
                      % (label or "拉取", i + 1, type(e).__name__))
                time.sleep(3)
    raise last


def audit_upstream(domain_sets, rules, guard, risky_domains=(), mitm_hosts=()):
    """拉一遍外链域名集做实检：格式、重复、冗余、会不会把护栏盖掉。"""
    report = {"sources": [], "total": 0, "bad_lines": [], "redundant": 0,
              "dupes": 0, "guard_hits": [], "risky_hits": [], "ours_covered": 0,
              "ours_total": 0, "ours_uncovered": [], "mitm_killed": []}
    all_doms = set()
    for url in domain_sets:
        try:
            text = fetch_text(url, label=url.split("/")[-1])
        except Exception as e:
            report["sources"].append((url, "FAIL", "%s: %s" % (type(e).__name__, e)))
            continue
        lines = [l.strip().lower() for l in text.splitlines()]
        doms = [l for l in lines if l and not l.startswith("#")]
        bad = [d for d in doms if not DOMAIN_RE.match(d)]
        dupes = len(doms) - len(set(doms))
        report["sources"].append((url, "OK", "%d 条域名（%d 行非域名、%d 条重复）"
                                  % (len(set(doms)), len(bad), dupes)))
        report["bad_lines"].extend(bad[:5])
        report["dupes"] += dupes
        all_doms |= set(doms)

    if not all_doms:
        return report, all_doms

    report["total"] = len(all_doms)

    # 冗余：被父域覆盖的子域
    red = 0
    for d in all_doms:
        parts = d.split(".")
        for i in range(1, len(parts) - 1):
            if ".".join(parts[i:]) in all_doms:
                red += 1
                break
    report["redundant"] = red

    # 护栏是否真的能挡住
    for g in guard:
        kind, value, action = g.split(",", 2)
        if action != "DIRECT":
            continue
        v = value.lower()
        hit = [d for d in all_doms if d == v or d.endswith("." + v)]
        if hit:
            report["guard_hits"].append((value, len(hit)))

    # 我们精选的域名有多少已被外链覆盖
    ours = [r.split(",", 1)[1].lower() for r in rules
            if r.startswith("DOMAIN-SUFFIX,")]
    report["ours_total"] = len(ours)
    uncov = []
    cov = 0
    for d in ours:
        parts = d.split(".")
        if any(".".join(parts[i:]) in all_doms for i in range(len(parts) - 1)):
            cov += 1
        else:
            uncov.append(d)
    report["ours_covered"] = cov
    report["ours_uncovered"] = uncov

    # 风险域名命中
    for d in risky_domains:
        n = sum(1 for x in all_doms if x == d or x.endswith("." + d))
        if n:
            report["risky_hits"].append((d, n))

    # MITM 域被外链杀掉 = 脚本永远收不到响应，整条链路哑掉
    for m in mitm_hosts:
        parts = m.lower().split(".")
        hit = None
        for i in range(len(parts) - 1):
            c = ".".join(parts[i:])
            if c in all_doms:
                hit = c
                break
        if hit:
            report["mitm_killed"].append((m, hit))

    return report, all_doms


def prune_covered(rules):
    """删掉已被上级 DOMAIN-SUFFIX 覆盖的子域，减少规则条数（小火箭按条数吃内存）"""
    suffixes = {r.split(",", 1)[1].lower() for r in rules if r.startswith("DOMAIN-SUFFIX,")}
    kept, dropped = [], []
    for r in rules:
        if r.startswith("DOMAIN-SUFFIX,"):
            d = r.split(",", 1)[1].lower()
            parent = next((p for p in suffixes if p != d and d.endswith("." + p)), None)
            if parent:
                dropped.append((r, "被 DOMAIN-SUFFIX,%s 覆盖" % parent))
                continue
        kept.append(r)
    return kept, dropped


def build_rule_section(rules, indent=""):
    lines = ["%s%s,REJECT" % (indent, r) for r in rules]
    return "\n".join(lines)


def load_mitm(name):
    path = os.path.join(SRC, "rules", name)
    return [h.strip().lower() for h in read_lines(path) if h.strip()]


def script_pattern(hosts):
    """把域名列表编成 ^https?://(a|b|c)/ 形式的匹配正则"""
    return "^https?:\\/\\/(%s)\\/" % "|".join(re.escape(h) for h in hosts)


def url_for_script(base_url, filename):
    """--script-url 传的是某个脚本的地址，同目录下的其它脚本自动推导"""
    d = posixpath.dirname(base_url)
    if not d:
        return base_url
    return d.rstrip("/") + "/" + filename


def is_local_url(url):
    """本机/局域网地址：手机换了网络就拉不到，只能临时用"""
    return any(h in url for h in ("127.0.0.1", "localhost", "192.168.",
                                  "10.0.0.", "172.16.", "172.17.", "172.18."))


def verify_url(url, timeout=12):
    """探测远程脚本是否真的拉得到。返回 (ok, 说明)"""
    req = urllib.request.Request(url, headers={"User-Agent": "ios-adblock-build/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(4096)
            status = resp.status
            ctype = resp.headers.get("Content-Type", "?")
        if status != 200:
            return False, "HTTP %s" % status
        if len(data) == 0:
            return False, "内容为空"
        head = data[:200].decode("utf-8", "replace")
        if "<html" in head.lower() or "<!doctype" in head.lower():
            return False, "返回的是 HTML（多半是 404 页面或需要登录）"
        return True, "%d B 起, %s" % (len(data), ctype)
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script-url", default=DEFAULT_SCRIPT_URL,
                    help="adclean.js 的公网地址；同目录下的 splash.js 会自动推导")
    ap.add_argument("--splash-url", default=None,
                    help="splash.js 单独指定地址（两个脚本不在同一目录时用）")
    ap.add_argument("--verify", action="store_true",
                    help="构建后真的去拉一遍 script-path，确认手机能取到")
    ap.add_argument("--with-aggressive", action="store_true")
    ap.add_argument("--with-risk", action="store_true",
                    help="把 risk.list 编进去（推送 SDK / 风控 / 第三方登录 / 深度链接），"
                         "覆盖更全但可能让 App 收不到推送或登不上")
    ap.add_argument("--upstream-url", action="append", default=None,
                    help="DOMAIN-SET 外链地址，可多次指定。默认挂 anti-AD 全量域名集")
    ap.add_argument("--no-upstream", action="store_true",
                    help="不生成 adblock-pro-plus.module（不挂任何外链域名集）")
    ap.add_argument("--no-guard", action="store_true",
                    help="plus 模块不写 guard.list 护栏（推送/登录/证书域会被外链杀掉，慎用）")
    ap.add_argument("--audit-upstream", action="store_true",
                    help="拉一遍外链域名集做实检：格式、冗余、护栏覆盖、精选重复率")
    ap.add_argument("--audit-source", action="append", default=None,
                    help="只对体检生效的源地址，可多次指定。传本地文件路径可离线体检")
    ap.add_argument("--no-auto", action="store_true",
                    help="不编 auto.list（sync.py 抓来的自动增长规则）")
    ap.add_argument("--with-auto-extra", action="store_true",
                    help="把 auto-extra.list 也编进去（广告位子域，多源认可但无语义）")
    args = ap.parse_args()

    if args.no_upstream:
        domain_sets = []
    elif args.upstream_url:
        domain_sets = args.upstream_url
    else:
        domain_sets = DEFAULT_DOMAIN_SETS

    rule_files = [os.path.join(SRC, "rules", "core.list")]
    auto_path = os.path.join(SRC, "rules", "auto.list")
    auto_extra_path = os.path.join(SRC, "rules", "auto-extra.list")
    auto_used = []
    if not args.no_auto and os.path.exists(auto_path):
        rule_files.append(auto_path)
        auto_used.append(auto_path)
    if args.with_auto_extra and os.path.exists(auto_extra_path):
        rule_files.append(auto_extra_path)
        auto_used.append(auto_extra_path)
    if args.with_aggressive:
        rule_files.append(os.path.join(SRC, "rules", "aggressive.list"))
    if args.with_risk:
        rule_files.append(os.path.join(SRC, "rules", "risk.list"))

    rules, bad = load_rules(*rule_files)
    rules, dropped = prune_covered(rules)
    rules.sort()

    rewrites, bad_rw = [], []
    for line in read_lines(os.path.join(SRC, "rules", "rewrite.list")):
        parts = line.rsplit(None, 1)
        if len(parts) != 2 or parts[1] not in ("reject", "reject-200", "reject-img", "reject-dict", "reject-array"):
            bad_rw.append((line, "语法错误"))
            continue
        pattern, action = parts
        try:
            re.compile(pattern)
        except re.error as e:
            bad_rw.append((line, "正则不合法: %s" % e))
            continue
        rewrites.append((pattern, action))

    # mitm-extra.txt 只进全局 [MITM]，不加进任何脚本的 pattern：
    # 它是给 rewrite.list 的路径级 reject 提供解密条件的（比如字节 CDN）。
    mitm = sorted(set(load_mitm("mitm.txt")
                      + load_mitm("mitm-splash.txt")
                      + load_mitm("mitm-extra.txt")))

    guard, bad_guard = load_guard()
    # 护栏只在 plus 里生效；没挂外链或显式 --no-guard 时它不起作用，也就不该报冲突
    conflict = guard_conflicts(guard, rules) if (guard and domain_sets and not args.no_guard) else []

    if not mitm:
        print("[FAIL] MITM 域名为空")
        return 1

    if bad or bad_rw or bad_guard:
        print("[FAIL] 源码有错误：")
        for p, line, why in bad:
            print("  %s | %s | %s" % (os.path.basename(p), line, why))
        for line, why in bad_rw:
            print("  rewrite.list | %s | %s" % (line, why))
        for p, line, why in bad_guard:
            print("  guard.list | %s | %s" % (line, why))
        return 1

    if conflict:
        print("[WARN] guard 与精选规则冲突，护栏在前所以护栏赢：")
        for g, b in conflict:
            print("  %-44s  <->  REJECT %s" % (g, b))
        print("  --with-risk 对这几个域名不会生效。这是设计的取舍：")
        print("  推送/登录断了比广告漏出来更烦，护栏优先。")

    os.makedirs(DIST, exist_ok=True)

    # ---------- 1. 纯规则集 ----------
    rule_list_path = os.path.join(DIST, "adblock.list")
    with open(rule_list_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# adblock.list — Shadowrocket / Surge RULE-SET\n")
        f.write("# 条数: %d\n" % len(rules))
        f.write("\n".join(rules) + "\n")

    # ---------- 2. 先把模块公共片段算好 ----------
    rw_lines = "\n".join("%s %s" % (p, a) for p, a in rewrites)
    hostname_line = "%APPEND% " + ", ".join(mitm)

    script_lines = []
    script_urls = {}
    for name, filename, mitm_src in SCRIPTS:
        hosts = sorted(set(load_mitm(mitm_src)))
        if not hosts:
            print("[FAIL] 脚本 %s 没有关联 MITM 域名" % name)
            return 1
        if filename == "adclean.js":
            # --script-url 就是 adclean.js 的完整地址，不要再推导
            url = args.script_url
        elif args.splash_url:
            url = args.splash_url
        else:
            url = url_for_script(args.script_url, filename)
        script_urls[name] = url
        script_lines.append(
            "{name} = type=http-response,pattern={pat},requires-body=1,max-size=0,timeout=15,script-path={url}".format(
                name=name, pat=script_pattern(hosts), url=url))

    # ---------- 3. 模块生成 ----------
    def module_text(name, desc, extra_rules=(), guard_rules=(), with_script=True):
        out = ["#!name=" + name, "#!desc=" + desc,
               "#!author=seagull", "#!category=Adblock", "", "[Rule]"]
        # 护栏必须排在最前：小火箭规则自上而下匹配，首个命中生效
        if guard_rules:
            out.append("# --- guard: must stay above every REJECT line below ---")
            out.extend(guard_rules)
            out.append("")
        out.append(build_rule_section(rules))
        out.extend(extra_rules)
        if with_script:
            out.append("")
            out.append("[URL Rewrite]")
            out.append(rw_lines)
            out.append("")
            out.append("[Script]")
            out.extend(script_lines)
            out.append("")
            out.append("[MITM]")
            out.append("hostname = " + hostname_line)
        out.append("")
        return "\n".join(out)

    lite_text = module_text(
        "去广告 · 纯规则版",
        "只做域名层拦截，无 MITM、无需证书、不碰 App 数据。共 {n} 条规则。".format(n=len(rules)),
        with_script=False)
    with open(os.path.join(DIST, "adblock-lite.module"), "w", encoding="utf-8", newline="\n") as f:
        f.write(lite_text)

    pro_text = module_text(
        "去广告 · 完整版",
        "规则拦截 + 开屏接口 reject + JSON 广告清洗。需开启 HTTPS 解密并安装证书。规则 {n} 条 / MITM {m} 域。".format(
            n=len(rules), m=len(mitm)))
    with open(os.path.join(DIST, "adblock-pro.module"), "w", encoding="utf-8", newline="\n") as f:
        f.write(pro_text)

    # ---------- 3b. plus 模块（DOMAIN-SET 外链，规则随小火箭自动刷新） ----------
    plus_text = None
    if domain_sets:
        extra = ["DOMAIN-SET,%s,REJECT" % u for u in domain_sets]
        use_guard = guard if not args.no_guard else []
        plus_text = module_text(
            "去广告 · 完整版 + 外链域名集",
            "完整版 + DOMAIN-SET 外链，规则随小火箭「自动更新」刷新，无需重导模块。"
            "已内置 {g} 条护栏（推送/登录/证书域放行），避免外链误伤。"
            "规则 {n} 条 + 外链 {k} 个 / MITM {m} 域。".format(
                g=len(use_guard), n=len(rules), k=len(extra), m=len(mitm)),
            extra_rules=extra, guard_rules=use_guard)
        with open(os.path.join(DIST, "adblock-pro-plus.module"), "w", encoding="utf-8", newline="\n") as f:
            f.write(plus_text)

    # ---------- 4. 拷贝脚本 ----------
    for _, filename, _ in SCRIPTS:
        js_src = os.path.join(SRC, "scripts", filename)
        js_dst = os.path.join(DIST, filename)
        with open(js_src, "r", encoding="utf-8") as f:
            js = f.read()
        with open(js_dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(js)

    # ---------- 5. 报告 ----------
    report = {
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "with_aggressive": args.with_aggressive,
        "with_risk": args.with_risk,
        "auto_lists": [os.path.basename(p) for p in auto_used],
        "rules": len(rules),
        "guard_rules": len(guard),
        "guard_conflicts": [{"guard": g, "blocked": b} for g, b in conflict],
        "pruned_subdomains": len(dropped),
        "rewrite_rules": len(rewrites),
        "mitm_hosts": len(mitm),
        "scripts": [s[0] for s in SCRIPTS],
        "script_url": args.script_url,
        "script_urls": script_urls,
        "script_url_is_local": is_local_url(args.script_url),
        "domain_sets": domain_sets,
        "sizes": {},
    }
    for name in ("adblock-lite.module", "adblock-pro.module", "adblock-pro-plus.module",
                 "adblock.list", "adclean.js", "splash.js"):
        p = os.path.join(DIST, name)
        if os.path.exists(p):
            report["sizes"][name] = os.path.getsize(p)

    with open(os.path.join(DIST, "manifest.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print("=" * 58)
    print("构建完成")
    print("  规则条数      : %d  (剪掉冗余子域 %d 条)" % (len(rules), len(dropped)))
    print("  自动增长区    : %s" % (
        " + ".join(os.path.basename(p) for p in auto_used) if auto_used
        else "未启用（先 python sync.py）"))
    print("  URL Rewrite   : %d 条" % len(rewrites))
    print("  MITM 域名     : %d 个" % len(mitm))
    print("  激进区        : %s" % ("已启用" if args.with_aggressive else "未启用"))
    print("  风险区        : %s" % ("已启用" if args.with_risk else "未启用（推送/风控/登录域名未拦）"))
    print("  护栏规则      : %d 条%s" % (len(guard), "（--no-guard 已关闭）" if args.no_guard else ""))
    print("  lite 模块     : %.1f KB" % (report["sizes"]["adblock-lite.module"] / 1024))
    print("  pro 模块      : %.1f KB" % (report["sizes"]["adblock-pro.module"] / 1024))
    if "adblock-pro-plus.module" in report["sizes"]:
        print("  plus 模块     : %.1f KB  (DOMAIN-SET 外链 %d 个)" % (
            report["sizes"]["adblock-pro-plus.module"] / 1024, len(domain_sets)))
    for name, url in script_urls.items():
        print("  %-13s : %s" % (name, url))
    if is_local_url(args.script_url):
        print()
        print("  !! script-path 是本机/局域网地址，手机换了 Wi-Fi 就拉不到。")
        print("  !! 长期用请挂 Gist（python deploy-gist.py）或用 --script-url 指公网地址。")
    print("=" * 58)

    if args.audit_upstream and (args.audit_source or domain_sets):
        audit_targets = args.audit_source or domain_sets
        print()
        print("上游域名集体检（要拉几 MB，慢）：")
        risky = [g.split(",")[1] for g in guard
                 if g.endswith(",DIRECT") and g.startswith("DOMAIN-SUFFIX,")]
        audit, _ = audit_upstream(audit_targets, rules, guard, risky, mitm)
        for url, state, detail in audit["sources"]:
            print("  %-4s %s" % (state, url))
            print("       %s" % detail)
        if not audit["total"]:
            print()
            print("  没拉到任何数据，体检跳过。这条线经常只有 10-20 KB/s，1.3 MB 要几分钟。")
            print("  要离线体检就先下载再传本地路径：")
            print("    curl -o domains.txt https://anti-ad.net/domains.txt")
            print("    python build.py --audit-upstream --audit-source domains.txt")

        if audit["total"]:
            print()
            print("  域名总数      : %d" % audit["total"])
            print("  被父域覆盖    : %d  (%.1f%%，挂上去纯占内存)"
                  % (audit["redundant"], 100.0 * audit["redundant"] / audit["total"]))
            print("  重复行        : %d" % audit["dupes"])
            print("  精选重复      : %d / %d  (%.0f%% 已被外链覆盖)"
                  % (audit["ours_covered"], audit["ours_total"],
                     100.0 * audit["ours_covered"] / max(1, audit["ours_total"])))
            print("  精选独有      : %d 条（外链没有，删了就是裸奔）"
                  % len(audit["ours_uncovered"]))
            print()
            print("  护栏命中：")
            if audit["guard_hits"]:
                for v, n in audit["guard_hits"]:
                    print("    %-34s 外链里 %d 条，已前置放行" % (v, n))
            else:
                print("    无 —— 护栏全部没命中，可以精简 guard.list")

            print()
            print("  MITM 域体检：")
            if audit["mitm_killed"]:
                for m, hit in audit["mitm_killed"]:
                    print("    !! %-32s 被外链杀了（%s）→ 脚本永远收不到响应" % (m, hit))
                print("    把这几条加进 guard.list 放行，否则 plus 的脚本层等于没有。")
            else:
                print("    %d 个 MITM 域全部安全，脚本能正常拿到响应" % len(mitm))

            report["audit"] = {
                "total": audit["total"],
                "redundant": audit["redundant"],
                "ours_covered": audit["ours_covered"],
                "ours_total": audit["ours_total"],
                "guard_hits": [{"domain": v, "hits": n} for v, n in audit["guard_hits"]],
                "mitm_killed": [{"host": m, "by": h} for m, h in audit["mitm_killed"]],
            }
            with open(os.path.join(DIST, "manifest.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("=" * 58)

    if args.verify:
        print()
        print("远程可达性检查：")
        all_ok = True
        for name, url in script_urls.items():
            ok, detail = verify_url(url)
            all_ok = all_ok and ok
            print("  %-4s %-12s %s" % ("OK" if ok else "FAIL", name, url))
            print("       %s" % detail)
        print("=" * 58)
        if not all_ok:
            print("有脚本拉不到，把 pro 模块导进手机也是白导。先修地址。")
            return 2
        print("两个脚本都能拉到，可以导模块了。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
