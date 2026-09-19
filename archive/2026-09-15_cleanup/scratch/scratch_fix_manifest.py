"""Rebuild the cleanup MANIFEST so it lists EVERY file that moved.

The first (interrupted) run moved items before the manifest was written, so
this walks archive/2026-09-15_cleanup/, works out each item's original path
from its archive subfolder, and appends anything missing.

Run from anywhere:  python archive\\2026-09-15_cleanup\\scratch\\scratch_fix_manifest.py
"""
import csv
import os

ROOT = r"E:\Test\racing-form-system"
ARCH = os.path.join(ROOT, "archive", "2026-09-15_cleanup")
MAN = os.path.join(ARCH, "MANIFEST.csv")

# archive subfolder -> where the item came from
MAP = {
    "scratch": "",
    "probe_logs": "reports",
    "root_legacy": "",
    "data_generated": "data",
    "launchers": "",
}

existing = []
if os.path.exists(MAN):
    with open(MAN, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    existing = rows[1:]
have_src = {r[0] for r in existing if r}

added = []
for sub, origin in MAP.items():
    base = os.path.join(ARCH, sub)
    if not os.path.isdir(base):
        continue
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = os.path.relpath(dirpath, base)
        # a moved directory is listed once, by its top-level name
        for name in list(dirnames):
            if rel_dir == ".":
                src = os.path.join(origin, name) if origin else name
                if src.replace("/", "\\") not in have_src:
                    added.append([src, os.path.relpath(os.path.join(dirpath,
                                                                   name), ROOT),
                                  0])
        for name in filenames:
            src = os.path.join(origin, name) if origin else name
            if rel_dir != ".":
                src = os.path.join(os.path.dirname(src), rel_dir, name)
            src = os.path.normpath(src)
            if src not in have_src:
                size = os.path.getsize(os.path.join(dirpath, name))
                added.append([src, os.path.relpath(os.path.join(dirpath, name),
                                                   ROOT), size])

with open(MAN, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["moved_from", "moved_to", "bytes"])
    w.writerows(existing + added)

total = len(existing) + len(added)
size = sum(int(r[2]) for r in existing + added if r and r[2])
print(f"manifest rows: {total}  (added {len(added)} that were missing)")
print(f"total size moved: {size / 1024 / 1024:.1f} MB")

# sanity check: the original paths should no longer exist
still_there = [r[0] for r in existing + added
               if r and os.path.exists(os.path.join(ROOT, r[0]))
               and os.path.abspath(os.path.join(ROOT, r[0]))
               != os.path.join(ROOT, "archive")]
print(f"originals still present (should be 0): {len(still_there)}")
for s in still_there[:10]:
    print("   " + s)
