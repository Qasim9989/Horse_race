"""
Add the race/runner fields the API returns but the databases never captured.

Ben's rules need these and they were only ever in the API response, thrown
away on every backfill:

  rule 4 (proven at the trip) needs the DISTANCE of every past run
  rules 1-3 (mark falling / below last win / below career best) need a mark
            series; where PRODB's marks stop (2026-05-22) the handicap BAND
            plus the weight reconstructs one

  python scripts\add_field_columns.py
"""
from __future__ import annotations

import sys

import pyodbc

DBS = ("RACINGTV_2023_2026", "SCRAPED_PRODB")
COLS = [
    ("DistanceYards", "INT"),
    ("DistanceText", "NVARCHAR(20)"),
    ("RatingBandTop", "INT"),
    ("RatingBandText", "NVARCHAR(20)"),
    ("RaceClass", "NVARCHAR(10)"),
    ("RaceType", "NVARCHAR(30)"),
    ("TimeformRating", "NVARCHAR(16)"),
    ("FormText", "NVARCHAR(30)"),
    ("DaysSinceRun", "INT"),
    ("ApiSP", "DECIMAL(10, 3)"),
]


def main():
    for db in DBS:
        c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                           "Server=(localdb)\\MSSQLLocalDB;Database=" + db +
                           ";Trusted_Connection=yes;Connection Timeout=60;")
        cur = c.cursor()
        added = []
        for name, typ in COLS:
            cur.execute("SELECT COL_LENGTH('dbo.Scraped_Results', ?)", (name,))
            if cur.fetchone()[0] is None:
                cur.execute(f"ALTER TABLE dbo.Scraped_Results ADD [{name}] "
                            f"{typ} NULL")
                added.append(name)
        c.commit()          # pyodbc autocommit is OFF: DDL needs the commit
        print(f"{db}: added {len(added)} column(s): {', '.join(added) or '-'}")
        cur.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_NAME='Scraped_Results'")
        print(f"    Scraped_Results now has {cur.fetchone()[0]} columns")
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
