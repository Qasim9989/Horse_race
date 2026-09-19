"""
DO WE HAVE WHAT BEN'S SYSTEM NEEDS?
===================================
Rule 1 (mark falling = OR_now < OR_LTO) needs the official mark from each
horse's most recent run.  Every OR in this project comes from PRODB, which
stopped on 2026-05-22.  So the size of the hole is: how much racing has
happened since, and how many horses does it affect?
"""
import pyodbc

SCRAPED = ("Driver={ODBC Driver 17 for SQL Server};"
           "Server=(localdb)\\MSSQLLocalDB;Database=RACINGTV_2023_2026;"
           "Trusted_Connection=yes;Connection Timeout=60;")
PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=60;")

c = pyodbc.connect(PRO)
cur = c.cursor()
cur.execute("SELECT MAX(RH_DateTime) FROM dbo.NEW_RH")
pro_max = cur.fetchone()[0]
print(f"PRODB (marks) last race:      {pro_max}")
cur.execute("SELECT COUNT(*) FROM dbo.BFSP")
print(f"real BSP rows available:      {cur.fetchone()[0]:,}")
c.close()

c = pyodbc.connect(SCRAPED)
cur = c.cursor()
cur.execute("""
    SELECT COUNT(*), COUNT(DISTINCT HorseName) FROM dbo.Scraped_Results
    WHERE RaceDate > '2026-05-22'
""")
runs, horses = cur.fetchone()
print(f"\nracing since PRODB stopped (2026-05-23 -> today):")
print(f"   runner appearances:        {runs:,}")
print(f"   distinct horses:           {horses:,}")
cur.execute("""
    SELECT COUNT(*) FROM (
        SELECT HorseName, MAX(RaceDate) AS last_run
        FROM dbo.Scraped_Results
        GROUP BY HorseName) x
    WHERE last_run > '2026-05-22'
""")
recent = cur.fetchone()[0]
print(f"   horses whose LAST run is after the PRODB cut-off "
      f"(rule 1 blocked): {recent:,}")
cur.execute("""
    SELECT COUNT(*), COUNT(Weight) FROM dbo.Scraped_Results
    WHERE RaceDate > '2026-05-22'
""")
rows, w = cur.fetchone()
print(f"   weight coverage of that window: {w:,}/{rows:,} "
      f"({w / max(rows, 1) * 100:.1f}%)")
c.close()
