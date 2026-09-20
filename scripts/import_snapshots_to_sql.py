r"""
CLOUD SNAPSHOTS -> SQL  (the other half of laptop-independent capture)
======================================================================
The GitHub workflows capture odds on GitHub's servers, so the laptop does not
need to be on.  They cannot write to our SQL Server: that database is LocalDB on
this machine - no public endpoint, and exposing one would be a liability.  So the
capture lands in the repo (cloud_app/snapshots/odds_*.json) and this script moves
it across when the laptop is awake:

    git -C cloud_app pull --ff-only        # collect what the cloud committed
    python scripts\import_snapshots_to_sql.py

One row per runner per capture, so the whole day is in SQL:

    morning   whole card
    hourly    whole remaining card
    T-15 T-10 T-5 T-4 T-3 T-2 T-1   one or two races at a time
    BSP       the Betfair starting price (WIN and PLACE), once a race is off

Idempotent: dbo.SnapshotFiles remembers which files have been imported, so
re-running only picks up what is new.

    python scripts\import_snapshots_to_sql.py --dry-run
    python scripts\import_snapshots_to_sql.py --since-days 7
    python scripts\import_snapshots_to_sql.py --force     # re-import everything
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

PRO = (r"Driver={ODBC Driver 17 for SQL Server};"
       r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;"
       r"MultipleActiveResultSets=True;Connection Timeout=120;")

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
SNAP_DIR = os.path.join(PROJECT, "cloud_app", "snapshots")

DDL_FILES = """
IF OBJECT_ID('dbo.SnapshotFiles') IS NULL
CREATE TABLE dbo.SnapshotFiles (
    SourceFile   NVARCHAR(140) NOT NULL PRIMARY KEY,
    Scope        NVARCHAR(16)  NULL,
    CapturePoint NVARCHAR(16)  NULL,
    CapturedAt   DATETIME2     NULL,
    RaceDate     DATE          NULL,
    RowsImported INT           NULL,
    ImportedAt   DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
)
"""

DDL_ROWS = """
IF OBJECT_ID('dbo.SnapshotOdds') IS NULL
CREATE TABLE dbo.SnapshotOdds (
    Id             BIGINT IDENTITY(1,1) PRIMARY KEY,
    SourceFile     NVARCHAR(140) NOT NULL,
    Scope          NVARCHAR(16)  NULL,
    CapturePoint   NVARCHAR(16)  NULL,
    CapturedAt     DATETIME2     NULL,
    RaceDate       DATE          NULL,
    Course         NVARCHAR(80)  NULL,
    CourseClean    NVARCHAR(80)  NULL,
    HHMM           NVARCHAR(8)   NULL,
    OffISO         NVARCHAR(40)  NULL,
    Horse          NVARCHAR(90)  NULL,
    HorseClean     NVARCHAR(90)  NULL,
    MarketType     NVARCHAR(8)   NULL,
    Cloth          INT           NULL,
    Weight         NVARCHAR(12)  NULL,
    Form           NVARCHAR(24)  NULL,
    DSLR           INT           NULL,
    Rating         NVARCHAR(20)  NULL,
    Quotes         INT           NULL,
    BookPrice      DECIMAL(9,2)  NULL,
    Bookmaker      NVARCHAR(40)  NULL,
    EWPlaces       INT           NULL,
    EWDenominator  INT           NULL,
    BookPlace      DECIMAL(9,2)  NULL,
    MaxPlaces      INT           NULL,
    BFWin          DECIMAL(9,2)  NULL,
    BFWinLay       DECIMAL(9,2)  NULL,
    BFWinBack      DECIMAL(9,2)  NULL,
    BFPlace        DECIMAL(9,2)  NULL,
    BFPlaceLay     DECIMAL(9,2)  NULL,
    BFPlaceBack    DECIMAL(9,2)  NULL,
    BFTerms        NVARCHAR(12)  NULL,
    BSP            DECIMAL(9,2)  NULL,
    LastTraded     DECIMAL(9,2)  NULL,
    ImportedAt     DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
)
"""

DDL_INDEX = """
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_SnapshotOdds_Key'
               AND object_id = OBJECT_ID('dbo.SnapshotOdds'))
