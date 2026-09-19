"""Coverage of the race/runner fields Ben's rules need, per DB and per year.

    python scripts\field_coverage.py
"""
import pyodbc

Q = """
SELECT YEAR(RaceDate) AS y,
       COUNT(*) AS rows_,
       COUNT(Weight) AS weight,
       COUNT(DistanceYards) AS dist,
       COUNT(RatingBandTop) AS band,
       COUNT(TimeformRating) AS tf,
       COUNT(FormText) AS form,
       COUNT(DaysSinceRun) AS days,
       COUNT(ApiSP) AS sp
FROM dbo.Scraped_Results
WHERE RaceDate BETWEEN '2021-01-01' AND '2026-09-14'
GROUP BY YEAR(RaceDate) ORDER BY y
"""
for db in ("RACINGTV_2023_2026", "SCRAPED_PRODB"):
    c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                       "Server=(localdb)\\MSSQLLocalDB;Database=" + db +
                       ";Trusted_Connection=yes;Connection Timeout=60;")
    cur = c.cursor()
    cur.execute(Q)
    rows = cur.fetchall()
    print(f"\n--- {db} ---")
    print("  year     rows   weight  distance   band   tform    form    days"
          "    apiSP")
    for r in rows:
        y, n = r[0], r[1]
        print(f"  {y}  {n:>7,}  " + "  ".join(f"{v:>7,}" for v in r[2:]))
    c.close()
