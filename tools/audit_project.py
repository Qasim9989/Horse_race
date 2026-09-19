"""
PROJECT AUDIT - what code and files are actually still used?
============================================================
Builds a reference graph from the live entry points (the .bat launchers, the
daily pipeline, and imports between scripts) and reports anything that is not
reachable from them.

  python tools/audit_project.py                 # report only
  python tools/audit_project.py --apply         # move unreferenced items to
                                                # archive/audit_<date>/
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import os
import re
import shutil
import sys

ROOT = r"E:\Test\racing-form-system"
ARCHIVE = os.path.join(ROOT, "archive", f"audit_{dt.date.today()}")

ENTRY_EXT = {".bat", ".cmd"}
CODE_DIRS = ("scripts", "tools", "tests")
KEEP_ALWAYS = {"dashboard.bat", "run_auto_daily_pipeline.bat",
               "run_book_odds.bat", "betfair_prices.bat", "finish_bsp.bat",
               "watch_prices.bat", "report.bat", "run_price_log.bat",
               "after_backfill.bat"}


def read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def collect():
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in ("archive", "__pycache__", ".git",
                                    ".continue", "node_modules", ".venv")]
        for fn in filenames:
            files.append(os.path.join(dirpath, fn))
    return files


# Files that are not wired into a launcher but must survive: they are either
# the evidence behind a published verdict (re-running them is how the numbers
# are checked) or a manual tool used from the console.
KEEP_EVIDENCE = {
    "bens_bookmaker_backtest.py": "produces the bookmaker-price ROI verdict",
    "bens_drop_pct.py": "produces the shorten<20% / >20% ledger split",
    "bens_exchange_move.py": "produces the exchange-vs-books drift numbers",
    "bens_odds_check.py": "proves recorded odds vs real BSP gap",
    "bens_odds_fake_check.py": "second check on the recorded-odds gap",
    "bens_forward.py": "builds the 1,759-bet settled ledger",
    "ben_report.py": "manual console ledger + today verdict",
    "pick_tracker.py": "manual: trace one pick's price across snapshots",
    "shorten_test.py": "produces the 829-bet shortened subset",
    "backfill_weights.py": "rebuilds weights (run by after_backfill.bat)",
    "backfill_weights_api.py": "fast 5-year weight rebuild (API, no browser)",
    "backfill_weights_chunked.py": "runs the API rebuild in bounded chunks",
    "add_results_index.py": "one-off: index that makes the weight writes fast",
    "rtv_api.py": "RacingTV JSON client (imported by the API rebuild)",
    "verify_weights.py": "checks weight coverage after a rebuild",
    "wait_for_backfill.py": "waits for the rebuild, then audits",
    "weight_progress.py": "manual: how many races still need weights",
    "selection_today.py": "five-rule picks (run by the daily pipeline)",
    "export_daily_card.py": "manual: export a day's card from PRODB",
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    files = collect()
    entry = [f for f in files if os.path.splitext(f)[1].lower() in ENTRY_EXT
             and os.path.dirname(f) == ROOT]
    code = [f for f in files
            if os.path.relpath(f, ROOT).split("\\")[0] in CODE_DIRS
            and f.endswith(".py")]

    # everything referenced by an entry point (path mentioned in the .bat)
    referenced = set()
    for e in entry:
        txt = read(e)
        for f in code:
            if os.path.basename(f) in txt:
                referenced.add(os.path.normcase(f))

    # transitive: this codebase wires scripts together with subprocess calls to
    # their filename, so any mention of a script's name inside a reachable file
    # counts as a reference (repeat until the set stops growing)
    for _ in range(6):
        before = len(referenced)
        for f in code:
            if os.path.normcase(f) not in referenced:
                continue
            txt = read(f)
            for other in code:
                if os.path.normcase(other) in referenced:
                    continue
                base = os.path.basename(other)
                if base in txt or re.search(
                        rf"\b(import|from)\s+{re.escape(base[:-3])}\b", txt):
                    referenced.add(os.path.normcase(other))
        if len(referenced) == before:
            break

    code_used = [f for f in code if os.path.normcase(f) in referenced]
    # only scripts/ is a candidate for removal: tools/ is the security+audit
    # kit and tests/ is the safety net, both stay whatever the graph says
    protected_dirs = ("tools", "tests")
    unreachable = sorted(
        f for f in set(code) - set(code_used)
        if os.path.relpath(f, ROOT).split("\\")[0] not in protected_dirs)
    kept = [f for f in unreachable if os.path.basename(f) in KEEP_EVIDENCE]
    code_unused = [f for f in unreachable
                   if os.path.basename(f) not in KEEP_EVIDENCE]

    # junk classes that never need to exist (never touch a file that is being
    # written right now by a running job, or one modified in the last 10 min)
    now = dt.datetime.now().timestamp()
    junk = []
    for f in files:
        base = os.path.basename(f)
        rel = os.path.relpath(f, ROOT)
        if base.startswith("_") and base.endswith((".log", ".err", ".txt")):
            junk_like = rel.startswith("reports\\") or rel.startswith("reports/")
        elif base.endswith((".pyc", ".pyo")) or "__pycache__" in rel or re.match(r"^scratch_", base) or base.endswith((".tmp", ".bak", "~")):
            junk_like = True
        else:
            junk_like = False
        if not junk_like:
            continue
        if base in ("_bf_weights.log", "_bf_weights.err"):
            continue                       # live backfill output
        try:
            if now - os.path.getmtime(f) < 600:
                continue                   # in use
        except OSError:
            continue
        junk.append(f)
    junk = sorted(set(junk))
    # keep the audit/report of the last run readable, it is the evidence
    junk = [f for f in junk if not os.path.basename(f).startswith("_bf_")]

    def total(paths):
        n = 0
        for p in paths:
            with contextlib.suppress(OSError):
                n += os.path.getsize(p)
        return n

    print(f"=== PROJECT AUDIT {dt.date.today()} ===")
    print(f"launchers (.bat)          : {len(entry)}")
    print(f"python files              : {len(code)}")
    print(f"  reachable from launchers: {len(code_used)}")
    print(f"  NOT reachable           : {len(code_unused)}")
    for f in code_unused:
        print(f"      {os.path.relpath(f, ROOT)}")
    print(f"\nkept (not wired in, but evidence or a manual tool): {len(kept)}")
    for f in kept:
        print(f"      {os.path.basename(f):<34} "
              f"{KEEP_EVIDENCE[os.path.basename(f)]}")
    print(f"\njunk (logs, scratch, bytecode): {len(junk)} files, "
          f"{total(junk) / 1024:.0f} KB")
    for f in junk[:25]:
        print(f"      {os.path.relpath(f, ROOT)}")
    if len(junk) > 25:
        print(f"      ... and {len(junk) - 25} more")

    if not a.apply:
        print("\nreport only. --apply moves the unreachable python files and "
              f"deletes the junk (archive: {os.path.relpath(ARCHIVE, ROOT)})")
        return 0

    os.makedirs(ARCHIVE, exist_ok=True)
    moved = []
    for f in code_unused:
        dest = os.path.join(ARCHIVE, "unused_code", os.path.basename(f))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.exists(dest):
            shutil.move(f, dest)
            moved.append((os.path.relpath(f, ROOT),
                          os.path.relpath(dest, ROOT)))
    removed = 0
    for f in junk:
        try:
            os.remove(f)
            removed += 1
        except OSError:
            pass
    man = os.path.join(ARCHIVE, "MANIFEST.csv")
    with open(man, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["moved_from", "moved_to"])
        w.writerows(moved)
    print(f"\nmoved {len(moved)} unreferenced script(s) to "
          f"{os.path.relpath(ARCHIVE, ROOT)}")
    print(f"deleted {removed} junk file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
