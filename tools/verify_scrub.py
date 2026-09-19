"""Verify: how many of the known files still contain the key/password?"""
import json
import os

import scratch_scrub_fast as S

vals = S.load_values()
paths = S.target_paths()
still, clean, missing = [], 0, 0
for p in paths:
    if not os.path.isfile(p):
        missing += 1
        continue
    if os.path.normcase(os.path.abspath(p)) == next(iter(S.KEEP)):
        print(f"KEPT BY DESIGN (source config): {p}")
        continue
    try:
        text = S.read_text(p)
    except Exception:
        continue
    n = sum(text.count(v) for v in vals.values())
    if n:
        size = os.path.getsize(p)
        still.append((p, n, size))
    else:
        clean += 1

print(f"\ncandidate files : {len(paths)}")
print(f"now clean       : {clean}")
print(f"still contain it: {len(still)}")
print(f"no longer exist : {missing}")
for p, n, size in sorted(still, key=lambda x: -x[1])[:25]:
    print(f"   {n:4d}x  {size:>9,}b  {p}")

# sanity: the source config must still hold the real values
cfg = json.load(open(S.CONFIG, encoding="utf-8"))
print("\nsource config intact:",
      {k: f"len {len(str(v))}" for k, v in cfg.items()})
