"""
RECON: stride/IQ data and odds-by-time-of-day
=============================================
Questions:
  1. What stride / pace data exists, where, and how much of it is populated?
  2. Do we have odds at different times of day (evening / morning / BSP)?
  3. How many runners have BOTH stride data and a real BSP -> sample size.
"""
import pyodbc

PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=60;")
for_db = ("Driver={ODBC Driver 17 for SQL Server};"
          "Server=(localdb)\\MSSQLLocalDB;Database={};"
          "Trusted_Connection=yes;Connection Timeout=60;")

c = pyodbc.connect(PRO)
cur = c.cursor()

print("=== PRODB tables that might hold prices or stride/pace ===")
cur.execute("""
    SELECT t.name,
           SUM(p.rows) AS rows_
    FROM sys.tables t
    JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1)
    WHERE t.name LIKE '%od%' OR t.name LIKE '%rice%' OR t.name LIKE '%SP%'
       OR t.name LIKE '%stride%' OR t.name LIKE '%IQ%' OR t.name LIKE '%pace%'
       OR t.name LIKE '%TS_%' OR t.name LIKE '%HIR%'
    GROUP BY t.name ORDER BY rows_ DESC
""")
for n, r in cur.fetchall():
    print(f"   {n:<28} {r:>10,}")

print("\n=== NEW_HIR columns mentioning pace/stride/speed ===")
cur.execute("""
    SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME='NEW_HIR'
      AND (COLUMN_NAME LIKE '%Pace%' OR COLUMN_NAME LIKE '%Stride%'
           OR COLUMN_NAME LIKE '%Speed%' OR COLUMN_NAME LIKE '%Freq%')
""")
cols = cur.fetchall()
print("   " + (", ".join(f"{n}({t})" for n, t in cols) or "none"))
for n, _ in cols:
    cur.execute(f"SELECT COUNT(*) FROM dbo.NEW_HIR WHERE [{n}] IS NOT NULL")
    print(f"      {n:<28} populated: {cur.fetchone()[0]:,}")
c.close()

print("\n=== Scraped_RaceIQ (sectionals: stride etc.) in both scrape DBs ===")
for db in ("RACINGTV_2023_2026", "SCRAPED_PRODB"):
    c = pyodbc.connect(for_db.format(db))
    cur = c.cursor()
    cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME='Scraped_RaceIQ' ORDER BY ORDINAL_POSITION")
    names = [r[0] for r in cur.fetchall()]
    print(f"\n   {db} Scraped_RaceIQ columns: {', '.join(names)}")
    cur.execute("SELECT COUNT(*), MIN(RaceDate), MAX(RaceDate) "
                "FROM dbo.Scraped_RaceIQ")
    n, lo, hi = cur.fetchone()
    print(f"      rows {n:,}   {str(lo)[:10]} -> {str(hi)[:10]}")
    for col in ("StrideLength", "AvgFrequency", "TopSpeed",
                "FinishingSpeedPct"):
        if col in names:
            cur.execute(f"SELECT COUNT([{col}]) FROM dbo.Scraped_RaceIQ")
            print(f"      {col:<20} populated {cur.fetchone()[0]:,}/{n:,}")
    c.close()

print("\n=== runners that have BOTH stride data and a real BSP ===")
c = pyodbc.connect(for_db.format("SCRAPED_PRODB"))
cur = c.cursor()
cur.execute("""
    SELECT COUNT(*) FROM dbo.Scraped_RaceIQ IQ
    WHERE IQ.StrideLength IS NOT NULL
""")
iq = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM dbo.BFSP")
try:
    bsp = cur.fetchone()[0]
except Exception:
    bsp = "n/a"
print(f"   Scraped_RaceIQ rows with stride: {iq:,}   BFSP rows: {bsp}")
c.close()
