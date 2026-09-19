"""Pending weights by year and by date range - where the remaining work is."""
import pyodbc

for db in ("SCRAPED_PRODB", "RACINGTV_2023_2026"):
    c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                       "Server=(localdb)\\MSSQLLocalDB;Database=" + db +
                       ";Trusted_Connection=yes;Connection Timeout=60;")
    cur = c.cursor()
    cur.execute("""
        SELECT YEAR(RaceDate) AS y,
               COUNT(*) AS races_without_weights
        FROM (SELECT RaceDate, RaceTime, CourseName
              FROM dbo.Scraped_Results
              WHERE RaceDate BETWEEN '2021-01-01' AND '2026-09-14'
              GROUP BY RaceDate, RaceTime, CourseName
              HAVING COUNT(Weight) = 0) x
        GROUP BY YEAR(RaceDate) ORDER BY y
    """)
    print(f"--- {db} ---")
    tot = 0
    for y, n in cur.fetchall():
        print(f"   {y}: {n:,} races still need weights")
        tot += n
    print(f"   total {tot:,}")
    c.close()
