"""Which RACE dates does the stride data cover (not load dates)?"""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=180;"
                   "MultipleActiveResultSets=True;")
cur = c.cursor()

cur.execute("""
    SELECT YEAR(RH.RH_DateTime) AS y, MONTH(RH.RH_DateTime) AS m,
           COUNT(*) AS stride_rows, COUNT(DISTINCT S.RH_RNo) AS races
    FROM dbo.NEW_TPD_STRIDE S
    JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.RH_RNo
    GROUP BY YEAR(RH.RH_DateTime), MONTH(RH.RH_DateTime)
    ORDER BY y, m
""")
print("stride rows by RACE month:")
for y, m, n, r in cur.fetchall():
    print(f"   {y}-{m:02d}   {n:>7,} rows   {r:>5,} races")

cur.execute("""
    SELECT MIN(RH.RH_DateTime), MAX(RH.RH_DateTime)
    FROM dbo.NEW_TPD_STRIDE S JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.RH_RNo
""")
print("race-date span:", cur.fetchone())

print("\noverlap with BFSP (which spans 2020-12-31 -> 2026-09-15):")
cur.execute("""
    SELECT COUNT(*) FROM dbo.NEW_TPD_STRIDE S
    JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.RH_RNo
    WHERE RH.RH_DateTime >= '2020-12-31'
""")
print("   stride rows on race dates BFSP covers:", cur.fetchone()[0])
c.close()
