#!/usr/bin/env python3
"""
resettle_ledger.py  -  settle the placeholder rows in `system_results_ledger`.

THE PROBLEM
-----------
Picks are logged with  finish_pos = '⏳ Running Today'  and are supposed to be
settled overnight by `settle_daily_results.py`.  That script cannot work:

  1. it queries  (localdb)\\MSSQLLocalDB / RACINGTV_2023_2026  - a database that
     DOES NOT EXIST on this machine (checked every SQL Server instance), so
     `fetch_scraped_results()` always returns nothing;
  2. it reads `results_ledger.csv` expecting columns `date`, `pos`, `horse`,
     but the file actually has `race_date`, `finish_pos`, `horse_name` -
     so it dies with `KeyError: 'date'` on the very first line.

Result: **125 rows were stuck on '⏳ Running Today'**, 48 of them for 2026-09-22,
which is what made the app show "⏳ Scheduled (Today)" for races that had already
been run.

WHAT THIS DOES INSTEAD
----------------------
D:\\Mydata now keeps `cloud_app/racing_form.db`.`race_results` complete, so the
outcome is already in the database - no scraper, no SQL Server, no CSV needed.
This script reads the unanswered ledger rows, matches them against
`race_results` on (race_date, horse_name), and fills in the position, SP and
P&L using the SAME formulae `settle_daily_results.py` used (see `settle_row`).

It updates both the database table and `results_ledger.csv` so they stay in step.

SAFETY
    dry run by default (--apply to write); backs up the db first.

USAGE
    python resettle_ledger.py                       # report the backlog
    python resettle_ledger.py --date 2026-09-22 --apply
    python resettle_ledger.py --all --apply
"""

import argparse
import csv
import datetime as dt
import os
import re
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# This module lives in two places: scripts/ (next to the pipeline, on the archive
# host) and cloud_app/tools/ (for the GitHub Action, where the repo IS cloud_app).
# Resolve the paths for whichever layout we are in.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.exists(os.path.join(HERE, "racing_form.db")):
    DB_PATH = os.path.join(HERE, "racing_form.db")
    CSV_PATH = os.path.join(HERE, "results_ledger.csv")
    BACKUP_DIR = os.path.join(HERE, "_settle_backups")
else:
    DB_PATH = os.path.join(PROJECT_DIR, "cloud_app", "racing_form.db")
    CSV_PATH = os.path.join(PROJECT_DIR, "cloud_app", "results_ledger.csv")
    BACKUP_DIR = os.path.join(PROJECT_DIR, "archive", "settle_backups")
KEEP = 3

# exactly the set settle_daily_results.py treats as "not yet settled"
PLACEHOLDERS = ("\u23f3 Running Today", "Pending", "-", "nan", "None", "")

SUFFIX = re.compile(r"\s*\([A-Z]{2,3}\)\s*$")
FRAC = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*([a-zA-Z]*)\s*$")


def bare(name):
    return SUFFIX.sub("", str(name or "")).strip()


def norm(name):
    """Match key for a horse name.

    The app's ledger writes 'Winston's Oath' / 'She's Too Kool' while Racing Post
    stores 'Winstons Oath (IRE)' / 'Shes Too Kool (IRE)' - no apostrophe and a
    country suffix.  Strip both sides so they line up.
    """
    s = bare(name).lower()
    s = s.replace("'", "").replace("\u2019", "").replace("\u2018", "")
    return re.sub(r"\s+", " ", s).strip()


def frac_to_dec(txt):
    """'6/4' -> 2.5   '8/13f' -> 1.62   '200/1' -> 201.0   'evs' -> 2.0"""
    s = str(txt or "").strip().lower()
    if not s or s in ("-", "nan", "none"):
        return None
    if s in ("evs", "evens", "evensf"):
        return 2.0
    m = FRAC.match(s)
    if m:
        n, d = float(m.group(1)), float(m.group(2))
        if d:
            return round(n / d + 1.0, 4)
    try:
        v = float(s)
        return v if v > 1.0 else None
    except Exception:
        return None


