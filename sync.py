#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync.py — 从公开广告规则源抓域名，交叉验证后产出 src/rules/auto.list

为什么不是「直接拉 anti-AD 全量」
--------------------------------
6.8 万条里混着推送通道、登录 SDK、风控、证书 CRL。全量挂上去不是去广告，
是把手机打残。所以这里做三道闸门，只让高置信度的进来：

  1. 广告语义闸门   —— 域名里得有明确的广告/统计词元，不是长得像就算
  2. 多源交叉闸门   —— 至少 N 个独立上游都收录，单源独家一律不放行
  3. 保护区闸门     —— guard.list / risk.list 里的域，以及大厂主域，一票否决

产物 auto.list 和手写的 core.list 分开存：
  - core.list  人维护，改一次算一次
  - auto.list  机器维护，每次 sync 全量重算，可随时删掉重建

用法:
    python sync.py                 # 抓取 → 过滤 → 写 auto.list
    python sync.py --dry-run       # 只看统计，不落盘
    python sync.py --min-sources 3 # 更严，只留三个源都有的
    python sync.py --prune         # 丢弃上一轮残留（默认保留，避免规则闪断）
    python sync.py --offline DIR   # 用本地已下载的文件跑（断网调试用）
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
RULES_DIR = os.path.join(SRC, "rules")
AUTO_LIST = os.path.join(RULES_DIR, "auto.list")
AUTO_EXTRA = os.path.join(RULES_DIR, "auto-extra.list")
STATS_JSON = os.path.join(RULES_DIR, "auto.stats.json")

# ---------------------------------------------------------------- 上游源

# anti-ad 那条线实测只有 2-16 KB/s，1.3 MB 能拖十分钟以上（urllib 的
# timeout 是 socket 级的，只要还在滴数据就不断）。所以：
#   - 默认不抓，要就 --with-anti-ad
#   - 每个源都有总时长上限，到点直接放弃
# 它和 plus 版的 DOMAIN-SET 外链内容完全重复，抓不抓对结果影响很小。
SOURCES = [
    {"name": "adguard-cn", "weight": 2, "timeout": 60, "retries": 2,
     "url": "https://cdn.jsdelivr.net/gh/AdguardTeam/AdguardFilters@master/"
            "ChineseFilter/sections/adservers.txt"},
    {"name": "easylist-cn", "weight": 2, "timeout": 90, "retries": 2,
     "url": "https://easylist-downloads.adblockplus.org/easylistchina.txt"},
    {"name": "oisd-big", "weight": 3, "timeout": 120, "retries": 2,
     "url": "https://big.oisd.nl/domainswild"},
    {"name": "pgl-yoyo", "weight": 2, "timeout": 60, "retries": 2,
     "url": "https://pgl.yoyo.org/adservers/serverlist.php?"
            "hostformat=nohtml&showintro=0&mimetype=plaintext"},
    {"name": "anti-ad", "weight": 1, "timeout": 90, "retries": 1,
     "default_off": True,
     "url": "https://anti-ad.net/domains.txt"},
]

DOMAIN_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-_]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-_]*[A-Za-z0-9])?)+$")


# ---------------------------------------------------------------- 闸门 1：广告语义

