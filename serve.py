#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serve.py — 本机起 HTTP 服务，把模块直接喂给小火箭

用法:
    python serve.py              # 构建 + 起服务，端口 8000
    python serve.py --port 9000
    python serve.py --dry-run    # 只算地址不起服务

关键点：脚本里 script-path 必须是手机能访问的地址，
所以这里会自动用本机局域网 IP 重新构建 pro 模块，
构建完会把三条导入地址直接打出来，复制粘贴进小火箭即可。
"""

import argparse
import http.server
import os
import socket
import socketserver
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")


def lan_ip():
    """取本机在局域网里的 IP（不会真的发包）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--with-aggressive", action="store_true")
    ap.add_argument("--rebuild", action="store_true",
                    help="强制用本机局域网 IP 重建模块（会覆盖模块里已配置的 script-url，"
                         "如果脚本已经挂在 Gist 上，别加这个）")
    args = ap.parse_args()

    ip = lan_ip()
    base = "http://%s:%d" % (ip, args.port)
    script_url = base + "/adclean.js"

    pro_path = os.path.join(DIST, "adblock-pro.module")
    if args.rebuild or not os.path.exists(pro_path):
        # 用真实 IP 重建，保证 script-path 手机能拉到
        cmd = [sys.executable, os.path.join(ROOT, "build.py"), "--script-url", script_url]
        if args.with_aggressive:
            cmd.append("--with-aggressive")
        subprocess.run(cmd, check=True)
    else:
        print("已存在模块，跳过重建（脚本地址保持模块里原有的；要改成局域网地址加 --rebuild）")

    # 打印模块里实际的 script-path，避免以为在用局域网
    try:
        with open(pro_path, "r", encoding="utf-8") as f:
            for line in f:
                if "script-path=" in line:
                    print("  脚本源: %s" % line.split("script-path=", 1)[1].strip())
    except OSError:
        pass

    print()
    print("=" * 62)
    print("把小火箭切到「配置」全局路由，然后按下面对应步骤导入：")
    print()
    print("  [纯规则版]  配置 > 模块 > 右上角 + > 填地址 > 下载")
    print("  %s/adblock-lite.module" % base)
    print()
    print("  [完整版]    需要先开 HTTPS 解密 + 装证书，再导入")
    print("  %s/adblock-pro.module" % base)
    print()
    print("  [完整版+外链] 规则随小火箭自动更新刷新，不用重导模块")
    print("  %s/adblock-pro-plus.module" % base)
    print()
    print("  [仅规则集]  配置 > 规则 > 添加规则集")
    print("  %s/adblock.list" % base)
    print()
    print("  手机必须和这台电脑在同一个 Wi-Fi。导完就可以关掉本服务。")
    print("  本服务同时提供 adclean.js 和 splash.js，完整版两个脚本都能拉到。")
    print("=" * 62)
    print()

    if args.dry_run:
        print("(--dry-run，未启动服务)")
        return 0

    os.chdir(DIST)
    handler = http.server.SimpleHTTPRequestHandler

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    try:
        with Server((args.bind, args.port), handler) as httpd:
            print("Serving %s on %s ... Ctrl+C 停止" % (DIST, base))
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    except OSError as e:
        print("端口被占用或绑定失败: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    # 用 pythonw 跑（无控制台）时 stdout 是 None，print 会直接崩，这里重定向到日志
    if sys.stdout is None:
        log_dir = os.path.join(ROOT, "logs")
        os.makedirs(log_dir, exist_ok=True)
        f = open(os.path.join(log_dir, "serve.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
    sys.exit(main())
