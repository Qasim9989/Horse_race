"""
WAIT FOR BETFAIR SP, THEN IMPORT IT
===================================
Betfair publishes https://promo.betfair.com/betfairsp/prices/
dwbfprices{uk,ire}winDDMMYYYY.csv, and **the file dated DDMMYYYY holds the
PREVIOUS day's races**.  A race date's SP therefore only exists in the file
published the day after - which is why "run the backfill after racing" tonight
cannot work.

This waits for the right file to appear, then imports it and rebuilds the
price log and the book-vs-BSP report.

  python scripts/wait_for_bsp.py 2026-09-15            # wait (default 20h), then import
  python scripts/wait_for_bsp.py 2026-09-15 --check     # just say if it is there
  python scripts/wait_for_bsp.py 2026-09-15 --interval 15 --deadline 8
  python scripts/wait_for_bsp.py 2026-09-15 --now       # try once, import if up
"""
import argparse
import csv
import datetime as dt
import io
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # project root: run scripts from there
UA = {"User-Agent": "Mozilla/5.0"}
URL = ("https://promo.betfair.com/betfairsp/prices/"
       "dwbfprices{}win{:02d}{:02d}{:04d}.csv")


def cells(row, key):
    return next((v for k, v in row.items() if str(k).lower() == key), "")


def file_has_date(publish_day, race_day):
    """Do any rows in that day's files belong to race_day?

    Returns (found, detail_string).  Both sources are checked because an Irish
    meeting only appears in the IRE file.
    """
    seen = []
    for src in ("uk", "ire"):
        url = URL.format(src, publish_day.day, publish_day.month,
                         publish_day.year)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=40) as r:
                text = r.read().decode("utf-8", errors="replace")
        except Exception as e:
            seen.append(f"{src}:{type(e).__name__}")
            continue
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            seen.append(f"{src}:empty")
            continue
        dates = {str(cells(r, "event_dt"))[:10] for r in rows}
        if race_day.strftime("%d-%m-%Y") in dates:
            return True, (f"{src}: {len(rows)} rows for " \
                         f"{race_day.strftime('%d-%m-%Y')}")
        seen.append(f"{src}: other dates {sorted(dates)[:2]}")
    return False, "; ".join(seen)


def run(cmd):
    print(f"    > {' '.join(cmd[1:])}", flush=True)
    res = subprocess.run(cmd, cwd=ROOT)
    return res.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("race_date", help="YYYY-MM-DD - the day the races ran")
    ap.add_argument("--check", action="store_true",
                    help="report whether the file is out, do not wait/import")
    ap.add_argument("--now", action="store_true",
                    help="try once: import if available, otherwise stop")
    ap.add_argument("--interval", type=float, default=20,
                    help="minutes between checks (default 20)")
    ap.add_argument("--deadline", type=float, default=20,
                    help="give up after this many hours (default 20)")
    a = ap.parse_args()

    race_day = dt.date.fromisoformat(a.race_date)
    publish_day = race_day + dt.timedelta(days=1)
    print("=" * 68)
    print(f"  Betfair SP for races on {race_day}")
    print(f"  Published in the file dated {publish_day} "
          f"(Betfair ships a day's prices the next day)")
    print("=" * 68)

    pub_eta = dt.datetime.combine(publish_day, dt.time(10, 0))
    hours_away = (pub_eta - dt.datetime.now()).total_seconds() / 3600
    if hours_away > 0:
        print(f"  That file does not exist yet, and cannot: it is created on "
              f"{publish_day}\n  (roughly {hours_away:.0f} hours from now). "
              "Nothing you run before then will change it.")

    deadline = dt.datetime.now() + dt.timedelta(hours=a.deadline)
    attempt = 0
    while True:
        attempt += 1
        found, detail = file_has_date(publish_day, race_day)
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        print(f"  [{stamp}] check {attempt}: "
              f"{'FOUND' if found else 'not yet'} - {detail}", flush=True)

        if found:
            break
        if a.check or a.now:
            print(f"\n  Nothing to import yet. Betfair publishes this in the "
                  f"file dated {publish_day} -\n  re-run this script after "
                  f"that morning (or drop --check/--now to let it wait here).")
            return 1
        if dt.datetime.now() >= deadline:
            print(f"\n  Gave up after {a.deadline}h. The file for "
                  f"{race_day} is usually out by mid-morning on {publish_day}."
                  "  Re-run this script then.")
            return 2
        nxt = dt.datetime.now() + dt.timedelta(minutes=a.interval)
        print(f"          (leaving this window open - it will check again at "
              f"{nxt.strftime('%H:%M')}, and keeps trying until "
              f"{deadline.strftime('%a %H:%M')})", flush=True)
        time.sleep(a.interval * 60)

    if a.check:
        print("\n  File is available.")
        return 0

    rc = 0
    print("\n  [1/3] importing the SP into PRODB.dbo.BFSP ...")
    rc |= run([sys.executable, "scripts\\betfair_bsp_backfill.py",
               race_day.isoformat(), race_day.isoformat()])
    print("\n  [2/3] rebuilding the price log ...")
    rc |= run([sys.executable, "scripts\\book_odds.py", "pricelog",
               race_day.isoformat()])
    print("\n  [3/3] book price vs Betfair SP report...")
    rc |= run([sys.executable, "scripts\\book_odds.py", "report",
               race_day.isoformat(), "--book", "BEST"])
    print("\n" + "=" * 68)
    print("  DONE. Open dashboard.bat and tick 'Auto-refresh' - the "
          "'Book vs Betfair' tab\n  now has data. Or run:"
          f"  python scripts\\book_odds.py value {race_day}")
    print("=" * 68)
    return rc


if __name__ == "__main__":
    sys.exit(main())
