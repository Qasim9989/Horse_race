"""Hunt for a stored Betfair app key / session token on this machine.

Read-only. Prints file paths and MASKED matches (first 4 + length + last 4) so
nothing secret ends up in a log.

  python scratch_find_betfair_key.py
"""
import os
import re

OUT = r"E:\Test\racing-form-system\reports\betfair_key_hunt.txt"

ROOTS = [r"E:\Test", "E:\\", r"C:\Users\qasim", "D:\\"]

SKIP_DIRS = {"node_modules", "site-packages", "__pycache__", ".git", "dist",
             "build", "Cache", "Code Cache", "GPUCache", "Service Worker",
             "CacheStorage", "Temp", "tmp", ".venv", "venv", "env",
             "Windows", "Program Files", "Program Files (x86)", "AppData\\Local\\Microsoft",
             "AppData\\Local\\Packages", "$Recycle.Bin", "System Volume Information",
             "price_log", "reports"}

EXTS = {".py", ".txt", ".json", ".md", ".bat", ".cmd", ".ps1", ".env", ".cfg",
        ".ini", ".yaml", ".yml", ".log", ".csv", ".js", ".ts", ".html", ".xml"}

# phrases that appear near a key/token in real configs and notes
KEYS = [
    r"app[_\-\s]?key", r"appkey", r"application[_\-\s]?key", r"x-application",
    r"betfair[_\-\s]?key", r"delayed[_\-\s]?key", r"activation[_\-\s]?key",
    r"session[_\-\s]?token", r"ssoid", r"sessiontoken", r"betfair",
    r"developer\.betfair", r"api-ng", r"identitysso",
]

# a plausible Betfair app key: 12+ chars of letters/digits, mixed case
KEYLIKE = re.compile(r"\b(?=[A-Za-z0-9]{12,70}\b)(?=[^\s]*[A-Z])"
                     r"(?=[^\s]*[a-z])(?=[^\s]*[0-9])[A-Za-z0-9]+\b")


def mask(s):
    s = s.strip()
    return f"{s[:4]}...{s[-4:]} (len {len(s)})" if len(s) > 12 else "(short)"


def scan_file(path):
    hits = []
    try:
        if os.path.getsize(path) > 4_000_000:
            return hits
        with open(path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                low = line.lower()
                if not any(re.search(k, low) for k in KEYS):
                    continue
                if any(t in low for t in ("betfair.com/betfairsp",
                                          "promo.betfair", "import", "#",
                                          "help", "docs", "readme")):
                    continue
                cands = [m.group(0) for m in KEYLIKE.finditer(line)
                         if not m.group(0).isdigit()]
                if cands:
                    hits.append((i, mask(line.strip()[:80]),
                                 [mask(c) for c in cands[:3]]))
    except Exception:                                          # noqa: BLE001
        pass
    return hits


def main():
    results, scanned, skipped = [], 0, 0
    seen = set()
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if any(s.lower() in dirpath.lower() for s in SKIP_DIRS):
                dirnames[:] = []
                skipped += 1
                continue
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in {s.lower() for s in SKIP_DIRS}]
            # filename-level hits are interesting even if content is binary
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                if p in seen:
                    continue
                seen.add(p)
                low = fn.lower()
                if any(t in low for t in ("betfair", "appkey", "app_key",
                                          "session", ".env")):
                    results.append((p, "FILENAME", [(0, fn, [])]))
                    continue
                if os.path.splitext(fn)[1].lower() not in EXTS:
                    continue
                scanned += 1
                h = scan_file(p)
                if h:
                    results.append((p, "CONTENT", h[:4]))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"scanned {scanned} files, skipped {skipped} dirs\n")
        f.write(f"{len(results)} files with a possible hit\n\n")
        for path, kind, hits in results:
            f.write(f"[{kind}] {path}\n")
            for ln, text, cands in hits:
                f.write(f"      line {ln}: {text}\n")
                if cands:
                    f.write(f"         candidates: {', '.join(cands)}\n")
    print(f"scanned={scanned} skipped_dirs={skipped} files_with_hits="
          f"{len(results)}")
    print("report:", OUT)


main()