CREATE INDEX IX_SnapshotOdds_Key
    ON dbo.SnapshotOdds (RaceDate, CourseClean, HorseClean, CapturePoint)
"""


def clean(value) -> str:
    """Same join key the rest of the project uses (country suffix stripped)."""
    text = "".join(ch for ch in str(value or "").lower() if ch.isalnum())
    return re.sub(r"(ire|gb|fr|usa|can|ger|ity|spa|aus|nz|jpn|hk)$", "", text) or text


def file_date(name: str):
    try:
        return dt.date.fromisoformat(name[5:15])
    except ValueError:
        return None


def runner_rows(payload, source_file):
    """One tuple per runner for a race/snapshot file (morning or intraday)."""
    scope = payload.get("scope") or "day"
    point = payload.get("capture_point") or ("morning" if scope == "day" else "")
    captured = payload.get("captured_at")
    race_date = payload.get("date")
    out = []
    for race in payload.get("races", []):
        course = race.get("course") or race.get("course_name")
        hhmm = race.get("hhmm")
        off = race.get("start_iso")
        for run in race.get("runners", []):
            out.append((
                source_file, scope, point, captured, race_date,
                course, clean(course), hhmm, off,
                run.get("horse"), clean(run.get("horse")), "WIN",
                run.get("cloth"), run.get("weight"), run.get("form"),
                run.get("dslr"), run.get("rating"), run.get("quotes"),
                run.get("price"), run.get("bookmaker"),
                run.get("places"), run.get("denominator"), run.get("place_price"),
                run.get("max_places"),
                run.get("bf_win"), run.get("bf_win_lay"), run.get("bf_win_back"),
                run.get("bf_place"), run.get("bf_place_lay"), run.get("bf_place_back"),
                run.get("bf_terms"), None, None,
            ))
    return out


def bsp_rows(payload, source_file):
    """One tuple per runner per market for a BSP file (WIN and PLACE)."""
    captured = payload.get("captured_at")
    race_date = payload.get("date")
    out = []
    for market in payload.get("markets", []):
        venue = market.get("venue")
        for run in market.get("runners", []):
            out.append((
                source_file, "bsp", "BSP", captured, race_date,
                market.get("venue"), clean(venue), None, market.get("off_utc"),
                None, run.get("key"), market.get("market_type") or "WIN",
                None, None, None, None, None, None,
                None, None,
                market.get("places"), None, None, None,
                None, None, None, None, None, None, None,
                run.get("bsp"), run.get("last_traded"),
            ))
    return out


INSERT = """
INSERT INTO dbo.SnapshotOdds (
    SourceFile, Scope, CapturePoint, CapturedAt, RaceDate, Course, CourseClean,
    HHMM, OffISO, Horse, HorseClean, MarketType, Cloth, Weight, Form, DSLR,
    Rating, Quotes, BookPrice, Bookmaker, EWPlaces, EWDenominator, BookPlace,
    MaxPlaces, BFWin, BFWinLay, BFWinBack, BFPlace, BFPlaceLay, BFPlaceBack,
    BFTerms, BSP, LastTraded
) VALUES ({})
""".format(", ".join("?" * 33))


def fetch_from_git(repo_dir: str, ref: str = "origin/main"):
    """(name, payload) for every snapshot file in the remote commit.

    Reading blobs out of the fetched ref instead of pulling into the worktree
    means a dirty racing_form.db (which the daily job constantly modifies) cannot
    block the import - `git pull --ff-only` would refuse in that case.
    """
    import subprocess
    def git(*args):
        return subprocess.run(["git", "-C", repo_dir, *args],
                              capture_output=True, text=True)
    fetched = git("fetch", "--quiet", "origin", ref.split("/")[-1])
    if fetched.returncode != 0:
        print(f"  [WARN] git fetch failed: {fetched.stderr.strip()[:120]}")
    listing = git("ls-tree", "-r", "--name-only", ref, "snapshots/")
    if listing.returncode != 0:
        print(f"  [WARN] cannot list {ref}: {listing.stderr.strip()[:120]}")
        return []
    out = []
    for path in listing.stdout.splitlines():
        name = os.path.basename(path.strip())
        if not (name.startswith("odds_") and name.endswith(".json")):
            continue
        blob = git("show", f"{ref}:{path.strip()}")
        if blob.returncode != 0:
            continue
        try:
            out.append((name, json.loads(blob.stdout)))
        except json.JSONDecodeError:
            print(f"  [WARN] {name} is not valid JSON in {ref}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Import cloud odds snapshots into PRODB.")
    ap.add_argument("--snap-dir", default=SNAP_DIR)
    ap.add_argument("--repo", default=os.path.join(PROJECT, "cloud_app"),
                    help="git repo holding the snapshots (with --from-git)")
    ap.add_argument("--ref", default="origin/main", help="git ref to read (with --from-git)")
    ap.add_argument("--from-git", action="store_true",
                    help="read snapshots out of the remote ref instead of the worktree")
    ap.add_argument("--since-days", type=int, default=None,
                    help="only files for the last N days")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-import files already recorded")
    args = ap.parse_args()

    import pyodbc

    if args.from_git:
        sources = fetch_from_git(args.repo, args.ref)
        print(f"{len(sources)} snapshot file(s) in {args.ref}")
    else:
        files = sorted(glob.glob(os.path.join(args.snap_dir, "odds_*.json")))
        sources = []
        for path in files:
            try:
                with open(path, encoding="utf-8") as fh:
                    sources.append((os.path.basename(path), json.load(fh)))
            except Exception as exc:
                print(f"  {os.path.basename(path)}: unreadable ({str(exc)[:60]})")
        if not files:
            print(f"no snapshot files in {args.snap_dir}")
            print("   run: git -C cloud_app pull --ff-only")
            return 0

    if args.since_days:
        cutoff = dt.date.today() - dt.timedelta(days=args.since_days)
        sources = [(n, p) for n, p in sources
                   if (file_date(n) or cutoff) >= cutoff]

    con = pyodbc.connect(PRO)
    cur = con.cursor()
    cur.execute(DDL_FILES)
    cur.execute(DDL_ROWS)
    cur.execute(DDL_INDEX)
    con.commit()

    cur.execute("SELECT SourceFile FROM dbo.SnapshotFiles")
    done = {row[0] for row in cur.fetchall()}
    print(f"{len(sources)} snapshot file(s) to consider, {len(done)} already imported")

    imported = skipped = rows_total = failed = 0
    for name, payload in sources:
        if name in done and not args.force:
            skipped += 1
            continue

        rows = bsp_rows(payload, name) if payload.get("scope") == "bsp" \
            else runner_rows(payload, name)
        if not rows:
            print(f"  {name}: nothing to import")
            failed += 1
            continue
        if args.dry_run:
            print(f"  would import {len(rows):>5} rows  {name}"
                  f"  ({payload.get('capture_point') or 'morning'} {payload.get('date')})")
            imported += 1
            rows_total += len(rows)
            continue

        if args.force or name in done:
            cur.execute("DELETE FROM dbo.SnapshotOdds WHERE SourceFile = ?", name)
            cur.execute("DELETE FROM dbo.SnapshotFiles WHERE SourceFile = ?", name)
        cur.executemany(INSERT, rows)
        cur.execute(
            "INSERT INTO dbo.SnapshotFiles (SourceFile, Scope, CapturePoint, "
            "CapturedAt, RaceDate, RowsImported) VALUES (?,?,?,?,?,?)",
            name, payload.get("scope") or "day", payload.get("capture_point"),
            payload.get("captured_at"), payload.get("date"), len(rows))
        con.commit()
        imported += 1
        rows_total += len(rows)
        print(f"  {len(rows):>5} rows  {name}")

    print(f"imported {imported} file(s), {rows_total:,} rows"
          f"   skipped {skipped} already done"
          + (f"   FAILED {failed}" if failed else ""))
    if imported and not args.dry_run:
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT RaceDate) FROM dbo.SnapshotOdds")
        total, days = cur.fetchone()
        print(f"dbo.SnapshotOdds now holds {total:,} rows across {days} day(s)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
