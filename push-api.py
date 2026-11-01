#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""push-api.py — 走 GitHub REST API 提交，绕开 git 直连 github.com:443 被重置的问题。

本机网络环境下 github.com:443 时通时不通，但 api.github.com 一直可达。
git push 失败时用这个顶上：

    python push-api.py                      # 提交工作区所有改动
    python push-api.py build.py README.md   # 只提交指定文件
    python push-api.py --message "..." ...

它做的事和 git commit + push 等价：blob → tree → commit → 更新 ref，
提交历史正常，手机端 raw 地址照常生效。

token 从 .git-token 读，或者 GIST_TOKEN / GH_TOKEN 环境变量。
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
API = "https://api.github.com"
REPO = "suyanasg/ios-adblock"
BRANCH = "main"

# 这些不进仓库（跟 .gitignore 保持一致）
SKIP_DIRS = {".git", "__pycache__", ".cache", "logs", "node_modules"}
SKIP_FILES = {".git-token", ".git-token.old"}


def token():
    for env in ("GH_TOKEN", "GIST_TOKEN"):
        v = os.environ.get(env, "").strip()
        if v:
            return v
    p = os.path.join(ROOT, ".git-token")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    raise SystemExit("[FAIL] 没找到 token：q")


def call(method, path, data=None, tok=None):
    url = path if path.startswith("http") else API + path
    body = None
    headers = {
        "Authorization": "token " + tok,
        "User-Agent": "ios-adblock-push",
        "Accept": "application/vnd.github+json",
    }
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit("[FAIL] %s %s -> HTTP %s\n%s" % (method, path, e.code, detail[:600]))


def walk_files():
    """仓库里应该被跟踪的文件（简化版 git ls-files）"""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            if rel.startswith(".git/"):
                continue
            out.append(rel)
    return sorted(out)


def main():
    args = [a for a in sys.argv[1:]]
    message = None
    if "--message" in args:
        i = args.index("--message")
        message = args[i + 1]
        del args[i:i + 2]
    targets = args or None

    tok = token()
    origin = "https://x-access-token:%s@github.com/%s.git" % (tok, REPO)

    print("[1/5] 读远端 HEAD")
    _, ref = call("GET", "/repos/%s/git/ref/heads/%s" % (REPO, BRANCH), tok=tok)
    base_commit = ref["object"]["sha"]
    _, commit = call("GET", "/repos/%s/git/commits/%s" % (REPO, base_commit), tok=tok)
    base_tree = commit["tree"]["sha"]
    print("      base = %s  (%s)" % (base_commit[:7], commit["message"].splitlines()[0][:50]))

    print("[2/5] 取远端现有文件列表")
    _, tree = call("GET", "/repos/%s/git/trees/%s?recursive=1" % (REPO, base_tree), tok=tok)
    remote = {e["path"]: e["sha"] for e in tree.get("tree", []) if e["type"] == "blob"}

    files = targets or walk_files()
    changed, deleted = [], []

    print("[3/5] 上传 blob")
    for rel in files:
        full = os.path.join(ROOT, rel)
        if not os.path.exists(full):
            if rel in remote:
                deleted.append(rel)
            continue
        with open(full, "rb") as f:
            data = f.read()
        local_sha = _git_blob_sha(data)
        if remote.get(rel) == local_sha:
            continue
        _, blob = call("POST", "/repos/%s/git/blobs" % REPO,
                       {"content": base64.b64encode(data).decode("ascii"),
                        "encoding": "base64"}, tok=tok)
        changed.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print("      + %-36s %5.1f KB" % (rel, len(data) / 1024.0))

    for rel in deleted:
        print("      - %s（远端有、本地没有，不动它，避免误删）" % rel)
        deleted.remove(rel)

    if not changed:
        print()
        print("没有变化，不提交。")
        return 0

    print("[4/5] 建 tree + commit")
    _, new_tree = call("POST", "/repos/%s/git/trees" % REPO,
                       {"base_tree": base_tree, "tree": changed}, tok=tok)
    msg = message or "chore: 更新 %d 个文件（push-api）" % len(changed)
    _, new_commit = call("POST", "/repos/%s/git/commits" % REPO,
                         {"message": msg + "\n\n[via api]",
                          "tree": new_tree["sha"], "parents": [base_commit]}, tok=tok)

    print("[5/5] 更新 ref")
    call("PATCH", "/repos/%s/git/refs/heads/%s" % (REPO, BRANCH),
         {"sha": new_commit["sha"], "force": False}, tok=tok)

    print()
    print("已推送 %s" % new_commit["sha"][:7])
    print("  %s" % (msg.splitlines()[0]))
    print()
    print("本机 git 历史已落后，等网络恢复后执行：")
    print("  git fetch %s %s && git reset --hard FETCH_HEAD" % (origin, BRANCH))
    return 0


def _git_blob_sha(data):
    """本地算 git blob sha，跟远端 tree 里的比对，避免重复上传"""
    import hashlib
    h = hashlib.sha1()
    h.update(b"blob " + str(len(data)).encode() + b"\0")
    h.update(data)
    return h.hexdigest()


if __name__ == "__main__":
    sys.exit(main())
