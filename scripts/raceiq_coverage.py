r"""Is the FREE RacingTV RaceIQ sectional feed current?

racingtv_db_updater.py scrapes RacingTV's public pages (no API key, no login)
and writes per-horse StrideLength / AvgFrequency / TopSpeed / FinishingSpeedPct
into RACINGTV_2023_2026.dbo.Scraped_RaceIQ.  PRODB.SData stopped on 2026-04-30,
which is what blocked the stride systems, so the question is whether this free
source has data after that date.

    python scripts\raceiq_coverage.py
"""
from __future__ import annotations

import pyodbc

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;"
        r"Database=RACINGTV_2023_2026;"
        r"Trusted_Connection=yes;"
        r"MultipleActiveResultSets=True;")

c = pyodbc.connect(CONN)
cur = c.cursor()

cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME")
print("tables in RACINGTV_2023_2026:", [r[0] for r in cur.fetchall()])

cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='Scraped_RaceIQ' ORDER BY ORDINAL_POSITION")
print("Scraped_RaceIQ columns:", [r[0] for r in cur.fetchall()])

print("\n=== RaceIQ (the free sectional feed) ===")
cur.execute("""
    SELECT COUNT(*), MIN(RaceDate), MAX(RaceDate),
           SUM(CASE WHEN StrideLength IS NOT NULL THEN 1 ELSE 0 END),
           SUM(CASE WHEN TopSpeed IS NOT NULL THEN 1 ELSE 0 END),
           SUM(CASE WHEN FinishingSpeedPct IS NOT NULL THEN 1 ELSE 0 END)
    FROM dbo.Scraped_RaceIQ
""")
row = cur.fetchone()
if row:
    print(f"rows            : {row[0]:,}")
    print(f"date range      : {row[1]} to {row[2]}")
    print(f"with stride len : {row[3] or 0:,}")
    print(f"with top speed  : {row[4] or 0:,}")
    print(f"with FSP        : {row[5] or 0:,}")

print("\n=== results table, for comparison ===")
cur.execute("SELECT COUNT(*), MIN(RaceDate), MAX(RaceDate) "
            "FROM dbo.Scraped_Results")
for r in cur.fetchall():
    print(f"rows {r[0]:,}   dates {r[1]} to {r[2]}")

print("\n=== RaceIQ rows per month, latest 10 months ===")
cur.execute("""
    SELECT FORMAT(RaceDate, 'yyyy-MM') AS m, COUNT(*) AS rows_,
           COUNT(DISTINCT CONCAT(CourseName, RaceTime)) AS races
    FROM dbo.Scraped_RaceIQ
    GROUP BY FORMAT(RaceDate, 'yyyy-MM')
    ORDER BY m DESC
    OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY
""")
for m, rows_, races in cur.fetchall():
    print(f"  {m}   rows {rows_:>7,}   races {races:>6,}")

print("\n=== RaceIQ v2 (raceiq_scrape_v2.py - the live source of truth) ===")
print("The cloud DB's telemetry is built from this table: v1 is a stride-only")
print("fallback for older dates because 35% of its TopSpeed reads are junk.")
try:
    cur.execute("""
        SELECT COUNT(*), MIN(RaceDate), MAX(RaceDate),
               SUM(CASE WHEN StrideM IS NOT NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN TopSpeedMph IS NOT NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN FspPct IS NOT NULL THEN 1 ELSE 0 END)
        FROM dbo.Scraped_RaceIQ_v2
    """)
    row = cur.fetchone()
    if row and row[0]:
        print(f"rows            : {row[0]:,}")
        print(f"date range      : {row[1]} to {row[2]}")
        print(f"with stride (m) : {row[3] or 0:,}")
        print(f"with top speed  : {row[4] or 0:,}")
        print(f"with FSP        : {row[5] or 0:,}")
    else:
        print("no rows yet - run scripts/raceiq_scrape_v2.py --date YYYY-MM-DD")
except pyodbc.Error as exc:
    print(f"table not available: {exc}")

print("\n=== is the untouched 2026-05-01.. window covered? ===")
cur.execute("""
    SELECT COUNT(*) AS rows_, COUNT(DISTINCT CONCAT(CourseName, RaceTime)) AS races
    FROM dbo.Scraped_RaceIQ WHERE RaceDate >= '2026-05-01'
""")
for rows_, races in cur.fetchall():
    print(f"  RaceIQ rows since 2026-05-01: {rows_:,} over {races:,} races")

c.close()