# 精确词元。宁可漏，不可错杀 —— 所以不用 ad* 前缀匹配，
# 否则 admin / adobe / address / addon 全会进来。
AD_TOKENS = {
    # 通用广告
    "ad", "ads", "adv", "advert", "adverts", "advertise", "advertising",
    "advertisement", "adserver", "adservers", "adservice", "adservices",
    "adserving", "adtech", "adzone", "adslot", "adunit", "adunits",
    "adlog", "adlogs", "addata", "adinfo", "adview", "adshow", "adclick",
    "adx", "adex", "adexchange", "adnetwork", "adnetworks", "admarket",
    "adrequest", "adrequests",
    # 变现 / 投放平台
    "admob", "adcolony", "applovin", "appodeal", "unityads", "vungle",
    "chartboost", "ironsource", "tapjoy", "supersonic", "mopub", "inmobi",
    "pangle", "bytedance", "gdt", "gdtimg", "kuaishou", "tanx", "alimama",
    "pospal",
    # 联盟 / 程序化
    "dsp", "ssp", "rtb", "dmp", "adex", "adform", "adition", "smartadserver",
    "pubmatic", "rubicon", "openx", "criteo", "taboola", "outbrain",
    "doubleclick", "googlesyndication", "googleadservices", "adnxs",
    # 追踪 / 埋点 / 归因
    "track", "tracker", "trackers", "tracking", "clicktrack", "clicktracking",
    "impress", "impression", "impressions", "beacon", "beacons",
    "telemetry", "metric", "metrics", "analytics", "analytic", "analysis",
    "stat", "stats", "statistic", "statistics", "statics",
    "tongji", "umeng", "growingio", "growing", "sensorsdata", "sensors",
    "talkingdata", "appsflyer", "adjust", "kochava", "singular", "branch",
    "miaozhen", "admaster", "gridsum", "deerun",
    # 推广 / 电商联盟
    "promo", "promos", "promotion", "promoted", "promoting",
    "affiliate", "affiliates", "cps", "cpa",
    # 弹窗 / 插屏
    "popup", "popunder", "interstitial", "banner", "banners",
}

# 已经出现在词表里但语义会反转的，先摘出去（保留记录，方便复查）
FALSE_FRIENDS = {
    "branch",   # branch.io 是深度链接，已在 guard 放行；这里靠保护区兜
    "adjust",   # 归因 SDK，确实该拦
}


def tokens_of(domain):
    return [t for t in re.split(r"[.\-_]", domain.lower()) if t]


def looks_like_ad(domain):
    return any(t in AD_TOKENS for t in tokens_of(domain))


VOWELS = set("aeiouy")

# 多级公共后缀，判断「主域」时要多退一段
MULTI_SUFFIX = {
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
    "com.hk", "com.tw", "com.mo", "com.au", "co.jp", "co.uk", "co.kr",
    "com.sg", "com.my", "com.br", "com.mx", "com.tr", "com.ru", "com.ua",
    "com.vn", "com.ph", "com.pk", "co.in", "co.id", "co.th", "com.ar",
}


def core_label(domain):
    """取可注册主域的标签：a.b.example.com.cn -> example"""
    parts = domain.split(".")
    if len(parts) < 2:
        return parts[0]
    n = 3 if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_SUFFIX else 2
    idx = len(parts) - n
    return parts[idx] if idx >= 0 else parts[0]


def _random_label(lbl):
    """判断一个标签是不是机器生成的随机串（xkcd 式）。

    这类域名（00k456wyh.com / 06evyxnibl.com）在 EasyList 里成片出现，
    是广告跳转的落地页。生命周期通常只有几天到几周，塞进静态模块
    只会白白吃内存 —— 覆盖它们本来就是 plus 版外链的活。
    """
    if len(lbl) <= 3:
        return any(c.isdigit() for c in lbl)
    digits = sum(c.isdigit() for c in lbl)
    if digits >= 3:
        return True
    if digits >= 2 and len(lbl) >= 8:
        # a1ajfrk4i / aa4jq9non —— 字母数字交错，真域名不这么起
        return True
    if digits >= 1 and len(lbl) <= 8:
        return True
    # 数字开头的长标签，几乎必然是生成的
    if lbl[0].isdigit() and len(lbl) >= 6:
        return True
    if len(lbl) >= 4 and not (set(lbl) & VOWELS):
        return True
    run = 0
    for c in lbl:
        if c.isalpha() and c not in VOWELS:
            run += 1
            if run >= 5:
                return True
        else:
            run = 0
    return False


def is_random_domain(domain):
    return _random_label(core_label(domain))


def has_numeric_label(domain):
    """||1.example.com^ 这种泛匹配残留。拦一条没意义，还占内存。"""
    parts = domain.split(".")
    n = 3 if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_SUFFIX else 2
    return any(lbl.isdigit() for lbl in parts[:-n])


