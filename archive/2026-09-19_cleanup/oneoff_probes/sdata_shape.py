"""SData: column families, coverage, and a sample horse."""
import collections

import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=180;"
                   "MultipleActiveResultSets=True;")
cur = c.cursor()

cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='SData' ORDER BY ORDINAL_POSITION")
cols = [r[0] for r in cur.fetchall()]
fam: collections.Counter[str] = collections.Counter()
for n in cols:
    fam[n.split("_")[0]] += 1
print("column families in SData:", dict(fam.most_common(20)))
print(f"total columns: {len(cols)}")

cur.execute("SELECT COUNT(*) FROM dbo.SData")
print(f"\nrows: {cur.fetchone()[0]:,}")
cur.execute("SELECT COUNT(DISTINCT SD_RNo), COUNT(DISTINCT SD_HNo) FROM dbo.SData")
r, h = cur.fetchone()
print(f"distinct races: {r:,}   distinct horses: {h:,}")

print("\ncoverage by year (via NEW_RH race date):")
cur.execute("""
    SELECT YEAR(RH.RH_DateTime) AS y, COUNT(*) AS rows_,
           COUNT(DISTINCT S.SD_RNo) AS races
    FROM dbo.SData S JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.SD_RNo
    GROUP BY YEAR(RH.RH_DateTime) ORDER BY y
""")
rows = cur.fetchall()
if rows:
    for y, n, rc in rows:
        print(f"   {y}  {n:>8,} rows   {rc:>6,} races")
else:
    print("   no rows joined NEW_RH on SD_RNo")

print("\nSTRK_* population (stride per section):")
for n in ["STRK_Finish", "STRK_1", "STRK_2", "STRK_5", "STRK_10", "STRK_16"]:
    if n in cols:
        cur.execute(f"SELECT COUNT([{n}]) FROM dbo.SData")
        print(f"   {n:<12} {cur.fetchone()[0]:,}")

print("\none horse's stride profile across its sections:")
cur.execute("""SELECT TOP 1 SD_RNo, SD_HNo, ST_Finish, STRK_Finish,
                      ST_1, STRK_1, ST_2, STRK_2, ST_3, STRK_3
               FROM dbo.SData WHERE STRK_Finish IS NOT NULL""")
names = [d[0] for d in cur.description]
print("   ", dict(zip(names, cur.fetchone(), strict=False)))
c.close()
