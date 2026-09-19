"""Fast, time-bounded hunt for a Betfair app key / session token.

Only looks where such a string realistically lives: your project folders and
the chat/config histories of the coding tools.  Masks every match.

  python scratch_find_betfair_key2.py
Writes reports/betfair_key_hunt.txt
"""
import os
import re
import time

OUT = r"E:\Test\racing-form-system\reports\betfair_key_hunt.txt"
BUDGET = 150.0            # seconds
MAX_FILES = 120000

HOME = r"C:\Users\qasim"
ROOTS = [
    (r"E:\Test", 10),
    (os.path.join(HOME, ".aider"), 6),
    (os.path.join(HOME, ".cline"), 8),
    (os.path.join(HOME, ".continue"), 6),
    (os.path.join(HOME, ".claude"), 6),
    (os.path.join(HOME, ".codex"), 6),
    (os.path.join(HOME, ".cursor"), 6),
    (os.path.join(HOME, ".gemini"), 8),
    (os.path.join(HOME, ".windsurf"), 6),
    (os.path.join(HOME, ".antigravity-ide"), 8),
    (os.path.join(HOME, ".opencode"), 6),
    (os.path.join(HOME, ".config"), 6),
    (os.path.join(HOME, ".streamlit"), 3),
    (os.path.join(HOME, "Documents"), 5),
    (os.path.join(HOME, "Downloads"), 4),
    (os.path.join(HOME, "Desktop"), 4),
]

SKIP = {"node_modules", "site-packages", "__pycache__", ".git", ".venv", "venv",
        "Cache", "CacheStorage", "Code Cache", "GPUCache", "Service Worker",
        "dist", "build", ".next", "target", "bin", "obj"}

EXTS = {".py", ".txt", ".json", ".md", ".bat", ".cmd", ".ps1", ".env", ".cfg",
        ".ini", ".yaml", ".yml", ".log", ".js", ".ts", ".html", ".xml", ".csv",
        ".toml", ".jsonl", ".sql"}

NAME_HINT = ("betfair", "appkey", "app_key", "app-key", "session", ".env",
             "apikey", "api_key", "x-application")

# words that mean the line is probably about a key
KEYS = [re.compile(p, re.I) for p in (
    r"app[\s_\-]?key", r"appkey", r"api[\s_\-]?key", r"x-application",
    r"betfair[\s_\-]?(app|key|login)", r"delayed[\s_\-]?key",
    r"activation[\s_\-]?key", r"session[\s_\-]?token", r"ssoid",
    r"betfairlightweight", r"developer\.betfair")]

# app keys are mixed-case alnum 12-70; session tokens are long base64
APPKEY = re.compile(r"\b(?=[A-Za-z0-9]{12,70}\b)(?=[^\s]*[A-Z])"
                    r"(?=[^\s]*[a-z])(?=[^\s]*\d)[A-Za-z0-9]+\b")
TOKEN = re.compile(r"\b[A-Za-z0-9+/]{120,600}={0,2}\b")


def mask(s):
    s = s.strip()
    if len(s) < 10:
        return "(too short)"
    return f"{s[:4]}\u2026{s[-4:]}  len={len(s)}"


def interesting_file(name):
    low = name.lower()
    return any(t in low for t in NAME_HINT)


def scan(path):
    """Return [(lineno, snippet, [masked candidates])] for one file."""
    hits = []
    try:
        if os.path.getsize(path) > 3_000_000:
            return hits
        with open(path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                if not any(rx.search(line) for rx in KEYS):
                    continue
                low = line.lower()
                if any(t in low for t in ("promo.betfair", "betfairsp/prices",
                                          "import ", "docs.", "http:",
                                          "https:")):
                    continue                       # our own code / documentation
                cands = [m.group(0) for m in APPKEY.finditer(line)
                         if not m.group(0).isdigit()]
                cands += [m.group(0) for m in TOKEN.finditer(line)]
                hits.append((i, line.strip()[:100].replace("\n", " "),
                             [mask(c) for c in cands[:4]]))
                if len(hits) >= 4:
                    break
    except Exception:                                          # noqa: BLE001
        pass
    return hits


def main():
    t0 = time.time()
    files = 0
    named, content = [], []
    for root, depth in ROOTS:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\").count("\\")
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() - t0 > BUDGET or files > MAX_FILES:
                break
            if dirpath.count("\\") - base_depth > depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP and not d.startswith("$")]
            for fn in filenames:
                files += 1
                p = os.path.join(dirpath, fn)
                if interesting_file(fn):
                    try:
                        named.append((p, os.path.getsize(p)))
                    except OSError:
                        pass
                if os.path.splitext(fn)[1].lower() in EXTS:
                    h = scan(p)
                    if h:
                        content.append((p, h))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"files scanned: {files}   elapsed: {time.time()-t0:.0f}s\n")
        f.write(f"TIME BUDGET HIT: {time.time()-t0 > BUDGET}\n\n")
        f.write("=== FILES WHOSE NAME MENTIONS BETFAIR/KEY/SESSION/ENV ===\n")
        for p, sz in named:
            f.write(f"  {p}  ({sz} bytes)\n")
        f.write(f"\n=== FILES WITH KEY-LIKE CONTENT ({len(content)}) ===\n")
        for p, hits in content:
            f.write(f"\n{p}\n")
            for ln, text, cands in hits:
                f.write(f"   line {ln}: {text}\n")
                if cands:
                    f.write(f"      -> {', '.join(cands)}\n")
    print(f"files={files} named_hits={len(named)} content_hits={len(content)} "
          f"elapsed={time.time()-t0:.0f}s")
    print("report:", OUT)


main()
