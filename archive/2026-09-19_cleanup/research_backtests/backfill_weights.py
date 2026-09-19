"""
BACKFILL WEIGHTS FROM RACINGTV RESULTS PAGES
============================================
One source, RacingTV.  Results pages carry each runner's weight ("8-13" =
8st 13lb) for past races going back years, which the database never captured
(dbo.Scraped_Results.Weight is NULL in every row).

Weight is what sets a handicap mark, so this is the piece the selection system
is missing: with real weights we can measure weight/mark changes between runs
instead of depending on PRODB, which is frozen at 2026-05-22.

  python scripts/backfill_weights.py --days 3
  python scripts/backfill_weights.py --from 2026-06-01 --to 2026-09-13
  python scripts/backfill_weights.py --from 2021-01-01 --to 2026-09-13 --workers 4

Resumable: a race whose weights are already stored is skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import re
import sys
import time

import pyodbc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "reports", "backfill_weights.log")

# The LIVE scrape database.  racingtv_db_updater.py writes results here and
# this is the only Scraped_Results that keeps growing (2023-01-01 -> today).
# SCRAPED_PRODB holds the older 2021-2022 tail and is frozen at 2026-08-19.
DEFAULT_DB = "RACINGTV_2023_2026"


def conn_str(db):
    return (r"Driver={ODBC Driver 17 for SQL Server};"
            r"Server=(localdb)\MSSQLLocalDB;Database=" + db +
            r";Trusted_Connection=yes;MultipleActiveResultSets=True;"
            r"Connection Timeout=30;")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

GRAB = r"""
() => {
  const out = [];
  let cur = null;
  for (const el of document.querySelectorAll('a[href*="/profiles/horse/"], div')) {
    if (el.tagName === 'A' &&
        (el.getAttribute('href') || '').includes('/profiles/horse/')) {
      const t = (el.innerText || '').split('\n')[0].trim();
      if (t) { cur = {name: t, wgt: null, claim: null}; out.push(cur); }
      continue;
    }
    if (!cur || el.children.length) continue;
    const t = (el.innerText || '').trim();
    if (!t || t.length > 12) continue;
    if (/^\d{1,2}-\d{1,2}$/.test(t) && !cur.wgt) { cur.wgt = t; continue; }
    const m = /^\(?\s*(\d+)\s*lb\s*\)?$/i.exec(t);
    if (m && cur.claim === null) { cur.claim = parseInt(m[1], 10); }
  }
  return out;
}
"""


def log(msg):
    line = f"{dt.datetime.now():%H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def lbs(weight):
    """'8-13' -> 125 lb."""
    if not weight:
        return None
    m = re.match(r"^(\d{1,2})-(\d{1,2})$", str(weight).strip())
    return int(m.group(1)) * 14 + int(m.group(2)) if m else None


def pending_races(conn, d_from, d_to):
    """Races in range where no runner has a weight stored yet.

    Race-level, not date-level, so an interrupted run still resumes properly
    for the races it had not finished.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT RaceDate, RaceTime, CourseName
        FROM dbo.Scraped_Results
        WHERE RaceDate BETWEEN ? AND ?
        GROUP BY RaceDate, RaceTime, CourseName
        HAVING COUNT(Weight) = 0
        ORDER BY RaceDate DESC, RaceTime
    """, (d_from, d_to))
    return [(r[0], str(r[1])[:5].replace(":", ""), r[2])
            for r in cur.fetchall()]


async def fetch(pg, url, wait=1.2, sel_timeout=20):
    """Wait for the runner rows to exist rather than guessing with a fixed
    sleep - faster when the page is quick, complete when it is slow."""
    await pg.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        await pg.wait_for_selector('a[href*="/profiles/horse/"]',
                                   timeout=sel_timeout * 1000)
    except Exception:
        await asyncio.sleep(3)
    await asyncio.sleep(wait)          # let the rest of the row render
    return await pg.evaluate(GRAB)


async def run(d_from, d_to, workers, wait, db=DEFAULT_DB):
    from playwright.async_api import async_playwright

    conn = pyodbc.connect(conn_str(db), autocommit=True)
    todo = pending_races(conn, d_from, d_to)
    log(f"range {d_from}..{d_to}: {len(todo):,} races need weights")
    if not todo:
        conn.close()
        return 0, 0

    done = rows = 0
    cur = conn.cursor()
    t0 = time.time()
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(user_agent=UA,
                                  viewport={"width": 1400, "height": 900})
        sem = asyncio.Semaphore(workers)

        async def one(day, hhmm, course):
            nonlocal done, rows
            hhmm = str(hhmm).zfill(4)
            url = f"https://www.racingtv.com/results/{day}/{course}/{hhmm}"
            runtime = hhmm            # RaceTime is stored as '1730', not '17:30'
            async with sem:
                pg = await ctx.new_page()
                try:
                    got = await fetch(pg, url, wait)
                except Exception as e:
                    log(f"  ! {day} {course} {hhmm}: {type(e).__name__}")
                    got = []
                finally:
                    await pg.close()
            n = 0
            for r in got:
                w = lbs(r["wgt"])
                if w is None:
                    continue
                # LocalDB drops connections when many workers write at once,
                # so retry once before giving up on a runner
                for attempt in (1, 2):
                    try:
                        cur.execute(
                            "UPDATE dbo.Scraped_Results SET Weight=?, "
                            "JockeyClaim=COALESCE(?, JockeyClaim) "
                            "WHERE RaceDate=? AND RaceTime=? AND CourseName=? "
                            "AND HorseName=?",
                            (f"{w // 14}-{w % 14}", r.get("claim"), day,
                             runtime, course, r["name"]))
                        n += max(cur.rowcount, 0)
                        break
                    except pyodbc.Error as e:
                        if attempt == 2:
                            log(f"  ! write failed {day} {course} {hhmm} "
                                f"{r['name']}: {str(e)[:60]}")
                        else:
                            await asyncio.sleep(0.5)
            done += 1
            rows += n
            if done % 25 == 0:
                rate = done / max(time.time() - t0, 1)
                log(f"  {done}/{len(todo)} races, {rows:,} weights set, "
                    f"{rate * 60:.1f} races/min, "
                    f"eta {(len(todo) - done) / max(rate, 0.0001) / 60:.0f} min")

        for i in range(0, len(todo), workers * 3):
            await asyncio.gather(*(one(*r) for r in todo[i:i + workers * 3]))
            await asyncio.sleep(0.3)
        await b.close()
    conn.close()
    log(f"DONE {d_from}..{d_to}: {done:,} races, {rows:,} weights written, "
        f"{(time.time() - t0) / 60:.1f} min")
    return done, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="d_from", default=None)
    ap.add_argument("--to", dest="d_to", default=None)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--db", default=DEFAULT_DB,
                    help="target database (default: the live scrape DB)")
    ap.add_argument("--wait", type=float, default=1.2,
                    help="settle time after the runner rows appear")
    a = ap.parse_args()
    today = dt.date.today()
    if a.days:
        d_from, d_to = (today - dt.timedelta(days=a.days)).isoformat(), \
            today.isoformat()
    else:
        d_from = a.d_from or (today - dt.timedelta(days=7)).isoformat()
        d_to = a.d_to or today.isoformat()
    log(f"=== backfill weights {d_from} .. {d_to}  db={a.db}  "
        f"workers={a.workers} ===")
    done, _rows = asyncio.run(run(d_from, d_to, a.workers, a.wait, a.db))
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())

