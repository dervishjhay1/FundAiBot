#!/usr/bin/env python3
"""Push fundzaibot/ directory to GitHub via REST API (no git required)."""
import os, sys, json, base64, urllib.request, urllib.error
from pathlib import Path

TOKEN   = os.environ["GITHUB_TOKEN"]
OWNER   = "dervishjhay1"
REPO    = "FundAiBot"
BRANCH  = "main"
BASE    = f"https://api.github.com/repos/{OWNER}/{REPO}"
SRC_DIR = Path(__file__).parent  # fundzaibot/

SKIP = {".git", "__pycache__", ".pyc", ".pyo", "push_to_github.py", "push_to_github.sh"}

def api(method, path, body=None):
    url = BASE + path if path.startswith("/") else path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "FundzAiBot-Pusher",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  HTTP {e.code} {method} {path}: {body[:200]}")
        raise

def collect_files():
    files = []
    for p in sorted(SRC_DIR.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(SRC_DIR)
        parts = rel.parts
        if any(s in parts or str(rel).endswith(s) for s in SKIP):
            continue
        if any(part.startswith(".") for part in parts):
            continue
        files.append((str(rel).replace("\\", "/"), p))
    return files

def create_blob(content_bytes):
    return api("POST", "/git/blobs", {
        "content": base64.b64encode(content_bytes).decode(),
        "encoding": "base64"
    })["sha"]

def main():
    print(f"Pushing {SRC_DIR} → github.com/{OWNER}/{REPO} ({BRANCH})")

    # Get current branch tip (create if doesn't exist)
    try:
        ref_data = api("GET", f"/git/ref/heads/{BRANCH}")
        base_commit_sha = ref_data["object"]["sha"]
        print(f"  Current HEAD: {base_commit_sha[:8]}")
        commit_data = api("GET", f"/git/commits/{base_commit_sha}")
        base_tree_sha = commit_data["tree"]["sha"]
    except urllib.error.HTTPError:
        base_commit_sha = None
        base_tree_sha   = None
        print("  Branch does not exist — will create fresh")

    # Collect files
    files = collect_files()
    print(f"  Uploading {len(files)} files as blobs…")
    tree_items = []
    for i, (rel_path, abs_path) in enumerate(files, 1):
        try:
            content = abs_path.read_bytes()
        except Exception as e:
            print(f"    SKIP {rel_path}: {e}")
            continue
        sha = create_blob(content)
        tree_items.append({"path": rel_path, "mode": "100644", "type": "blob", "sha": sha})
        if i % 10 == 0:
            print(f"    {i}/{len(files)} blobs uploaded…")
    print(f"  All {len(tree_items)} blobs uploaded.")

    # Create tree
    tree_body = {"tree": tree_items}
    if base_tree_sha:
        tree_body["base_tree"] = base_tree_sha
    new_tree_sha = api("POST", "/git/trees", tree_body)["sha"]
    print(f"  New tree: {new_tree_sha[:8]}")

    # Create commit
    commit_body = {
        "message": "feat: v2.6.0 — enterprise /testaudit audit center + group integration\n\n"
                   "- services/audit_service.py: 12-section diagnostic engine\n"
                   "- handlers/audit.py: interactive /testaudit inline dashboard\n"
                   "- handlers/group.py: welcome, /ai groups, @mention, anti-spam\n"
                   "- main.py: register all new handlers; /ai restricted to groups only\n"
                   "- utils/keyboards.py: Audit Center button in admin panel",
        "tree": new_tree_sha,
    }
    if base_commit_sha:
        commit_body["parents"] = [base_commit_sha]
    new_commit_sha = api("POST", "/git/commits", commit_body)["sha"]
    print(f"  New commit: {new_commit_sha[:8]}")

    # Force-update (or create) the branch ref
    if base_commit_sha:
        api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": new_commit_sha, "force": True})
        print(f"  Branch '{BRANCH}' force-updated ✓")
    else:
        api("POST", "/git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": new_commit_sha})
        print(f"  Branch '{BRANCH}' created ✓")

    print(f"\n✅ Done! https://github.com/{OWNER}/{REPO}/commits/{BRANCH}")

if __name__ == "__main__":
    main()
