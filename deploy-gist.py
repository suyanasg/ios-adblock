#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 adclean.js / splash.js 推到 GitHub Gist，然后用 raw 地址重建 pro 模块。

两种用法
--------
自动（需要 classic PAT，勾了 gist 权限）:
    set GIST_TOKEN=ghp_xxxxxxxx
    python deploy-gist.py --auto

手动（你自己在网页建好 Gist，只给 adclean.js 的 raw 地址）:
    python deploy-gist.py --raw https://gist.githubusercontent.com/你/id/raw/adclean.js

无论哪种，最后都会调 build.py 生成模块并 --verify 真拉一遍。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
# 脚本 + 三个模块都推上去。模块也进 Gist 的好处：手机上「模块订阅」直接
# 指 raw 地址，每天构建完自动更新，不用重新导入。
FILES = ["adclean.js", "splash.js",
         "adblock-lite.module", "adblock-pro.module", "adblock-pro-plus.module"]
API = "https://api.github.com/gists"
UA = "ios-adblock-deploy/1.0"


def log(msg):
    print(msg, flush=True)


def opener(use_proxy):
    """use_proxy=False 时绕开系统代理（本机代理链有时会污染 TLS）"""
    if use_proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(url, data=None, token=None, method=None, timeout=25, use_proxy=True):
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "token " + token
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with opener(use_proxy).open(req, timeout=timeout) as resp:
        return resp.status, resp.headers, resp.read()


def fetch_with_fallback(url, token=None, method=None, data=None):
    """先走系统代理，失败再直连，反过来也试一次"""
    errs = []
    for use_proxy in (True, False):
        try:
            return http(url, data=data, token=token, method=method, use_proxy=use_proxy)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise SystemExit("[FAIL] HTTP %s %s\n%s" % (e.code, e.reason, detail))
        except Exception as e:
            errs.append("%s=%s" % ("proxy" if use_proxy else "direct",
                                   "%s: %s" % (type(e).__name__, e)))
    raise SystemExit("[FAIL] 连不上 GitHub API\n  " + "\n  ".join(errs))


def canon_raw(url):
    """把 /raw/<sha>/ 里的 sha 去掉，这样以后更新 Gist 内容地址不变"""
    url = url.strip()
    m = re.match(r"^(https://gist\.githubusercontent\.com/[^/]+/[^/]+/raw)/[0-9a-f]{7,}/?(.*)$", url)
    if m:
        base = m.group(1)
        rest = m.group(2)
        return base + ("/" + rest if rest else "")
    return url


def raw_dir(adclean_raw):
    return adclean_raw.rsplit("/", 1)[0]


def create_gist(token, public=False):
    files = {}
    for name in FILES:
        p = os.path.join(DIST, name)
        if not os.path.isfile(p):
            raise SystemExit("[FAIL] 缺文件 %s，先跑 python build.py" % p)
        with open(p, "r", encoding="utf-8") as f:
            files[name] = {"content": f.read()}

    log("[1/4] 创建 %s Gist ..." % ("public" if public else "secret"))
    status, _, body = fetch_with_fallback(
        API, token=token, method="POST",
        data={"description": "ios-adblock scripts (auto)",
              "public": bool(public), "files": files})
    info = json.loads(body.decode("utf-8"))
    gid = info["id"]
    html = info["html_url"]
    owner = info["owner"]["login"]
    log("      OK  https://gist.github.com/%s/%s" % (owner, gid))
    return "https://gist.githubusercontent.com/%s/%s/raw/adclean.js" % (owner, gid), html


def update_gist(token, gist_id):
    """原地更新已有 Gist 的两个脚本文件。raw 地址保持不变，模块不用重导。

    每日自动构建走这条，避免反复建新 Gist —— 建新的会让手机上的
    script-path 指向旧地址，等于永远拿不到新脚本。
    """
    files = {}
    for name in FILES:
        p = os.path.join(DIST, name)
        if not os.path.isfile(p):
            raise SystemExit("[FAIL] 缺文件 %s，先跑 python build.py" % p)
        with open(p, "r", encoding="utf-8") as f:
            files[name] = {"content": f.read()}

    log("[1/4] 更新 Gist %s ..." % gist_id)
    _, _, body = fetch_with_fallback(
        "%s/%s" % (API, gist_id), token=token, method="PATCH", data={"files": files})
    info = json.loads(body.decode("utf-8"))
    owner = info["owner"]["login"]
    changed = sorted(info.get("files", {}).keys())
    log("      OK  已更新 %d 个文件：%s" % (len(changed), ", ".join(changed)))
    return "https://gist.githubusercontent.com/%s/%s/raw/adclean.js" % (owner, gist_id)


