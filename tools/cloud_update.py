#!/usr/bin/env python3
"""
cloud_update.py  -  update the app database from Racing Post, with NO local data.

WHY
---
Everything currently depends on one laptop: the fetcher, the bridge, the
settler and the publish all live on that machine.  Switch it off and the website
freezes.  The website itself cannot do the work - `cloud_app/requirements.txt`
has no `pyodbc`, and Streamlit Cloud has no SQL Server, no `LocalDB` and no
`D:\\Mydata`.

But the CORE does not need any of that.  `rp_fetch.day_races()` and
`race_result()` are plain HTTP, and `rp_to_rows.build_rows()` turns the payload
into rows carrying position, SP and race_id without touching a database of any
kind.  So this script can run anywhere with Python and a network - including a
GitHub Actions runner.

WHAT IT DOES
    1. for each of the last N days: day_races -> race_result -> build_rows
    2. UPSERT those rows into cloud_app/racing_form.db `race_results`
       (matching on (race_date, horse_name) so the app's own meeting spelling is
        preserved - see the note in mydata_bridge.py)
    3. settle any pending ledger rows from `race_results`
    4. mirror the result into `results_ledger.csv`, which is what app.py reads
    5. repack racing_form.db.gz

WHAT IT CANNOT DO (and does not need to)
    * the racingtv `Scraped_Results` scrape - that needs SQL Server, and the
      app's settlement no longer depends on it
    * the 1 GB D:\\Mydata archive and its gap filling
    * the morning price snapshot (that one needs Betfair credentials)

USAGE
    python tools/cloud_update.py --days 3              # dry run
    python tools/cloud_update.py --days 3 --apply
    RP_BASE=/tmp/rp python tools/cloud_update.py --days 3 --apply
"""

import argparse
import datetime as dt
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CLOUD_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# RP_BASE must be writable; rp_fetch caches a throttle file there.  On a runner
# point it at the temp dir - nothing here reads the archive database.
os.environ.setdefault("RP_BASE", os.environ.get("TEMP") or "/tmp")

import rp_fetch                                    # noqa: E402
import rp_to_rows as R                             # noqa: E402

DB = os.path.join(CLOUD_DIR, "racing_form.db")
CSV = os.path.join(CLOUD_DIR, "results_ledger.csv")

# The database travels as a release asset, not in git - see the DATABASE BOOTSTRAP
# note in app.py, and tools/publish_release_asset.py which uploads it.
REPO = os.environ.get("GITHUB_REPOSITORY", "Qasim9989/Horse_race")

SUFFIX = re.compile(r"\s*\([A-Z]{2,3}\)\s*$")


def norm(name):
    s = SUFFIX.sub("", str(name or "")).lower()
    s = s.replace("'", "").replace("\u2019", "").replace("\u2018", "")
    return re.sub(r"\s+", " ", s).strip()


def ordinal(pos):
    p = str(pos if pos is not None else "").strip()
    if not p:
        return ""
    try:
        n = int(float(p))
    except Exception:
        return p
    if n == 1:
        return "1st"
    if n == 2:
        return "2nd"
    if n == 3:
        return "3rd"
    return "%dth" % n


def clean(v):
    s = "" if v is None else str(v).strip()
    return "" if s in ("\u2013", "\u2014", "-", "\ufffd") else s


def row_from(r, race):
    """One RP runner -> the app's race_results shape."""
    return {
        "race_date": clean(r.get("date")),
        "horse_name": SUFFIX.sub("", clean(r.get("horse"))),
        "meeting": "",                       # filled from the app's existing row
        "distance": clean(r.get("dist")),
        "finish_pos": ordinal(r.get("pos")),
        "beaten_distance": clean(r.get("btn")),
        "weight_lbs": clean(r.get("wgt")),
        "official_rating": clean(r.get("or")),
        "topspeed": clean(r.get("ts")),
        "rpr": clean(r.get("rpr")),
        "jockey": clean(r.get("jockey")),
        "sp_odds": clean(r.get("sp")),
        "comment": clean(r.get("comment")),
        "going": clean(r.get("going")),
        "race_id": r.get("race_id"),
    }


