#!/usr/bin/env python3
"""prune_phantom_picks.py - delete ledger picks for horses that were never in a race.

THE PROBLEM
-----------
A pick can be logged for a horse that is not running that day.  The 2026-09-23 ledger
holds four of them:

    Tamarind Bay        Perth    15:57   last ran 2024-10-29
    Tamarind Bay        Perth    15:57   (logged twice - Tips AND Speed & Stride)
    Yoradreamer         Listowel 16:15   last ran 2025-04-11
    A Penny A Hundred   Listowel 16:15   not in the database at all

They are stamped "⏳ Running Today" and no settle run will ever clear them, because
`resettle_ledger.py` looks for the horse in `race_results` and there is nothing to
find - the horse did not run.  They sit on the site forever looking like a fault.

WHAT THIS DOES
--------------
Finds placeholder picks whose race has ALREADY GONE OFF but whose horse is not in
`race_results` for that date, and removes them from `results_ledger.csv` and
`system_results_ledger`.

A race only counts as gone off once it is more than `--hours` (default 2) past its
off time, so a race merely in progress is never mistaken for a phantom.  For any date
before today, every unresolved placeholder is eligible.

SAFETY
    dry run by default (--apply to write); backs the db up first; prints every row it
    would remove.

USAGE
    python prune_phantom_picks.py                        # report on today
    python prune_phantom_picks.py --date 2026-09-23 --apply
"""

import argparse
import csv
import datetime as dt
import os
import sqlite3
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resettle_ledger import DB_PATH, CSV_PATH, norm, rule, backup  # noqa: E402

FIELDS = None


def load_csv():
    global FIELDS
    with open(CSV_PATH, encoding="utf-8", errors="replace", newline="") as fh:
        rd = csv.DictReader(fh)
        FIELDS = rd.fieldnames
        return list(rd)


def race_has_gone_off(date, time_text, hours, now):
    """True once the race is more than `hours` in the past."""
    if date < now.strftime("%Y-%m-%d"):
        return True
    try:
        hh, mm = str(time_text).strip().split(":")[:2]
        off = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except Exception:
        return True                      # no off time - assume it has been run
    return off < now - dt.timedelta(hours=hours)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="only this date (yyyy-mm-dd); default today")
    ap.add_argument("--last", type=int, help="every date in the last N days")
    ap.add_argument("--hours", type=float, default=2.0,
                    help="how long after the off before a race counts as run "
                         "(default 2)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    now = dt.datetime.now(ZoneInfo("Europe/London"))
    today = now.strftime("%Y-%m-%d")

    rule("PRUNE PHANTOM PICKS  -  horses that never ran")
    print("  db      : %s" % DB_PATH)
    print("  csv     : %s" % CSV_PATH)
    print("  uk now  : %s" % now.strftime("%Y-%m-%d %H:%M"))
    print("  scope   : %s" % (a.date or ("last %d days" % a.last if a.last else today)))
    print("  mode    : %s" % ("APPLY - this deletes" if a.apply else "DRY RUN"))
    print()

    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True)
    cur = con.cursor()

    # SAFETY: if race_results has nothing for the date, a fetch failed rather than
    # every horse being a phantom - refuse, or this would wipe the whole day's picks.
    check = [a.date] if a.date else []
    if not check:
        check = sorted({(r.get("race_date") or "")[:10] for r in load_csv()})
        check = check[-min(len(check), a.last or 1):]
    for d in check:
        if not d:
            continue
        n = cur.execute("SELECT COUNT(*) FROM race_results WHERE race_date=?",
                        (d,)).fetchone()[0]
        if n == 0:
            print("  ABORT: race_results has NO rows for %s." % d)
            print("         That means the results fetch failed, not that every horse "
                  "was a phantom.")
            print("         Run the updater first (`tools/cloud_update.py --date %s --apply`)." % d)
            con.close()
            return 1

    rows = load_csv()
    phantoms = []
    for r in rows:
        d = (r.get("race_date") or "")[:10]
        if a.date and d != a.date:
            continue
        if a.last and not (today <= d or d >= (now - dt.timedelta(days=a.last)).strftime("%Y-%m-%d")):
            if d > today or d < (now - dt.timedelta(days=a.last)).strftime("%Y-%m-%d"):
                continue
        pos = (r.get("finish_pos") or "")
        if "Running" not in pos and "Pending" not in pos and pos.strip():
            continue                                  # already settled
        if not race_has_gone_off(d, r.get("race_time"), a.hours, now):
            continue                                  # not run yet - leave it
        horse = norm(r.get("horse_name")).replace(" ", "")
        hit = cur.execute(
            "SELECT finish_pos FROM race_results WHERE race_date=? AND "
            "LOWER(REPLACE(REPLACE(horse_name,'''',''),' ',''))=? LIMIT 1",
            (d, horse)).fetchone()
        if hit:
            continue                                  # the horse did run
        phantoms.append(r)

    print("  phantom picks (race ran, horse not in any race that day): %d" % len(phantoms))
    for r in phantoms:
        print("     %s  %-6s %-14s %-22s %s"
              % (r.get("race_date"), r.get("race_time"), r.get("course"),
                 r.get("horse_name"), r.get("system_name")))

    if not phantoms:
        print("\n  nothing to remove.")
        con.close()
        return 0

    if not a.apply:
        print("\n  Nothing written.  Re-run with --apply.")
        con.close()
        return 0

    backup()
    keys = {(r["race_date"], r["horse_name"], r["race_time"]) for r in phantoms}

    keep = [r for r in rows
            if (r["race_date"], r["horse_name"], r["race_time"]) not in keys]
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(keep)
    print("\n  csv : %d -> %d rows (removed %d)"
          % (len(rows), len(keep), len(rows) - len(keep)))

    wcon = sqlite3.connect(DB_PATH, timeout=60)
    wcon.execute("PRAGMA busy_timeout=60000")
    wcur = wcon.cursor()
    gone = 0
    for d, hn, rt in keys:
        gone += wcur.execute(
            "DELETE FROM system_results_ledger WHERE race_date=? AND horse_name=? "
            "AND race_time=?", (d, hn, rt)).rowcount
    wcon.commit()
    wcon.close()
    print("  table: %d rows removed" % gone)
    print("\n  done.")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
