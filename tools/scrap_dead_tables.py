"""
SCRAP DEAD TABLES
=================
Drops database tables that nothing in the live code reads any more, but never
without writing the rows out first: every table is dumped to CSV under
archive/audit_<date>/dropped_tables/ before the DROP runs, so "scrapped" still
means recoverable.

  python tools/scrap_dead_tables.py            # report only
  python tools/scrap_dead_tables.py --apply    # dump then drop

Two classes of table are scrapped:

* output of systems that were archived because they failed at real prices
  (Scored_Racecards_LaySheet / Scored_Racecards_B2L)
* tables that hold no rows at all (schema clutter, zero data to lose)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys

import pyodbc

BASE = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=")
ROOT = r"E:\Test\racing-form-system"
ARCHIVE = os.path.join(ROOT, "archive", f"audit_{dt.date.today()}",
                       "dropped_tables")
DBS = ("PRODB", "SCRAPED_PRODB", "RACINGTV_2023_2026")
DEAD_SYSTEM = ("Scored_Racecards_LaySheet", "Scored_Racecards_B2L")

# legacy SQL Server 2000 diagram table: zero benefit to dropping, slight risk
NEVER_DROP = {"dtproperties"}


def connect(db):
    return pyodbc.connect(BASE + db +
                          ";Trusted_Connection=yes;Connection Timeout=30;")


def dump(db, table, rows, cols):
    os.makedirs(ARCHIVE, exist_ok=True)
    path = os.path.join(ARCHIVE, f"{db}.{table}.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    return path


def code_corpus():
    """All live .py/.bat text, archive excluded: if a table is named anywhere
    in it, the table is not dead and must not be scrapped.

    The audit/scrap tools themselves are excluded - their DEAD_HINTS and
    DEAD_SYSTEM lists name dead tables on purpose and would otherwise make
    every candidate block itself.
    """
    parts = []
    policy = {os.path.normcase(os.path.join(ROOT, "tools", "audit_db.py")),
              os.path.normcase(os.path.join(ROOT, "tools",
                                             "scrap_dead_tables.py"))}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in ("archive", "__pycache__", ".git")]
        for fn in filenames:
            if fn.endswith((".py", ".bat", ".cmd")):
                p = os.path.normcase(os.path.join(dirpath, fn))
                if p in policy:
                    continue
                try:
                    with open(p, encoding="utf-8", errors="ignore") as fh:
                        parts.append(fh.read().lower())
                except OSError:
                    pass
    return "\n".join(parts)


def references(t, corpus):
    """True only if a table is used as a table - `dbo.X`, FROM/JOIN/INTO/UPDATE X.

    Plain substring matching gave false positives: the string "BetfairSP"
    appears in the Betfair URL promo.betfair.com/betfairsp/prices, and "Notes"
    appears as a docstring heading.
    """
    t = t.lower()
    pats = (rf"dbo\.{re.escape(t)}\b", rf"\bfrom\s+{re.escape(t)}\b",
            rf"\bjoin\s+{re.escape(t)}\b", rf"\binto\s+{re.escape(t)}\b",
            rf"\bupdate\s+{re.escape(t)}\b")
    return any(re.search(p, corpus) for p in pats)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    corpus = code_corpus()
    manifest = []
    for db in DBS:
        try:
            c = connect(db)
        except Exception as e:
            print(f"{db}: cannot connect ({str(e)[:70]})")
            continue
        cur = c.cursor()
        cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME")
        for (t,) in cur.fetchall():
            try:
                cur.execute(f"SELECT COUNT(*) FROM dbo.{t}")
                n = cur.fetchone()[0]
            except pyodbc.Error:
                continue
            empty = n == 0
            dead_system = any(d in t for d in DEAD_SYSTEM)
            if not (empty or dead_system):
                continue
            why = "empty" if empty else "archived system"
            if references(t, corpus):
                print(f"  SKIP  {db}.dbo.{t:<30} used by live code")
                continue
            if t in NEVER_DROP:
                print(f"  SKIP  {db}.dbo.{t:<30} on the never-drop list")
                continue
                continue
            if not a.apply:
                print(f"  would scrap {db}.dbo.{t:<28} rows={n:<7} ({why})")
                continue
            cur.execute(f"SELECT * FROM dbo.{t}")
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            path = dump(db, t, rows, cols) if rows else ""
            cur.execute(f"DROP TABLE dbo.{t}")
            c.commit()
            manifest.append((db, t, n, os.path.relpath(path, ROOT)
                             if path else "(empty, nothing to dump)"))
            print(f"  dropped {db}.dbo.{t:<30} rows={n:<7} {why}")
        c.close()
    if a.apply and manifest:
        mf = os.path.join(ARCHIVE, "MANIFEST.csv")
        with open(mf, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["database", "table", "rows", "csv_backup"])
            w.writerows(manifest)
        print(f"\n{len(manifest)} table(s) scrapped, data in "
              f"{os.path.relpath(ARCHIVE, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