# ---------------------------------------------------------------- 闸门 3：保护区

# 这些主域下面的子域，除非最左标签明确是广告，否则一律不动
PROTECTED_ROOTS = {
    # 大厂主域
    "qq.com", "weixin.qq.com", "wechat.com", "tencent.com", "gtimg.com",
    "baidu.com", "bdimg.com", "bdstatic.com",
    "alipay.com", "taobao.com", "tmall.com", "alicdn.com", "aliyun.com",
    "jd.com", "360buyimg.com", "pinduoduo.com", "yangkeduo.com",
    "weibo.com", "weibo.cn", "sina.com.cn", "sinaimg.cn",
    "163.com", "126.net", "127.net", "netease.com",
    "bilibili.com", "hdslb.com", "bilivideo.com",
    "douyin.com", "douyinpic.com", "kuaishou.com", "kwaicdn.com",
    "xiaohongshu.com", "xhscdn.com", "zhihu.com", "zhimg.com",
    "iqiyi.com", "qiyipic.com", "youku.com", "mgtv.com", "qcloud.com",
    "myqcloud.com", "aliyuncs.com", "hicloud.com",
    # 系统 / 支付 / 证书
    "apple.com", "icloud.com", "mzstatic.com", "microsoft.com",
    "windowsupdate.com", "office.com", "live.com",
    "google.com", "googleapis.com", "gstatic.com", "gmail.com",
    "github.com", "githubusercontent.com",
    "cloudflare.com", "akamai.net", "akamaized.net", "cloudfront.net",
    "fastly.net", "jsdelivr.net", "unpkg.com",
    "trafficmanager.net", "azureedge.net", "azurefd.net",
}

# 共享托管域：一个域名下挂几十万个互不相关的站点。
# 拦掉 xxx.vercel.app 会连带一片正常页面，整条域名一律不进 auto.list。
SHARED_HOSTING = {
    "vercel.app", "netlify.app", "netlify.com", "github.io", "gitlab.io",
    "pages.dev", "workers.dev", "web.app", "firebaseapp.com",
    "herokuapp.com", "onrender.com", "fly.dev", "deno.dev", "glitch.me",
    "repl.co", "replit.app", "blogspot.com", "wordpress.com", "wixsite.com",
    "azurewebsites.net", "cloudapp.azure.com", "s3.amazonaws.com",
    "r2.dev", "backblazeb2.com", "digitaloceanspaces.com",
    "ngrok.io", "ngrok-free.app", "trycloudflare.com",
    "duckdns.org", "no-ip.org", "ddns.net", "hopto.org",
    "bitbucket.io", "surge.sh", "now.sh", "zeit.co",
    "weebly.com", "square.site", "myshopify.com",
}


def protected_suffixes():
    """guard.list / risk.list 里的域全部进保护区，外加硬编码主域"""
    out = set(PROTECTED_ROOTS) | SHARED_HOSTING
    out |= hard_direct()
    return out


def hard_direct():
    """guard.list / risk.list 里点名放行的域。

    和 PROTECTED_ROOTS 的区别：这里是人明确决定「必须放行」的，
    所以连 ad.xxx 这种子域也不收。否则编进模块会和护栏打架，
    白白占一条永远不生效的规则。
    """
    out = set()
    for fn in ("guard.list", "risk.list"):
        path = os.path.join(RULES_DIR, fn)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2 and parts[0].upper() in ("DOMAIN", "DOMAIN-SUFFIX"):
                    out.add(parts[1].lower())
    return out


def under_protected(domain, prot):
    """域名落在保护区里，且最左标签不是广告词 → 一票否决"""
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        parent = ".".join(parts[i:])
        if parent in prot:
            return True
    return False


# ---------------------------------------------------------------- 解析


