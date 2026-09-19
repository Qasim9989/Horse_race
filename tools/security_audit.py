"""Exposure audit for the Betfair credentials - READ ONLY, MASKED OUTPUT.

Reads the secret values from the config itself (never hardcoded here) and
reports where copies live and whether those places can reach the internet
(git remotes, cloud-sync folders, AI chat histories).  Never prints a secret in
full.  Writes reports/security_audit.txt
"""
import json
import os
import re
import subprocess
import time
from typing import Any

HOME = r"C:\Users\qasim"
CONFIG = r"E:\CGMBET\betfair_api_config.json"
OUT = r"E:\Test\racing-form-system\reports\security_audit.txt"

SKIP = {"node_modules", "site-packages", "__pycache__", ".git", ".venv", "venv",
        "Cache", "CacheStorage", "Code Cache", "GPUCache", "$Recycle.Bin"}
EXTS = {".py", ".txt", ".json", ".md", ".bat", ".cmd", ".ps1", ".env", ".cfg",
        ".ini", ".yaml", ".yml", ".log", ".jsonl", ".toml", ".csv", ".html"}

SCAN_ROOTS = [
    (r"E:\CGMBET", 10, "project (config lives here)"),
    (r"E:\Test", 10, "project"),
    (os.path.join(HOME, ".cline"), 8, "AI chat history"),
    (os.path.join(HOME, ".continue"), 6, "AI chat history"),
    (os.path.join(HOME, ".aider"), 6, "AI chat history"),
    (os.path.join(HOME, ".claude"), 6, "AI chat history"),
    (os.path.join(HOME, ".codex"), 6, "AI chat history"),
    (os.path.join(HOME, ".cursor"), 6, "AI chat history"),
    (os.path.join(HOME, ".gemini"), 8, "AI chat history"),
    (os.path.join(HOME, ".windsurf"), 6, "AI chat history"),
    (os.path.join(HOME, ".antigravity-ide"), 8, "AI chat history"),
    (os.path.join(HOME, ".opencode"), 6, "AI chat history"),
    (os.path.join(HOME, ".config"), 6, "app config"),
    (os.path.join(HOME, "Documents"), 5, "documents"),
    (os.path.join(HOME, "Downloads"), 4, "downloads"),
    (os.path.join(HOME, "Desktop"), 4, "desktop"),
    (os.path.join(HOME, "AppData", "Roaming"), 6, "roaming appdata"),
    (os.path.join(HOME, "AppData", "Local"), 4, "local appdata"),
]


def mask(v):
    v = str(v)
    return f"{v[:3]}\u2026{v[-3:]} (len {len(v)})" if len(v) > 8 else "(short)"


def load_secrets():
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    secrets = {k: v for k, v in cfg.items()
               if isinstance(v, str) and len(v) >= 8 and k.lower() != "username"}
    return cfg, secrets


def git_info(repo):
    info: dict[str, Any] = {}
    try:
        info["remote"] = subprocess.run(
            ["git", "-C", repo, "remote", "-v"], capture_output=True,
            text=True, timeout=20).stdout.strip() or "(no remote)"
    except Exception as e:
        info["remote"] = f"error {e}"
    try:
        tracked = subprocess.run(["git", "-C", repo, "ls-files"],
                                 capture_output=True, text=True,
                                 timeout=60).stdout.splitlines()
        info["n_tracked"] = len(tracked)
        info["tracked_secrets"] = [t for t in tracked
                                   if re.search(r"(config|cred|secret|key|env)",
                                                t, re.IGNORECASE)][:25]
    except Exception as e:
        info["n_tracked"] = -1
        info["tracked_secrets"] = [f"error {e}"]
    return info



def main():
    t0 = time.time()
    cfg, secrets = load_secrets()
    L = []
    L.append("BETFAIR CREDENTIAL EXPOSURE AUDIT   " +
             time.strftime("%Y-%m-%d %H:%M"))
    L.append("=" * 68)
    L.append(f"config file: {CONFIG}")
    L.append("fields: " + ", ".join(
        f"{k}={mask(v) if isinstance(v, str) and len(v) > 8 else v}"
        for k, v in cfg.items()))
    L.append("")

    L.append("--- 1. GIT / CLOUD EXPOSURE ---")
    for repo_root in (r"E:\CGMBET", r"E:\Test\racing-form-system"):
        if os.path.isdir(os.path.join(repo_root, ".git")):
            gi = git_info(repo_root)
            L.append(f"  git repo: {repo_root}")
            L.append("    remote: " + gi["remote"].replace("\n", " | "))
            L.append(f"    tracked files: {gi['n_tracked']}")
            L.append(f"    tracked config/key-ish: {gi['tracked_secrets']}")
        else:
            L.append(f"  {repo_root}: NOT a git repo (cannot be pushed)")
    cloud = [p for p in (os.path.join(HOME, "OneDrive"),
                         os.path.join(HOME, "Dropbox"),
                         os.path.join(HOME, "Google Drive"),
                         os.path.join(HOME, "iCloudDrive"))
             if os.path.isdir(p)]
    L.append(f"  cloud-sync folders present: {cloud or 'none'}")
    for p in cloud:
        L.append(f"    config inside {os.path.basename(p)}? "
                 f"{CONFIG.lower().startswith(p.lower())}")
    L.append("")

    L.append("--- 2. COPIES OF THE SECRET VALUES ELSEWHERE ---")
    files_scanned = hits = 0
    for root, depth, why in SCAN_ROOTS:
        if not os.path.isdir(root):
            continue
        base = root.rstrip("\\").count("\\")
        block = []
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() - t0 > 200:
                block.append("    (time budget reached - partial scan)")
                break
            if dirpath.count("\\") - base > depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in SKIP]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                if os.path.splitext(fn)[1].lower() not in EXTS:
                    continue
                try:
                    if os.path.getsize(p) > 5_000_000:
                        continue
                    files_scanned += 1
                    with open(p, encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            for k, v in secrets.items():
                                if v in line:
                                    hits += 1
                                    block.append(f"    {p}  line {i}  [{k}]")
                                    break
                except Exception:
                    pass
        if block:
            L.append(f"  [{why}] {root}")
            L.extend(sorted(set(block))[:40])
            L.append("")

    L.append(f"  files scanned: {files_scanned}   total hits: {hits}")
    L.append("")
    L.append("--- 3. WHAT THIS MEANS ---")
    L.append("  * .cline/.continue/.claude/.codex/.cursor/.gemini/.windsurf/"
             ".antigravity-ide are")
    L.append("    AI agent/chat histories - those logs go to model providers,"
             " so a key in")
    L.append("    them should be treated as disclosed and rotated.")
    L.append("  * A git remote means a value in a tracked file can be pushed.")
    L.append("  * No full secret appears anywhere in this report.")
    txt = "\n".join(L)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)


main()
