"""Scan the project for references to cleanup candidates (read-only)."""
import os

ROOT = r"E:\Test\racing-form-system"
SKIP_DIRS = {"archive", "__pycache__", ".git", "data", "reports", "price_log",
             ".continue", "launchers"}
SCAN_EXT = {".py", ".bat", ".md", ".ps1", ".cmd", ".json", ".txt"}

CANDIDATES = ["flat-handicaps", "historical-races", "excel_raw", "dump_cols",
              "gpt_coder", "may-aug26", "export_daily_card", "rdb_cols",
              "launchers", "scratch_", ".aider"]

# collect files to scan
scan = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fn in filenames:
        if os.path.splitext(fn)[1].lower() in SCAN_EXT:
            scan.append(os.path.join(dirpath, fn))

print(f"scanning {len(scan)} files (excluding archive/data/reports/price_log)\n")
for cand in CANDIDATES:
    hits = []
    for path in scan:
        if os.path.basename(path).startswith("scratch_"):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if cand.lower() in line.lower():
                        hits.append(f"{os.path.relpath(path, ROOT)}:{i}")
                        break
        except Exception:                                      # noqa: BLE001
            pass
    flag = "  <-- UNREFERENCED" if not hits else ""
    print(f"{cand:20s} {len(hits):3d} refs{flag}")
    for h in hits[:6]:
        print("      " + h)