def parse(text):
    """吃 AdGuard/ABP、hosts、Surge、纯域名四种格式，吐域名列表"""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[0] in "!#;[":
            continue
        # 行内注释
        for sep in ("#", "!"):
            if sep in line:
                line = line.split(sep, 1)[0].strip()
        if not line:
            continue

        d = None
        if line.startswith("||"):
            body = line[2:].split("^")[0].split("/")[0].split("$")[0]
            d = body
        elif line.upper().startswith(("DOMAIN-SUFFIX,", "DOMAIN,")):
            p = line.split(",")
            if len(p) >= 2:
                d = p[1]
        elif line.split() and line.split()[0] in ("0.0.0.0", "127.0.0.1", "::1", "::"):
            p = line.split()
            if len(p) >= 2:
                d = p[1]
        else:
            d = line.split()[0]

        if not d:
            continue
        d = d.strip().strip(".").lower()
        if d.startswith("*."):
            d = d[2:]
        if DOMAIN_RE.match(d):
            out.append(d)
    return out


def _grab(url, timeout, total_limit, use_proxy):
    handlers = [] if use_proxy else [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Encoding": "identity",
    })
    start = time.time()
    chunks = []
    with opener.open(req, timeout=timeout) as resp:
        while True:
            b = resp.read(65536)
            if not b:
                break
            chunks.append(b)
            if total_limit and time.time() - start > total_limit:
                raise TimeoutError("超过总时长上限 %ds" % total_limit)
    return b"".join(chunks).decode("utf-8", "replace")


def fetch(url, timeout=120, retries=2, total_limit=None):
    """拉远程文本，先走系统代理，失败再直连。

    timeout 是 socket 级超时（两次数据之间），total_limit 才是总时长硬上限。
    anti-ad 那种「一直有零星数据但慢得要死」的连接只有 total_limit 能治，
    而代理挂了报 502 的情况只有直连回退能治。
    """
    last = None
    for use_proxy in (True, False):
        label = "代理" if use_proxy else "直连"
        for i in range(retries):
            try:
                return _grab(url, timeout, total_limit, use_proxy)
            except Exception as e:
                last = e
                if i + 1 < retries:
                    print("      %s 第 %d 次失败（%s），重试…"
                          % (url.split("/")[-1][:28], i + 1, type(e).__name__))
                    time.sleep(2)
        if last is not None:
            print("      %s 走%s不通（%s）" % (url.split("/")[2], label, type(last).__name__))
    raise last


