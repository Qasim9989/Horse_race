"""
CHUNKED 5-YEAR WEIGHT BACKFILL
==============================
The API backfill is fast (700+ races/min) but a long-lived process wedges
after a few hundred races: requests stop completing, no error is raised, and
nothing more is written.  A fresh process is served instantly, so the fix is
structural - run the range in bounded chunks, each its own process, each under
a hard timeout, and keep going when one wedges.  The API backfill is
resumable at race level, so a killed chunk costs nothing but its retry.

  python scripts/backfill_weights_chunked.py --from 2021-01-01 --to 2026-09-14
  python scripts/backfill_weights_chunked.py --passes 3 --chunk-days 10

Ends with a "DONE chunked ..." line, which is what after_backfill.bat waits for.
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
WORKER = os.path.join(ROOT, "scripts", "backfill_weights_api.py")


def log(msg):
    line = f"{dt.datetime.now():%H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def daterange(a, b, step):
    d = dt.date.fromisoformat(a)
    end = dt.date.fromisoformat(b)
    while d <= end:
        yield d.isoformat(), min(d + dt.timedelta(days=step - 1),
                                 end).isoformat()
        d += dt.timedelta(days=step)


def pending_dates(d_from, d_to):
    """The dates that actually still have a race without weights.

    Walking the whole calendar costs ~15s per empty chunk; in a mop-up range
    most chunks are empty, and that overhead is then the bulk of the runtime.
    So ask the database where the work is instead of guessing.
    """
    import pyodbc
    out: set[str] = set()
    for db in ("RACINGTV_2023_2026", "SCRAPED_PRODB"):
        try:
            c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                               "Server=(localdb)\\MSSQLLocalDB;Database=" + db +
                               ";Trusted_Connection=yes;Connection Timeout=30;")
            cur = c.cursor()
            cur.execute("""
                SELECT DISTINCT CONVERT(char(10), RaceDate, 120)
                FROM dbo.Scraped_Results
                WHERE RaceDate BETWEEN ? AND ?
                  AND (Weight IS NULL OR DistanceYards IS NULL)
            """, (d_from, d_to))
            out.update(r[0] for r in cur.fetchall())
            c.close()
        except Exception as e:
            log(f"  ! pending date query {db}: {str(e)[:60]}")
    return sorted(out)


def chunks_from_dates(dates, span_days):
    """Group the pending dates into chunks of at most `span_days`."""
    chunks: list[list[str]] = []
    for d in dates:
        d0 = dt.date.fromisoformat(d)
        if chunks and (d0 - dt.date.fromisoformat(chunks[-1][0])).days \
                < span_days:
            chunks[-1][1] = d
        else:
            chunks.append([d, d])
    return [(x, y) for x, y in chunks]


def pending_total(d_from, d_to):
    """How many races still need weights, across both databases."""
    import pyodbc
    total = 0
    for db in ("RACINGTV_2023_2026", "SCRAPED_PRODB"):
        try:
            c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                               "Server=(localdb)\\MSSQLLocalDB;Database=" + db +
                               ";Trusted_Connection=yes;Connection Timeout=30;")
            cur = c.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM (
                    SELECT RaceDate, RaceTime, CourseName
                    FROM dbo.Scraped_Results
                    WHERE RaceDate BETWEEN ? AND ?
                    GROUP BY RaceDate, RaceTime, CourseName
                    HAVING COUNT(Weight) < COUNT(*)
                    OR COUNT(DistanceYards) < COUNT(*)) x
            """, (d_from, d_to))
            total += cur.fetchone()[0]
            c.close()
        except Exception as e:
            log(f"  ! pending check {db}: {str(e)[:60]}")
    return total


def run_chunk(d1, d2, threads, hard_timeout, stall_seconds, work_log):
    """Run one chunk as a child process, killing it if it stalls.

    An in-process watchdog cannot help: the stall happens inside the ODBC
    driver while it holds the GIL, so every Python thread in the child freezes.
    The supervisor has to be external - if this worker's OWN log has not grown
    for `stall_seconds` the child is wedged.  It must be a per-worker log: with
    a shared one, a second healthy worker refreshes the timestamp and hides the
    stuck child (which is exactly what happened, twice).
    """
    p = subprocess.Popen([sys.executable, "-u", WORKER, "--from", d1,
                          "--to", d2, "--threads", str(threads),
                          "--log", work_log],
                         cwd=ROOT, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    t0 = time.time()
    last_progress = time.time()
    seen = os.path.getmtime(work_log) if os.path.exists(work_log) else 0
    while True:
        time.sleep(15)
        if p.poll() is not None:
            return "ok", time.time() - t0
        now = os.path.getmtime(work_log) if os.path.exists(work_log) else 0
        if now != seen:
            seen = now
            last_progress = time.time()
        if time.time() - last_progress > stall_seconds:
            p.kill()
            p.wait(timeout=30)
            return "stalled", time.time() - t0
        if time.time() - t0 > hard_timeout:
            p.kill()
            p.wait(timeout=30)
            return "timeout", time.time() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to", default=dt.date.today().isoformat())
    ap.add_argument("--chunk-days", type=int, default=10)
    ap.add_argument("--chunk-timeout", type=int, default=900,
                    help="absolute cap per chunk (backstop)")
    ap.add_argument("--stall-seconds", type=int, default=120,
                    help="no log activity for this long = wedged")
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--work-log", default=None,
                    help="per-worker progress log (default derived from --from)")
    ap.add_argument("--passes", type=int, default=2,
                    help="extra mopping-up passes once a full sweep is done")
    ap.add_argument("--targeted", action="store_true",
                    help="only visit dates that still have work (much faster "
                         "mop-up: avoids ~15s per empty chunk)")
    a = ap.parse_args()

    work_log = a.work_log or os.path.join(
        ROOT, "reports", f"bf_{a.d_from}_to_{a.d_to}.log")

    t0 = time.time()
    log(f"=== chunked backfill {a.d_from} .. {a.d_to} "
        f"chunk={a.chunk_days}d stall={a.stall_seconds}s threads={a.threads} "
        f"targeted={a.targeted} work_log={os.path.basename(work_log)} ===")
    for p in range(1, a.passes + 2):
        todo = pending_total(a.d_from, a.d_to)
        log(f"pass {p}: {todo:,} races still need weights")
        if todo == 0:
            break
        if a.targeted:
            dates = pending_dates(a.d_from, a.d_to)
            plan = chunks_from_dates(dates, a.chunk_days)
            log(f"pass {p}: {len(dates):,} dates have work -> "
                f"{len(plan)} chunks (vs "
                f"{len(list(daterange(a.d_from, a.d_to, a.chunk_days)))} "
                f"if walking the calendar)")
        else:
            plan = list(daterange(a.d_from, a.d_to, a.chunk_days))
        stalled = 0
        for d1, d2 in plan:
            why, secs = run_chunk(d1, d2, a.threads, a.chunk_timeout,
                                  a.stall_seconds, work_log)
            if why != "ok":
                stalled += 1
                log(f"  ! chunk {d1}..{d2} {why} after {secs:.0f}s, "
                    f"moving on")
        log(f"pass {p} done: {stalled} chunk(s) killed; "
            f"{pending_total(a.d_from, a.d_to):,} races left")
    left = pending_total(a.d_from, a.d_to)
    log(f"DONE chunked backfill {a.d_from}..{a.d_to}: {left:,} races still "
        f"without weights, {(time.time() - t0) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
