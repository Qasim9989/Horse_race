"""
WAIT FOR THE WEIGHT BACKFILL, THEN RUN THE AUDIT CHAIN
======================================================
Started by after_backfill.bat.  Detects completion from the backfill's own log
rather than from the process table: `wmic`/`Get-CimInstance -Filter` quoting is
fragile inside a .bat (it silently reported "not running" while the job was
running) and a log line cannot be mis-quoted.

Completion is either
  * a "DONE <range>" line in reports\backfill_weights.log, or
  * no new log line for 20 minutes (the job died)

Then: verify weights -> database audit -> scrap dead tables -> project audit
      -> tests, writing reports\\_after_backfill_done.txt at the end.

  python scripts/wait_for_backfill.py                 # wait then run everything
  python scripts/wait_for_backfill.py --dry-run       # just report the decision
  python scripts/wait_for_backfill.py --max-hours 16
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "reports", "backfill_weights.log")
STAMP = os.path.join(ROOT, "reports", "_after_backfill_done.txt")
STALE_MIN = 20

STEPS = [
    ("verify weight coverage", [r"scripts\verify_weights.py"]),
    ("database audit", [r"tools\audit_db.py", "--apply"]),
    ("scrap dead tables", [r"tools\scrap_dead_tables.py", "--apply"]),
    ("project audit", [r"tools\audit_project.py", "--apply"]),
    ("tests: book odds", [r"tests\test_book_odds.py"]),
    ("tests: dashboard", [r"tests\test_dashboard.py"]),
    ("tests: bsp files", [r"tests\check_bsp_files.py"]),
]


def pending_left():
    """Races still without weights, across both databases."""
    import pyodbc
    q = """
        SELECT COUNT(*) FROM (
            SELECT RaceDate, RaceTime, CourseName
            FROM dbo.Scraped_Results
            WHERE RaceDate BETWEEN '2021-01-01' AND '2026-09-14'
            GROUP BY RaceDate, RaceTime, CourseName
            HAVING COUNT(Weight) < COUNT(*)
                OR COUNT(DistanceYards) < COUNT(*)) x
    """
    total = 0
    for db in ("RACINGTV_2023_2026", "SCRAPED_PRODB"):
        try:
            c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                               "Server=(localdb)\\MSSQLLocalDB;Database=" + db +
                               ";Trusted_Connection=yes;Connection Timeout=60;")
            cur = c.cursor()
            cur.execute(q)
            total += cur.fetchone()[0]
            c.close()
        except Exception as e:
            print(f"  ! pending check {db}: {str(e)[:60]}", flush=True)
    return total


def status():
    """(finished, reason, age_minutes)

    Only a DONE line belonging to the CURRENT run counts.  The log is
    appended across runs, so the first version happily saw the 14:04 DONE
    from an earlier 2026-08-19 run and declared a 15-hour job finished.
    Each run starts with a "=== backfill weights" header, so completion is a
    DONE in the segment after the LAST header.
    """
    if not os.path.exists(LOG):
        return False, "log not found yet", 0.0
    age = (time.time() - os.path.getmtime(LOG)) / 60.0
    with open(LOG, encoding="utf-8", errors="ignore") as fh:
        text = fh.read()

    # The current run is whatever follows the LAST run header.  A chunked run
    # spawns child processes whose headers and DONEs appear after its own, so
    # while a chunked sweep is in progress only its final DONE counts -
    # otherwise the watcher fires the audit chain during the mop-up passes
    # (that is how it once archived the new backfill scripts out from under
    # the running job).
    if "=== chunked backfill" in text:
        tail = text.rsplit("=== chunked backfill", 1)[-1]
        done = [ln for ln in tail.splitlines()
                if ln.split("  ", 1)[-1].startswith("DONE chunked backfill")]
    else:
        markers = ("=== API backfill", "=== backfill weights")
        pos = max((text.rfind(m) for m in markers if m in text), default=-1)
        current_run = text[pos:] if pos >= 0 else text
        done = [ln for ln in current_run.splitlines() if "DONE " in ln]

    if done:
        return True, f"finished: {done[-1].strip()}", age
    if age > STALE_MIN:
        return True, f"no progress for {age:.0f} min - assuming it stopped", age
    return False, "running", age


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-hours", type=float, default=16.0)
    ap.add_argument("--every", type=int, default=60, help="seconds between checks")
    ap.add_argument("--until-empty", action="store_true",
                    help="wait until no race is left without weights, however "
                         "many workers are running (two workers share one log, "
                         "so log-based detection can fire early)")
    a = ap.parse_args()

    deadline = time.time() + a.max_hours * 3600
    last_left = None
    flat = 0
    while True:
        if a.until_empty:
            left = pending_left()
            if last_left is not None and left >= last_left:
                flat += 1
            else:
                flat = 0
            last_left = left
            # a handful of rows can never be filled (races whose names do not
            # exist in the API at all), so waiting for a literal zero would
            # hang forever: a plateau means the job is as done as it can get
            finished = left == 0 or flat >= 2
            reason = (f"{left:,} races left"
                      + (" (plateau - treating as finished)" if flat >= 2
                         else ""))
            age = 0.0
        else:
            finished, reason, age = status()
        stamp = f"{dt.datetime.now():%H:%M:%S}"
        if finished:
            print(f"{stamp}  {reason}")
            break
        print(f"{stamp}  backfill {reason} (log last written {age:.0f} min ago)",
              flush=True)
        if time.time() > deadline:
            print(f"{stamp}  gave up after {a.max_hours}h")
            return 2
        if a.dry_run:
            return 0
        time.sleep(a.every)

    if a.dry_run:
        return 0

    results = []
    for name, argv in STEPS:
        print(f"\n{'=' * 70}\n  {name}\n{'=' * 70}", flush=True)
        try:
            rc = subprocess.call([sys.executable, "-u",
                                  os.path.join(ROOT, argv[0]), *argv[1:]],
                                 cwd=ROOT)
        except Exception as e:
            print(f"  failed to run: {e}")
            rc = -1
        results.append((name, rc))

    with open(STAMP, "w", encoding="utf-8") as fh:
        fh.write(f"finished {dt.datetime.now():%Y-%m-%d %H:%M}\n\n")
        for name, rc in results:
            fh.write(f"{'OK  ' if rc == 0 else 'FAIL'} {name} (exit {rc})\n")
    print(f"\n{'=' * 70}\n  ALL DONE - see {os.path.relpath(STAMP, ROOT)}")
    for name, rc in results:
        print(f"   {'OK  ' if rc == 0 else 'FAIL'} {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