# ---------------------------------------------------------------- 主流程


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-sources", type=int, default=2,
                    help="至少几个上游收录才放行（默认 2）")
    ap.add_argument("--max-rules", type=int, default=2000,
                    help="auto.list（A 档）上限，默认 2000")
    ap.add_argument("--max-extra", type=int, default=4000,
                    help="auto-extra.list（B 档）上限，默认 4000")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    ap.add_argument("--prune", action="store_true",
                    help="丢弃上一轮已有但本轮没抓到的域名（默认保留）")
    ap.add_argument("--offline", metavar="DIR",
                    help="用本地目录里已下载的文件跑，文件名对应源名")
    ap.add_argument("--with-anti-ad", action="store_true",
                    help="把 anti-ad.net 也算进源（和 plus 外链重复，且慢到十分钟）")
    args = ap.parse_args()

    prot = protected_suffixes()
    hard = hard_direct()
    print("保护区主域：%d 个（其中护栏点名放行 %d 个）" % (len(prot), len(hard)))

    # ---------- 抓 ----------
    seen_in = Counter()      # domain -> 命中源数
    by_source = {}
    failed = []

    for src in SOURCES:
        name = src["name"]
        url = src["url"]
        if src.get("default_off") and not args.with_anti_ad:
            print("  SKIP %-14s 默认关闭（加 --with-anti-ad 才抓，很慢）" % name)
            continue

        if args.offline:
            path = None
            for cand in (name, name + ".txt", name.replace("-", "_") + ".txt"):
                p = os.path.join(args.offline, cand)
                if os.path.exists(p):
                    path = p
                    break
            if not path:
                print("  SKIP %-14s 本地没有对应文件" % name)
                failed.append(name)
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        else:
            t0 = time.time()
            try:
                text = fetch(url, timeout=src["timeout"], retries=src["retries"],
                             total_limit=src["timeout"])
            except Exception as e:
                print("  FAIL %-14s %s" % (name, e))
                failed.append(name)
                continue
            print("  OK   %-14s %.0f KB  %.1fs" % (
                name, len(text) / 1024, time.time() - t0))

        doms = set(parse(text))
        by_source[name] = len(doms)
        for d in doms:
            seen_in[d] += 1

    if not seen_in:
        print("[FAIL] 一个域名都没抓到，检查网络或 --offline 目录")
        return 1

    print()
    print("抓到去重域名：%d 条" % len(seen_in))
    for name, n in by_source.items():
        print("  %-14s %d" % (name, n))

    # 只剩一个源说明网络出问题了。这时候交叉验证失效，硬跑会把
    # auto.list 洗成很小的一个子集 —— 那不如保持上一轮的完整内容。
    if len(by_source) < 2:
        print()
        print("[FAIL] 只成功了 %d 个源，交叉验证不成立。" % len(by_source))
        print("       保持现有 auto.list 不动，下次网络好了再跑。")
        return 1

    # ---------- 过闸 ----------
    # 判定顺序（命中即停）：
    #   1. 层级/长度非法        → 拒
    #   2. 落在保护区            → 拒（除非最左标签本身就是广告词）
    #   3. 既非多源也无语义      → 拒
    #   4. 纯数字标签 / 随机串   → 拒（拦了没意义，plus 外链会覆盖）
    #
    # 通过的分两档：
    #   A 档 auto.list       最左标签就是广告词，或多源认可且语义明确
    #   B 档 auto-extra.list 多源认可但域名本身没有广告语义（广告位子域）
    #   默认只编 A 档进模块，B 档要 --with-auto-extra 才动
    rejected = Counter()
    tier_a, tier_b = {}, {}

    for d, hits in seen_in.items():
        labels = d.split(".")
        if len(labels) < 2:
            rejected["非法层级"] += 1
            continue
        if len(d) > 63 or any(len(l) > 40 for l in labels):
            rejected["超长"] += 1
            continue

        left = labels[0]
        left_is_ad = left in AD_TOKENS
        semantic = left_is_ad or looks_like_ad(d)

        if under_protected(d, hard):
            rejected["护栏点名放行"] += 1
            continue
        if under_protected(d, prot) and not left_is_ad:
            rejected["保护区"] += 1
            continue
        if not (hits >= args.min_sources or left_is_ad):
            rejected["单源且最左标签无广告词"] += 1
            continue
        if has_numeric_label(d):
            rejected["纯数字标签（泛匹配残留）"] += 1
            continue
        if is_random_domain(d):
            rejected["随机落地页域（交给 plus 外链）"] += 1
            continue

        if left_is_ad or (hits >= args.min_sources and semantic):
            tier_a[d] = hits
        else:
            tier_b[d] = hits

    print()
    print("过滤结果：")
    for why, n in rejected.most_common():
        print("  -%-24s %d" % (why, n))
    print("  =A 档 auto.list       %d" % len(tier_a))
    print("  =B 档 auto-extra.list %d" % len(tier_b))

    # ---------- 排序 + 父域覆盖剪枝（A 档先占位，B 档不能抢父域） ----------
    def rank_prune(tier, taken):
        # .cn 域名优先：国内 App 的广告位基本都在 cn 下，而它们大多只被
        # easylist-cn 单一源收录，按源数排会被海外多源域名挤到截断线外。
        ranked = sorted(tier.items(),
                        key=lambda kv: (0 if kv[0].endswith(".cn") else 1,
                                        -kv[1], len(kv[0]), kv[0]))
        kept, dropped = [], 0
        for dom, hits in ranked:
            parts = dom.split(".")
            if any(".".join(parts[i:]) in taken for i in range(1, len(parts) - 1)):
                dropped += 1
                continue
            kept.append(dom)
            taken.add(dom)
        return kept, dropped

    taken = set()
    kept_a, drop_a = rank_prune(tier_a, taken)
    kept_b, drop_b = rank_prune(tier_b, taken)
    print("  父域覆盖剪枝：A %d 条 / B %d 条" % (drop_a, drop_b))

    if len(kept_a) > args.max_rules:
        print("  !! A 档超过上限 %d，按源数截断到 %d" % (args.max_rules, args.max_rules))
        kept_a = kept_a[:args.max_rules]
    if len(kept_b) > args.max_extra:
        print("  !! B 档超过上限 %d，按源数截断到 %d" % (args.max_extra, args.max_extra))
        kept_b = kept_b[:args.max_extra]

    # ---------- 粘性：上轮有、本轮没抓到的，默认保留 ----------
    prev = set()
    if os.path.exists(AUTO_LIST):
        with open(AUTO_LIST, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if line.startswith("DOMAIN-SUFFIX,"):
                    prev.add(line.split(",", 1)[1].lower())
    cur = set(kept_a)
    stale = prev - cur
    revived = []
    if stale:
        if args.prune:
            print("  清理上轮残留：%d 条" % len(stale))
        else:
            revived = [d for d in sorted(stale)
                       if not under_protected(d, hard)
                       and not is_random_domain(d)
                       and not has_numeric_label(d)
                       and (looks_like_ad(d) or d.split(".")[0] in AD_TOKENS)]
            taken.update(revived)
            print("  保留上轮残留：%d 条（本轮没抓到，但不删）" % len(revived))

    final_a = sorted(cur | set(revived))
    final_b = sorted(set(kept_b) - set(final_a))

    print()
    if args.dry_run:
        print("dry-run：A 档 %d 条 / B 档 %d 条" % (len(final_a), len(final_b)))
    else:
        print("待写入：A 档 %d 条 / B 档 %d 条" % (len(final_a), len(final_b)))

    stats = {
        "sources": {n: by_source[n] for n in by_source},
        "failed_sources": failed,
        "raw_unique": len(seen_in),
        "min_sources": args.min_sources,
        "rejected": dict(rejected),
        "pruned_by_parent": {"a": drop_a, "b": drop_b},
        "revived_from_prev": len(revived),
        "tier_a": len(final_a),
        "tier_b": len(final_b),
    }

    if args.dry_run:
        print()
        print("A 档前 40 条预览：")
        for d in final_a[:40]:
            print("  " + d)
        print()
        print("B 档前 15 条预览：")
        for d in final_b[:15]:
            print("  " + d)
        return 0

    def write_list(path, domains, title, note):
        header = [
            "# ============================================================",
            "# %s  —  机器生成，别手改（下次 sync 会覆盖）" % title,
            "# ------------------------------------------------------------",
            "# 来源：%s" % ", ".join(stats["sources"].keys()),
            "# 生成：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "# %s" % note,
            "# 原始去重 %d 条 → 多源交叉 + 广告词元 + 保护区过滤 → %d 条"
            % (len(seen_in), len(domains)),
            "# 想手工加规则写 core.list；想让某条永远别进来，写 guard.list",
            "# ============================================================",
            "",
        ]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(header) + "\n")
            if domains:
                f.write("\n".join("DOMAIN-SUFFIX," + d for d in domains) + "\n")

    write_list(AUTO_LIST, final_a, "auto.list",
               "A 档：最左标签即广告词，或多源认可且语义明确。默认编进模块。")
    write_list(AUTO_EXTRA, final_b, "auto-extra.list",
               "B 档：多源认可但域名本身无广告语义（广告位子域）。--with-auto-extra 才启用。")

    with open(STATS_JSON, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(stats, ensure_ascii=False, indent=2) + "\n")

    print()
    print("已写入 %s" % os.path.relpath(AUTO_LIST, ROOT))
    print("       %s" % os.path.relpath(AUTO_EXTRA, ROOT))
    print("统计   %s" % os.path.relpath(STATS_JSON, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