def ensure_db():
    """Put a racing_form.db on disk so there is something to write to.

    Once racing_form.db.gz stops being committed (it now travels as a GitHub release
    asset, so the repo stops growing ~350 MB a day), a fresh runner checkout has no
    local archive at all - so the release asset has to be the primary source, with
    the local .gz used when it is present (a working copy, or the transitional
    period before the first release upload).
    """
    if os.path.exists(DB):
        return
    import gzip
    import shutil

    url = ("https://github.com/%s/releases/download/db-latest/racing_form.db.gz"
           % REPO)
    gz = DB + ".gz"
    src = ""

    try:
        tmp = gz + ".download"
        req = urllib.request.Request(url, headers={"User-Agent": "racing-form-ci"})
        with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as fh:
            shutil.copyfileobj(r, fh, 1024 * 1024)
        if os.path.getsize(tmp) >= 4096:
            os.replace(tmp, gz)
            src = "release asset"
        else:
            os.remove(tmp)
    except Exception as e:
        print("  release asset unavailable (%s)" % str(e)[:70])

    if not os.path.exists(gz):
        print("  no database: neither the release asset nor %s is available" % gz)
        return

    tmp = DB + ".unpacking"
    with gzip.open(gz, "rb") as s, open(tmp, "wb") as d:
        shutil.copyfileobj(s, d, 1024 * 1024)
    os.replace(tmp, DB)
    print("  unpacked %s -> %.1f MB%s"
          % (os.path.basename(gz), os.path.getsize(DB) / 1048576.0,
             ("  (from the %s)" % src) if src else ""))


def fetch(dates, quiet=False):
    """{date: [rows]} straight from Racing Post."""
    out = {}
    for d in dates:
        try:
            races, _ = rp_fetch.day_races(d)
        except Exception as e:
            if not quiet:
                print("   %s: day_races failed: %s" % (d, str(e)[:70]))
            continue
        if not races:
            if not quiet:
                print("   %s: no finished races yet" % d)
            continue
        rows = []
        for r in races:
            try:
                data, _ = rp_fetch.race_result(r)
            except Exception:
                continue
            if not data:
                continue
            rows.extend(R.build_rows(data))
        if rows:
            out[d] = rows
        if not quiet:
            print("   %s: %d races -> %d runner rows" % (d, len(races), len(rows)))
    return out


_ORD = {1: "1st", 2: "2nd", 3: "3rd", 21: "21st", 22: "22nd", 23: "23rd"}


def ordinal(pos):
    """`build_rows` gives pos="1"; race_results stores "1st".  Unplaced runners come
    through as "10", "PU", "F", "UR" etc. and are left alone."""
    s = str(pos if pos is not None else "").strip()
    if not s.isdigit():
        return s
    n = int(s)
    return _ORD.get(n, "%dth" % n)


def to_db_row(row):
    """rp_to_rows.build_rows() keys -> race_results column names.

    build_rows uses the SHORT names (date/horse/course/pos/dist/or/ts/sp) because
    that is what the D:\\Mydata archive scrape uses.  `race_results` uses the long
    ones, so every field has to be renamed.

    Values are passed through untouched: the table already stores sp_odds as a
    FRACTION ("6/4", "8/13f") and weight_lbs as stones-pounds ("9-7") - the same
    shapes build_rows produces.  Only `pos` needs converting to an ordinal.
    """
    def s(key):
        v = row.get(key)
        t = str(v if v is not None else "").strip()
        return "" if t in ("\u2013", "\u2014", "-", "nan", "None") else t

    return {
        "race_date": s("date"),
        "horse_name": s("horse"),
        "meeting": s("course"),
        "distance": s("dist"),
        "finish_pos": ordinal(row.get("pos")),
        "beaten_distance": s("btn"),
        "weight_lbs": s("wgt"),
        "official_rating": s("or"),
        "topspeed": s("ts"),
        "rpr": s("rpr"),
        "jockey": s("jockey"),
        "sp_odds": s("sp"),
        "comment": s("comment"),
        "going": s("going"),
        "race_id": s("race_id"),
    }


def canonical_meetings(cur):
    """lower(meeting) -> the spelling the database already uses.

    Racing Post says "Goodwood" but this database uses "goodwood", so inserting RP's
    casing creates a second meeting for the same course - the same duplicate-meeting
    bug that fix_dup_meetings.py had to clean up (479 rows).  Map every name onto the
    spelling already present, picking the most common one when several exist.
    """
    counts = {}
    for m, n in cur.execute("SELECT meeting, COUNT(*) FROM race_results "
                            "WHERE meeting IS NOT NULL AND TRIM(meeting)<>'' "
                            "GROUP BY meeting"):
        counts.setdefault(str(m).strip().lower(), []).append((n, str(m).strip()))
    canon = {}
    for low, options in counts.items():
        options.sort(reverse=True)
        canon[low] = options[0][1]
    return canon


