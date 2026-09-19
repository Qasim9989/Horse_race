r"""How many races AND rows still need weights (both DBs).

Reporting whole empty races alone understates the job: a race can have 11 of
12 runners filled.  So this reports rows missing as well.

    python scripts\weight_progress.py
"""
import pyodbc

total_rows = total_missing = total_races = 0
for db in ("RACINGTV_2023_2026", "SCRAPED_PRODB"):
    c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                       "Server=(localdb)\\MSSQLLocalDB;Database=" + db +
                       ";Trusted_Connection=yes;Connection Timeout=60;")
    cur = c.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT RaceDate, RaceTime, CourseName
            FROM dbo.Scraped_Results
            WHERE RaceDate BETWEEN '2021-01-01' AND '2026-09-14'
            GROUP BY RaceDate, RaceTime, CourseName
            HAVING COUNT(Weight) = 0) x
    """)
    empty_races = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT RaceDate, RaceTime, CourseName
            FROM dbo.Scraped_Results
            WHERE RaceDate BETWEEN '2021-01-01' AND '2026-09-14'
            GROUP BY RaceDate, RaceTime, CourseName
            HAVING COUNT(Weight) < COUNT(*)) x
    """)
    part_races = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*), COUNT(Weight) FROM dbo.Scraped_Results
        WHERE RaceDate BETWEEN '2021-01-01' AND '2026-09-14'
    """)
    rows, with_w = cur.fetchone()
    pct = (rows - with_w) / max(rows, 1) * 100
    print(f"{db}: rows {rows:,}  with weight {with_w:,}  "
          f"missing {rows - with_w:,} ({pct:.2f}%)")
    print(f"    races with NO weights : {empty_races:,}")
    print(f"    races with SOME missing: {part_races:,}")
    total_rows += rows
    total_missing += rows - with_w
    total_races += part_races
    c.close()
print(f"\nTOTAL rows {total_rows:,}  missing {total_missing:,} "
      f"({total_missing / max(total_rows, 1) * 100:.2f}%)  "
      f"races incomplete {total_races:,}")