def rule(t=""):
    print("=" * 78)
    if t:
        print(t)
        print("=" * 78)


def backup():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        dst = os.path.join(BACKUP_DIR, "racing_form_%s.db"
                           % dt.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
        shutil.copy2(DB_PATH, dst)
        print("  backup: %s (%.1f MB)" % (dst, os.path.getsize(dst) / 1048576.0))
        bs = sorted((os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
                     if f.endswith(".db")), key=os.path.getmtime, reverse=True)
        for old in bs[KEEP:]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception as e:
        print("  backup FAILED: %s" % str(e)[:100])
        raise


def settle_row(places_paid, early_odds, early_place_odds, pos_str, sp_dec):
    """Return (finish_pos, won, placed, e_win, sp_win, e_ew, sp_ew).

    Mirrors settle_daily_results.py lines 187-235 exactly, so rows settled by
    this script and rows settled by the old scraper are directly comparable.
    """
    places_paid = int(places_paid or 3)
    fraction = 0.25 if places_paid >= 4 else 0.20

    pos_str = str(pos_str or "").strip()
    is_nr = pos_str.upper() in ("NR", "NON-RUNNER", "VOID", "NR (VOID)") \
        or "void" in pos_str.lower()
    if is_nr:
        return "NR (Void)", 0, 0, 0.0, 0.0, 0.0, 0.0

    numeric = re.sub(r"(st|nd|rd|th)$", "", pos_str)
    won = 1 if pos_str in ("1", "1st") else 0
    if won == 1 or pos_str.lower() == "placed":
        placed = 1
    elif numeric.isdigit():
        placed = 1 if int(numeric) <= places_paid else 0
    else:
        placed = 0

    e_odds = float(early_odds) if early_odds and float(early_odds) > 1.0 \
        else (sp_dec or 1.0)
    e_pl_odds = float(early_place_odds or 0) or round(1.0 + (e_odds - 1.0) * fraction, 2)
    s_odds = sp_dec if (sp_dec and sp_dec > 1.0) else e_odds
    s_pl_odds = round(1.0 + (s_odds - 1.0) * fraction, 2)

    e_win = round(e_odds - 1.0, 2) if won == 1 else -1.0
    sp_win = round(s_odds - 1.0, 2) if won == 1 else -1.0
    if won == 1:
        e_ew = round((e_odds - 1.0) + (e_pl_odds - 1.0), 2)
        sp_ew = round((s_odds - 1.0) + (s_pl_odds - 1.0), 2)
    elif placed == 1:
        e_ew = round((e_pl_odds - 1.0) - 1.0, 2)
        sp_ew = round((s_pl_odds - 1.0) - 1.0, 2)
    else:
        e_ew = sp_ew = -2.0
    return pos_str, won, placed, e_win, sp_win, e_ew, sp_ew


def write_csv(csv_updates):
    """Apply the same settlements to `results_ledger.csv`.

    CRITICAL: `app.py`'s `load_results_ledger()` reads that CSV **first** and only
    falls back to the `system_results_ledger` table when the file is missing or
    empty.  Settling the table alone changes nothing the user can see - the site
    keeps rendering "⏳ Scheduled (Today)" straight from the CSV.
    """
    import pandas as pd
    if not os.path.exists(CSV_PATH):
        print("  csv not found, skipped: %s" % CSV_PATH)
        return 0
    df = pd.read_csv(CSV_PATH)
    n = 0
    for d, hn, rt, vals in csv_updates:
        mask = ((df["race_date"].astype(str) == str(d))
                & (df["horse_name"].astype(str) == str(hn))
                & (df["race_time"].astype(str) == str(rt)))
        if not mask.any():
            continue
        for col, v in vals.items():
            if col in df.columns:
                df.loc[mask, col] = v
        n += int(mask.sum())
    df.to_csv(CSV_PATH, index=False)
    return n


def sync_csv_from_db(apply_):
    """Copy settled values from `system_results_ledger` into `results_ledger.csv`.

    Needed because the two drifted apart: the table was settled while the CSV -
    which is what app.py actually displays - was left on "⏳ Running Today".
    """
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True)
    cur = con.cursor()
    settled = {}
    for (d, hn, rt, fpos, won, placed, spo, spt,
         ew, sw, eew, sew) in cur.execute(
            "SELECT race_date, horse_name, race_time, finish_pos, won, placed, "
            "sp_odds, sp_text, early_win_pl, sp_win_pl, early_ew_pl, sp_ew_pl "
            "FROM system_results_ledger WHERE finish_pos IS NOT NULL "
            "AND TRIM(finish_pos)<>'' AND finish_pos NOT LIKE '%Running%' "
            "AND finish_pos NOT LIKE '%Scheduled%' AND finish_pos NOT LIKE '%Pending%'"):
        settled[(str(d), str(hn), str(rt))] = {
            "finish_pos": fpos, "won": won, "placed": placed,
            "sp_odds": spo, "sp_text": spt,
            "early_win_pl": ew, "sp_win_pl": sw,
            "early_ew_pl": eew, "sp_ew_pl": sew}
    con.close()

    import pandas as pd
    if not os.path.exists(CSV_PATH):
        print("  csv not found: %s" % CSV_PATH)
        return 0, 0
    df = pd.read_csv(CSV_PATH)
    fixed = 0
    checked = 0
    for i, r in df.iterrows():
        key = (str(r["race_date"]), str(r["horse_name"]), str(r["race_time"]))
        cur_pos = str(r.get("finish_pos") or "")
        if "Running" not in cur_pos and "Scheduled" not in cur_pos and cur_pos.strip():
            continue
        checked += 1
        v = settled.get(key)
        if not v:
            continue
        for col, val in v.items():
            if col in df.columns:
                df.at[i, col] = val
        fixed += 1
    if apply_ and fixed:
        df.to_csv(CSV_PATH, index=False)
    return checked, fixed


