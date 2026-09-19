"""Where do the MARKS stop (as opposed to the results)?"""
import pyodbc

PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=60;")
SCRAPED = ("Driver={ODBC Driver 17 for SQL Server};"
           "Server=(localdb)\\MSSQLLocalDB;Database=RACINGTV_2023_2026;"
           "Trusted_Connection=yes;Connection Timeout=60;")

c = pyodbc.connect(PRO)
cur = c.cursor()
cur.execute("""
    SELECT MAX(RH.RH_DateTime) AS last_race_with_marks,
           COUNT(*) AS mark_rows
    FROM dbo.NEW_HIR HIR JOIN dbo.NEW_RH RH ON RH.RH_RNo = HIR.HIR_RNo
    WHERE HIR.HIR_OfficialRating > 0
""")
print("marks (HIR_OfficialRating>0):", cur.fetchone())
for cut in ("2026-05-22", "2026-08-11"):
    cur.execute("""
        SELECT COUNT(*) FROM dbo.NEW_HIR HIR
        JOIN dbo.NEW_RH RH ON RH.RH_RNo = HIR.HIR_RNo
        WHERE HIR.HIR_OfficialRating > 0 AND RH.RH_DateTime > ?
    """, (cut,))
    print(f"   mark rows after {cut}: {cur.fetchone()[0]:,}")
cur.execute("SELECT MAX(RH_DateTime) FROM dbo.NEW_RH")
print("results (NEW_RH) last race:", cur.fetchone()[0])
c.close()

c = pyodbc.connect(SCRAPED)
cur = c.cursor()
for cut in ("2026-05-22", "2026-08-11"):
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT HorseName) "
                "FROM dbo.Scraped_Results WHERE RaceDate > ?", (cut,))
    runs, horses = cur.fetchone()
    print(f"\nracing after {cut}: {runs:,} runner appearances, "
          f"{horses:,} horses")
c.close()