def upsert(src, apply_):
    """Insert missing rows, fill blank cells, and set race_id where we have it."""
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    cur = con.cursor()
    cols = [r[1] for r in cur.execute('PRAGMA table_info("race_results")')]
    has_rid = "race_id" in cols
    # `comment` is NOT filled on an existing row - the app's own scraper stores
    # the in-running comment there, which is better than the racecard one.
    FILL = ["distance", "finish_pos", "beaten_distance", "weight_lbs",
            "official_rating", "topspeed", "rpr", "jockey", "sp_odds", "going"]
    stat = {"added": 0, "filled": 0, "cells": 0, "rid": 0, "renamed": 0}
    canon_meet = canonical_meetings(cur)

    def meet(name):
        """Reuse the spelling already in the database, or invent a clean one."""
        s = (name or "").strip()
        if not s:
            return s
        got = canon_meet.get(s.lower())
        if got:
            return got
        canon_meet[s.lower()] = s
        return s

    for d, rows in sorted(src.items()):
        rows = [to_db_row(r) for r in rows]
        for r in rows:
            before = r["meeting"]
            r["meeting"] = meet(before)
            if r["meeting"] != before:
                stat["renamed"] += 1
        existing = {}
        sel = ("SELECT horse_name, meeting, finish_pos, distance, beaten_distance, "
               "weight_lbs, official_rating, topspeed, rpr, jockey, sp_odds, comment, "
               "going" + (", race_id" if has_rid else "") +
               " FROM race_results WHERE race_date=?")
        for rec in cur.execute(sel, (d,)):
            existing[norm(rec[0])] = rec

        ins, upd = [], []
        for row in rows:
            k = norm(row["horse_name"])
            got = existing.get(k)
            if got is None:
                vals = [row.get(c, "") for c in cols if c != "rowid"]
                ins.append(vals)
                stat["added"] += 1
                continue
            # row exists - keep the app's meeting spelling, fill only blanks
            sets, args = [], []
            for i, c in enumerate(["finish_pos", "distance", "beaten_distance",
                                   "weight_lbs", "official_rating", "topspeed", "rpr",
                                   "jockey", "sp_odds", "going"]):
                old = clean(got[2 + i])
                new = row.get(c, "")
                if not old and new:
                    sets.append("%s=?" % c)
                    args.append(new)
            if has_rid and not got[13] and row.get("race_id"):
                sets.append("race_id=?")
                args.append(row["race_id"])
                stat["rid"] += 1
            if sets:
                args += [d, got[0]]
                if apply_:
                    cur.execute("UPDATE race_results SET %s WHERE race_date=? "
                                "AND horse_name=?" % ", ".join(sets), args)
                stat["filled"] += 1
                stat["cells"] += len(sets)
        if ins and apply_:
            use = [c for c in cols if c != "rowid"]
            cur.executemany("INSERT INTO race_results (%s) VALUES (%s)"
                            % (", ".join(use), ", ".join("?" * len(use))), ins)
    if apply_:
        con.commit()
    con.close()
    return stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3,
                    help="how many days back to fetch (default 3)")
    ap.add_argument("--date", help="fetch and settle only this date (yyyy-mm-dd)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    today = dt.date.today()
    if a.date:
        dates = [a.date]
    else:
        dates = [(today - dt.timedelta(days=i)).isoformat()
                 for i in range(0, a.days + 1)]

    print("=" * 78)
    print("CLOUD UPDATE  -  Racing Post -> app database, no local data needed")
    print("=" * 78)
    print("  dates : %s .. %s (%d days)" % (dates[-1], dates[0], len(dates)))
    print("  db    : %s" % DB)
    print("  mode  : %s" % ("APPLY" if a.apply else "DRY RUN"))
    print("  delay : %.2fs between requests (RP_DELAY to change)" % rp_fetch.DELAY)
    print()

    ensure_db()
    src = fetch(dates)
    total = sum(len(v) for v in src.values())
    print()
    print("  fetched %d rows across %d dates" % (total, len(src)))
    if not src:
        print("  nothing to do.")
        return 0

    st = upsert(src, a.apply)
    print()
    print("  race_results rows added  : %d" % st["added"])
    print("  rows with blanks filled  : %d  (%d cells)" % (st["filled"], st["cells"]))
    print("  race_id set              : %d" % st["rid"])
    print("  meetings mapped to db spelling: %d" % st["renamed"])

    if not a.apply:
        print("\n  Nothing written.  Re-run with --apply.")
        return 0

    # Settle, by calling resettle_ledger.py rather than re-implementing it.  The
    # order matters:
    #   1. settle the table FROM race_results  (the real work)
    #   2. table -> results_ledger.csv         (app.py reads the CSV FIRST)
    #   3. csv -> table                        (so the two cannot drift apart)
    print()
    print("  --- settling ---")
    here = os.path.dirname(os.path.abspath(__file__))
    rst = os.path.join(here, "resettle_ledger.py")
    for label, flags in (("settle table from race_results", ["--last", "10", "--apply"]),
                         ("mirror table -> csv", ["--sync-csv", "--apply"]),
                         ("mirror csv -> table", ["--sync-table", "--apply"])):
        print("  -> %s" % label)
        r = subprocess.run([sys.executable, "-u", rst] + flags,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600, env=os.environ)
        for line in (r.stdout or "").splitlines():
            if any(k in line for k in ("backlog", "can settle", "no match",
                                       "settled from the db", "rows added",
                                       "rows updated", "placeholder rows")):
                print("     %s" % line.strip())
        if r.returncode != 0:
            print("     FAILED (%d): %s" % (r.returncode, (r.stderr or "")[-200:]))
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
