"""
RECON 4: confirm the IR_Prices time series semantics and coverage
"""
import pyodbc

BASE = ("Driver={ODBC Driver 17 for SQL Server};"
        "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
        "Trusted_Connection=yes;Connection Timeout=120;"
        "MultipleActiveResultSets=True;")
c = pyodbc.connect(BASE)
cur = c.cursor()

cur.execute("""
    SELECT COUNT(*) AS rows_,
           COUNT(DISTINCT IR_RNo) AS races,
           COUNT(DISTINCT IR_HNo) AS horses,
           MIN(RH.RH_DateTime) AS first_race,
           MAX(RH.RH_DateTime) AS last_race
    FROM dbo.IR_Prices P JOIN dbo.NEW_RH RH ON RH.RH_RNo = P.IR_RNo
""")
r = cur.fetchone()
print("IR_Prices joined to races:")
print(f"   price rows {r[0]:,}   races {r[1]:,}   horses {r[2]:,}")
print(f"   {r[3]} -> {r[4]}")

# how many of the 20 slots are actually used, on average
cur.execute("""
    SELECT AVG(CAST(filled AS float)), MIN(filled), MAX(filled)
    FROM (
        SELECT (CASE WHEN IR_Low1 > 0 THEN 1 ELSE 0 END)
             + (CASE WHEN IR_Low2 > 0 THEN 1 ELSE 0 END)
             + (CASE WHEN IR_Low5 > 0 THEN 1 ELSE 0 END)
             + (CASE WHEN IR_Low10 > 0 THEN 1 ELSE 0 END)
             + (CASE WHEN IR_Low15 > 0 THEN 1 ELSE 0 END)
             + (CASE WHEN IR_Low20 > 0 THEN 1 ELSE 0 END) AS filled
        FROM dbo.IR_Prices) x
""")
print(f"\n   sampled slots filled per row: avg {cur.fetchone()}")

print("\n   IR_Interval, most common values:")
cur.execute("""SELECT TOP 5 IR_Interval, COUNT(*) FROM dbo.IR_Prices
               GROUP BY IR_Interval ORDER BY COUNT(*) DESC""")
for v, n in cur.fetchall():
    print(f"      {v!r:<24} {n:,}")

print("\n   one race's price ladder (winner vs a drifter), first 12 slots:")
cur.execute("""
    SELECT TOP 2 P.IR_HNo, HIR.HIR_PositionNo, P.IR_Lowest, P.IR_Highest,
           P.IR_Low1, P.IR_High1, P.IR_Low2, P.IR_High2, P.IR_Low5, P.IR_High5,
           P.IR_Low10, P.IR_High10, P.IR_Low15, P.IR_High15
    FROM dbo.IR_Prices P
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_HNo = P.IR_HNo AND HIR.HIR_RNo = P.IR_RNo
    WHERE P.IR_RNo = 87533
    ORDER BY CAST(HIR.HIR_PositionNo AS int)
""")
for row in cur.fetchall():
    (hno, pos, lo, hi, l1, h1, l2, h2, l5, h5, l10, h10, l15, h15) = row
    print(f"      horse {hno} pos {pos}: overall {lo}-{hi} | "
          f"slot1 {l1}-{h1} slot2 {l2}-{h2} slot5 {l5}-{h5} "
          f"slot10 {l10}-{h10} slot15 {l15}-{h15}")

# overlap between stride data and price data
cur.execute("""
    SELECT COUNT(*) FROM dbo.NEW_TPD_STRIDE S
    JOIN dbo.IR_Prices P ON P.IR_RNo = S.RH_RNo AND P.IR_HNo = S.HIR_HNo
""")
print(f"\n   stride rows that also have price rows: {cur.fetchone()[0]:,}")
cur.execute("""
    SELECT COUNT(*) FROM dbo.NEW_TPD_STRIDE S
    JOIN dbo.BFSP B ON B.BF_RNo = S.RH_RNo
""")
try:
    print(f"   stride rows with a real BSP on the same race: {cur.fetchone()[0]:,}")
except Exception as e:
    print(f"   BFSP join failed: {e}")
c.close()
