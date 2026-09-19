r"""Where do the morning / evening / BSP price series live?

Lists BFSP's own columns, then every column in PRODB whose name looks like a
time-of-day price (morning, evening, night, early, pre-post).

    python scripts\price_cols_check.py
"""
from __future__ import annotations

import pyodbc

PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=300;"
       "MultipleActiveResultSets=True;")

c = pyodbc.connect(PRO)
cur = c.cursor()

cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='BFSP' ORDER BY ORDINAL_POSITION")
print("BFSP columns:")
for name, typ in cur.fetchall():
    print(f"  {name:<22} {typ}")

print("\nprice-looking columns anywhere in PRODB:")
cur.execute("""
    SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
    WHERE COLUMN_NAME LIKE '%WAP%' OR COLUMN_NAME LIKE '%Eve%'
       OR COLUMN_NAME LIKE '%Night%' OR COLUMN_NAME LIKE '%Early%'
       OR COLUMN_NAME LIKE '%Morn%' OR COLUMN_NAME LIKE '%PP%'
       OR COLUMN_NAME LIKE '%SP%'
    ORDER BY TABLE_NAME, COLUMN_NAME
""")
for table, col in cur.fetchall():
    print(f"  {table:<28} {col}")

print("\ntables holding prices (name contains price/odds/sp/book):")
cur.execute("""
    SELECT DISTINCT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME LIKE '%Price%' OR TABLE_NAME LIKE '%Odds%'
       OR TABLE_NAME LIKE '%SP%' OR TABLE_NAME LIKE '%Book%'
       OR TABLE_NAME LIKE '%Snap%'
    ORDER BY TABLE_NAME
""")
for (table,) in cur.fetchall():
    print(f"  {table}")

print("\nNEW_HIR keys and price columns:")
cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='NEW_HIR' ORDER BY ORDINAL_POSITION")
hir_cols = cur.fetchall()
for name, typ in hir_cols[:14]:
    print(f"  {name:<26} {typ}")
prices = [c for c, _ in hir_cols if c.startswith(("HIR_Even", "HIR_Morn",
                                                  "HIR_BSP", "HIR_BPSP"))]
print("  price columns:", prices)

print("\nNEW_HIR price coverage:")
cur.execute("""
    SELECT COUNT(*) AS rows_,
           SUM(CASE WHEN HIR_EveningPrice  IS NOT NULL
                     AND HIR_EveningPrice  > 0 THEN 1 ELSE 0 END) AS evening,
           SUM(CASE WHEN HIR_MorningPrice  IS NOT NULL
                     AND HIR_MorningPrice  > 0 THEN 1 ELSE 0 END) AS morning,
           SUM(CASE WHEN HIR_BSP_TRUE      IS NOT NULL
                     AND HIR_BSP_TRUE      > 0 THEN 1 ELSE 0 END) AS bsp
    FROM dbo.NEW_HIR
""")
row = cur.fetchone()
print(f"  rows {row[0]:,}   evening {row[1]:,}   morning {row[2]:,}"
      f"   BSP {row[3]:,}")

print("\nNEW_HIR evening-vs-morning-vs-BSP, recent window:")
cur.execute("""
    SELECT TOP 5 HIR_EveningPrice, HIR_MorningPrice, HIR_BSP_TRUE,
                 HIR_BSP, HIR_RNo, HIR_HNo
    FROM dbo.NEW_HIR
    WHERE HIR_EveningPrice > 0 AND HIR_MorningPrice > 0 AND HIR_BSP_TRUE > 0
    ORDER BY HIR_RNo DESC
""")
for r in cur.fetchall():
    print("  ", r)

c.close()