def sync_table_from_csv(apply_):
    """Import rows from `results_ledger.csv` into `system_results_ledger`.

    The two drifted: after log_todays_selections.py writes the CSV, the table still
    has nothing for that date.  `app.py` prefers the CSV but falls back to the table
    when the CSV is missing/empty, so anything reading the table sees stale data.
    """
    import pandas as pd
    if not os.path.exists(CSV_PATH):
        print("  csv not found: %s" % CSV_PATH)
        return 0, 0
    df = pd.read_csv(CSV_PATH)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    cur = con.cursor()

    cols = [r[1] for r in cur.execute('PRAGMA table_info("system_results_ledger")')]
    have = [c for c in df.columns if c in cols]
    missing = [c for c in df.columns if c not in cols]
    print("  csv columns : %d   table columns: %d" % (len(df.columns), len(cols)))
    if missing:
        print("  not in table: %s" % ", ".join(missing))

    # existing keys in the table, by (race_date, horse_name, race_time)
    key_sel = ('SELECT race_date, horse_name, race_time, rowid '
               'FROM system_results_ledger')
    existing = {}
    for d, hn, rt, rid in cur.execute(key_sel):
        existing[(str(d), str(hn), str(rt))] = rid

    added = updated = 0
    for _, r in df.iterrows():
        key = (str(r["race_date"]), str(r["horse_name"]), str(r["race_time"]))
        vals = {c: (None if pd.isna(r[c]) else r[c]) for c in have}
        if key in existing:
            sets = ", ".join("%s=?" % c for c in have if c != "race_date")
            args = [vals[c] for c in have if c != "race_date"]
            args.append(existing[key])
            if apply_:
                cur.execute("UPDATE system_results_ledger SET %s WHERE rowid=?"
                            % sets, args)
            updated += 1
        else:
            ph = ", ".join("?" * len(have))
            if apply_:
                cur.execute('INSERT INTO system_results_ledger (%s) VALUES (%s)'
                            % (", ".join(have), ph), [vals[c] for c in have])
            added += 1
    if apply_:
        con.commit()
    con.close()
    return added, updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="only this date (yyyy-mm-dd)")
    ap.add_argument("--last", type=int, help="the last N days up to yesterday")
    ap.add_argument("--all", action="store_true", help="every date with a backlog")
    ap.add_argument("--sync-csv", action="store_true",
                    help="copy settled values from the db table into "
                         "results_ledger.csv (the file the app actually reads)")
    ap.add_argument("--sync-table", action="store_true",
                    help="import results_ledger.csv into system_results_ledger so "
                         "the two do not drift apart")
    ap.add_argument("--apply", action="store_true", help="write (default: report)")
    a = ap.parse_args()

    if a.sync_csv:
        rule("SYNC  results_ledger.csv  FROM  system_results_ledger")
        checked, fixed = sync_csv_from_db(a.apply)
        print("  placeholder rows in the csv : %d" % checked)
        print("  settled from the db table   : %d" % fixed)
        if not a.apply:
            print("\n  Nothing written.  Re-run with --apply.")
        return 0

    if a.sync_table:
        rule("SYNC  system_results_ledger  FROM  results_ledger.csv")
        added, updated = sync_table_from_csv(a.apply)
        print("  rows added   : %d" % added)
        print("  rows updated : %d" % updated)
        if not a.apply:
            print("\n  Nothing written.  Re-run with --apply.")
        return 0

    con = sqlite3.connect(DB_PATH, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    cur = con.cursor()

    where = ("WHERE (finish_pos IS NULL OR TRIM(COALESCE(finish_pos,''))='' "
             "OR finish_pos LIKE '%Running%' OR finish_pos LIKE '%Pending%')")
    args = []
    if a.date:
        where += " AND race_date=?"
        args.append(a.date)
    elif a.last:
        where += " AND race_date >= date('now','-%d day')" % a.last
    elif not a.all:
        where += " AND race_date >= date('now','-10 day')"

    rows = list(cur.execute(
        "SELECT rowid, race_date, horse_name, course, race_time, system_name, "
        "early_odds, early_place_odds, places_paid, finish_pos, race_id "
        "FROM system_results_ledger " + where + " ORDER BY race_date, race_time", args))

    rule("RESETTLE LEDGER  from race_results")
    print("  db     : %s" % DB_PATH)
    print("  scope  : %s" % (a.date or ("ALL dates" if a.all else "last 10 days")))
    print("  mode   : %s" % ("APPLY - this writes" if a.apply else "DRY RUN"))
    print("  backlog: %d rows" % len(rows))
    print()

    by_date = {}
    for r in rows:
        by_date[r[1]] = by_date.get(r[1], 0) + 1
    if by_date:
        print("  backlog by date:")
        for d in sorted(by_date):
            print("     %s  %d" % (d, by_date[d]))
    print()

    updates = []
    unmatched = []
    csv_updates = []          # (race_date, horse_name, race_time) -> new values
    rrmap = {}
    ridmap = {}
    by_how = {"race_id": 0, "name": 0}
    for (rid, rdate, horse, course, rtime, sysname, e_odds, e_pl, pp,
         _old, led_race_id) in rows:
        if rdate not in rrmap:
            rrmap[rdate] = {
                norm(hn): (pos, sp) for pos, sp, hn in cur.execute(
                    "SELECT finish_pos, sp_odds, horse_name FROM race_results "
                    "WHERE race_date=?", (rdate,))}
            # the exact join.  NOTE the key is (race_id, horse) and NOT race_id
            # alone - a race_id identifies a RACE, so keying on it alone collapses
            # every runner in that race onto one value and hands out the wrong
            # finishing position.
            ridmap[rdate] = {
                (r, norm(hn)): (pos, sp) for r, hn, pos, sp in cur.execute(
                    "SELECT race_id, horse_name, finish_pos, sp_odds FROM race_results "
                    "WHERE race_date=? AND race_id IS NOT NULL", (rdate,))}

        # (race_id, horse) first - it cannot be confused by apostrophes or name
        # variants.  Fall back to the normalised name for rows without an id.
        hit = ridmap[rdate].get((led_race_id, norm(horse))) if led_race_id else None
        how = "race_id"
        if not hit:
            hit = rrmap[rdate].get(norm(horse))
            how = "name"
        if not hit or not str(hit[0] or "").strip():
            unmatched.append((rdate, horse, course, rtime))
            continue
        rr_pos, rr_sp = hit
        by_how[how] += 1
        sp_dec = frac_to_dec(rr_sp)
        f_pos, won, placed, e_win, sp_win, e_ew, sp_ew = settle_row(
            pp, e_odds, e_pl, rr_pos, sp_dec)
        vals = {"finish_pos": f_pos, "won": won, "placed": placed,
                "sp_odds": (sp_dec if sp_dec else ""),
                "sp_text": (str(rr_sp) if rr_sp else ""),
                "early_win_pl": e_win, "sp_win_pl": sp_win,
                "early_ew_pl": e_ew, "sp_ew_pl": sp_ew}
        # the CSV is what app.py actually displays (load_results_ledger reads it
        # FIRST and only falls back to the table when the file is missing), so
        # both must be written or the site keeps showing "Running Today"
        csv_updates.append((rdate, horse, rtime, vals))
        updates.append((f_pos, won, placed,
                        sp_dec if sp_dec else None,
                        (str(rr_sp) if rr_sp else None),
                        e_win, sp_win, e_ew, sp_ew, rid))

    print("  can settle : %d   (by race_id %d, by name fallback %d)"
          % (len(updates), by_how["race_id"], by_how["name"]))
    print("  no match   : %d" % len(unmatched))
    for u in unmatched[:10]:
        print("       %s  %-26s %-16s %s" % u)
    if len(unmatched) > 10:
        print("       ... %d more" % (len(unmatched) - 10))
    print()
    for u in updates[:6]:
        print("     %-26s -> %-9s won=%s placed=%s sp=%s"
              % (u[-1], u[0], u[1], u[2], u[3]))

    if a.apply and updates:
        print()
        backup()
        cur.executemany(
            "UPDATE system_results_ledger SET finish_pos=?, won=?, placed=?, "
            "sp_odds=?, sp_text=?, early_win_pl=?, sp_win_pl=?, "
            "early_ew_pl=?, sp_ew_pl=? WHERE rowid=?", updates)
        con.commit()
        print("  updated %d ledger rows in the db" % len(updates))
        n = write_csv(csv_updates)
        print("  updated %d rows in results_ledger.csv  <- this is what the app reads"
              % n)
    elif not a.apply:
        print("\n  Nothing written.  Re-run with --apply.")

    left = cur.execute(
        "SELECT COUNT(*) FROM system_results_ledger WHERE finish_pos LIKE '%Running%' "
        "OR finish_pos LIKE '%Pending%' OR finish_pos IS NULL "
        "OR TRIM(COALESCE(finish_pos,''))=''").fetchone()[0]
    print("\n  placeholder rows remaining: %d" % left)
    con.close()
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        dst = os.path.join(BACKUP_DIR, "racing_form_%s.db"
                           % dt.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
        shutil.copy2(DB_PATH, dst)
        print("  backup: %s (%.1f MB)" % (dst, os.path.getsize(dst) / 1048576.0))
        bs = sorted((os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
                     if f.endswith(".db")), key=os.path.getmtime, reverse=True)
        for old in bs[KEEP:]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception as e:
        print("  backup FAILED: %s" % str(e)[:100])
        raise