def check_scopes(token):
    log("[0/4] 检查 token 权限 ...")
    try:
        _, headers, _ = fetch_with_fallback(
            "https://api.github.com/user", token=token, use_proxy=False)
    except SystemExit:
        _, headers, _ = fetch_with_fallback("https://api.github.com/user", token=token)
    scopes = (headers.get("X-OAuth-Scopes") or "").strip()
    login = ""
    try:
        _, _, body = fetch_with_fallback("https://api.github.com/user", token=token)
        login = json.loads(body.decode("utf-8")).get("login", "")
    except Exception:
        pass
    log("      账号 = %s" % (login or "?"))
    log("      权限 = [%s]" % scopes)
    if "gist" not in scopes.split(", "):
        raise SystemExit(
            "[FAIL] 这个 token 没有 gist 权限，建不了 Gist。\n"
            "       去 https://github.com/settings/tokens/new 新建 classic token，\n"
            "       只勾 gist 一项。fine-grained token 不支持 Gist API，别用。")
    return login


def wait_raw(url, tries=6):
    """Gist 建完 CDN 有延迟，重试几次"""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with opener(False).open(req, timeout=20) as resp:
                data = resp.read(2048)
                if resp.status == 200 and data and b"<html" not in data[:200].lower():
                    return True, "%d B 起" % len(data)
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            if i == tries - 1:
                return False, last
        time.sleep(3)
    return False, "重试耗尽"


def rebuild(adclean_raw, splash_raw):
    log("[3/4] 重建 pro 模块 ...")
    cmd = [sys.executable, os.path.join(HERE, "build.py"),
           "--script-url", adclean_raw,
           "--splash-url", splash_raw,
           "--verify"]
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout or "").splitlines():
        if line.strip():
            log("      " + line)
    if r.returncode != 0:
        for line in (r.stderr or "").splitlines():
            if line.strip():
                log("      " + line)
        raise SystemExit("[FAIL] 构建失败（verify 没通过），模块没更新")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="用 GIST_TOKEN 自动建 Gist")
    ap.add_argument("--update", metavar="GIST_ID",
                    help="原地更新已有 Gist（每日自动构建用这个，raw 地址不变）")
    ap.add_argument("--raw", help="adclean.js 的 gist raw 地址（手动模式）")
    ap.add_argument("--splash-raw", help="splash.js 的 raw 地址，默认同目录推导")
    ap.add_argument("--public", action="store_true", help="建 public gist（默认 secret）")
    args = ap.parse_args()

    if args.auto:
        token = os.environ.get("GIST_TOKEN", "").strip()
        if not token:
            raise SystemExit("[FAIL] 没读到 GIST_TOKEN 环境变量")
        check_scopes(token)
        adclean_raw, _ = create_gist(token, args.public)
    elif args.update:
        token = os.environ.get("GIST_TOKEN", "").strip()
        if not token:
            raise SystemExit("[FAIL] 没读到 GIST_TOKEN 环境变量")
        check_scopes(token)
        adclean_raw = update_gist(token, args.update.strip())
    elif args.raw:
        adclean_raw = canon_raw(args.raw)
        log("[1/4] 使用手动 Gist 地址（跳过创建）")
        log("      " + adclean_raw)
    else:
        ap.print_help()
        return 2

    d = raw_dir(adclean_raw)
    splash_raw = canon_raw(args.splash_raw) if args.splash_raw else d + "/splash.js"

    log("[2/4] 校验两个 raw 地址可达性 ...")
    for name, url in (("adclean.js", adclean_raw), ("splash.js", splash_raw)):
        ok, detail = wait_raw(url)
        log("      %-4s %-12s %s  (%s)" % ("OK" if ok else "FAIL", name, url, detail))
        if not ok:
            raise SystemExit(
                "[FAIL] %s 拉不到。检查：\n"
                "       1. 文件名必须完全一致（adclean.js / splash.js）\n"
                "       2. Gist 里两个文件都要有\n"
                "       3. raw 地址要带文件名结尾" % name)

    rebuild(adclean_raw, splash_raw)

    log("[4/4] 完成。现在可以导模块了。")
    log("")
    log("  pro 模块  http://192.168.5.3:8000/adblock-pro.module")
    log("")
    log("  改完脚本后：写回同一个 Gist → python deploy-gist.py --raw <同一地址>")
    log("  raw 地址不变，模块不用重导。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
