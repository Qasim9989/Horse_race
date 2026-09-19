"""
Add the index the weight backfill needs.

Every write is

    UPDATE dbo.Scraped_Results SET Weight=...
    WHERE RaceDate=? AND RaceTime=? AND CourseName=? AND HorseName=?

With no index on those columns each of ~800,000 runner updates full-scans the
table (675k rows in SCRAPED_PRODB).  That is why both backfills crawled: the
browser version managed 30 races/min for the same reason.  One index turns
each write into a seek.

  python scripts/add_results_index.py
"""
from __future__ import annotations

import sys

import pyodbc

DBS = ("RACINGTV_2023_2026", "SCRAPED_PRODB")
COLS = "RaceDate, CourseName, RaceTime, HorseName"
IDX = "IX_Scraped_Results_RaceHorse"


def main():
    for db in DBS:
        c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                           "Server=(localdb)\\MSSQLLocalDB;Database=" + db +
                           ";Trusted_Connection=yes;Connection Timeout=30;")
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM sys.indexes WHERE name = ?", (IDX,))
        if cur.fetchone()[0]:
            print(f"{db}: {IDX} already exists")
            c.close()
            continue
        cur.execute("SELECT COUNT(*) FROM dbo.Scraped_Results")
        n = cur.fetchone()[0]
        print(f"{db}: {n:,} rows, creating {IDX} on ({COLS}) ...")
        cur.execute(f"CREATE NONCLUSTERED INDEX {IDX} ON dbo.Scraped_Results "
                    f"({COLS})")
        c.commit()          # pyodbc autocommit is OFF: DDL needs the commit
        cur.execute("SELECT COUNT(*) FROM sys.indexes WHERE name = ?", (IDX,))
        print(f"{db}: index present = {bool(cur.fetchone()[0])}")
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
