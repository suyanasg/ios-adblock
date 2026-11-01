#!/usr/bin/env python3
"""公共 DNS 实测探针。

直接构造 DNS 报文发 UDP:53，不经过系统解析器，所以测的是
「这台机器到那台 DNS 服务器」的真实往返时间。

用法：
    python dns-probe.py              # 默认测一遍
    python dns-probe.py --rounds 5   # 多测几轮取最小值

注意：结果只代表你跑这个脚本的那台机器、那条网络。
换个运营商、换个时段，排名会变。改路由器之前先在路由器后面跑一次。
"""

import argparse
import random
import socket
import struct
import sys
import time

SERVERS = [
    ("223.5.5.5", "阿里 AliDNS"),
    ("223.6.6.6", "阿里 备用"),
    ("119.29.29.29", "腾讯 DNSPod"),
    ("182.254.116.116", "腾讯 备用"),
    ("180.76.76.76", "百度 DNS"),
    ("180.184.1.1", "字节 火山"),
    ("117.50.10.10", "OneDNS"),
    ("114.114.114.114", "114DNS"),
    ("1.1.1.1", "Cloudflare"),
    ("8.8.8.8", "Google"),
    ("9.9.9.9", "Quad9"),
    ("208.67.222.222", "OpenDNS"),
]

DOMAINS = [
    "www.baidu.com",
    "www.qq.com",
    "www.taobao.com",
    "www.bilibili.com",
    "github.com",
]


def build_query(name, qtype=1):
    tid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    body = b""
    for label in name.split("."):
        body += bytes([len(label)]) + label.encode("ascii")
    body += b"\x00" + struct.pack(">HH", qtype, 1)
    return tid, header + body


def query(server, name, timeout):
    """返回 (rtt_ms, 状态)。状态正常时为 'ok'。"""
    tid, pkt = build_query(name)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        t0 = time.perf_counter()
        sock.sendto(pkt, (server, 53))
        data, _ = sock.recvfrom(4096)
        rtt = (time.perf_counter() - t0) * 1000.0
        if len(data) < 12:
            return None, "short"
        if struct.unpack(">H", data[:2])[0] != tid:
            return None, "tid"
        rcode = data[3] & 0x0F
        if rcode != 0:
            return None, "rcode%d" % rcode
        return rtt, "ok"
    except socket.timeout:
        return None, "timeout"
    except OSError as e:
        return None, type(e).__name__
    finally:
        sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=0.8)
    args = ap.parse_args()

    print("每台 DNS 对每个域名测 %d 轮，取最小值（最小值更能代表真实延迟）" % args.rounds)
    print("=" * 78)
    print("%-17s %-20s %8s %8s %8s" % ("地址", "名称", "最快ms", "平均ms", "可用"))
    print("-" * 78)

    rows = []
    for server, label in SERVERS:
        best = None
        samples = []
        ok = 0
        for domain in DOMAINS:
            for _ in range(args.rounds):
                rtt, status = query(server, domain, args.timeout)
                if rtt is not None:
                    samples.append(rtt)
                    ok += 1
                    if best is None or rtt < best:
                        best = rtt
        total = len(DOMAINS) * args.rounds
        if samples:
            rows.append((best, sum(samples) / len(samples), ok, total, server, label))
        else:
            rows.append((None, None, 0, total, server, label))

    for best, avg, ok, total, server, label in rows:
        if best is None:
            print("%-17s %-20s %8s %8s %6d/%d" % (server, label, "-", "-", ok, total))
        else:
            print("%-17s %-20s %8.1f %8.1f %6d/%d"
                  % (server, label, best, avg, ok, total))

    print("=" * 78)
    usable = [r for r in rows if r[0] is not None]
    if not usable:
        print("全部不可用，检查是不是拦了 UDP 53。")
        return 1
    usable.sort(key=lambda r: r[0])
    print("\n按最快值排名（前 4）：")
    for i, r in enumerate(usable[:4], 1):
        print("  %d. %-17s %-20s %.1f ms" % (i, r[4], r[5], r[0]))
    print("\n建议主 DNS 用第 1 名，备用用第 2 名，且两者要是不同厂商（同家挂了一起挂）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
