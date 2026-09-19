"""
RECON 5: does the stride data connect to the price data, and to results?
"""
import pyodbc

BASE = ("Driver={ODBC Driver 17 for SQL Server};"
        "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
        "Trusted_Connection=yes;Connection Timeout=120;"
        "MultipleActiveResultSets=True;")
c = pyodbc.connect(BASE)
cur = c.cursor()

print("=== stride table: dates and ids ===")
cur.execute("""
    SELECT MIN(RH.RH_DateTime), MAX(RH.RH_DateTime), COUNT(*),
           COUNT(DISTINCT S.RH_RNo)
    FROM dbo.NEW_TPD_STRIDE S JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.RH_RNo
""")
print("   ", cur.fetchone())

cur.execute("""SELECT TOP 5 S.RH_RNo, S.HIR_HNo, RH.RH_DateTime
               FROM dbo.NEW_TPD_STRIDE S
               JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.RH_RNo
               ORDER BY RH.RH_DateTime DESC""")
print("   newest stride rows (RH_RNo, HIR_HNo, when):")
for r in cur.fetchall():
    print("     ", r)

print("\n=== do those stride races exist in IR_Prices? ===")
cur.execute("""
    SELECT COUNT(DISTINCT S.RH_RNo)
    FROM dbo.NEW_TPD_STRIDE S
    WHERE EXISTS (SELECT 1 FROM dbo.IR_Prices P WHERE P.IR_RNo = S.RH_RNo)
""")
print(f"   stride races present in IR_Prices: {cur.fetchone()[0]}")

print("\n=== does the horse id space match? sample HIR_HNo vs IR_HNo ===")
cur.execute("""SELECT TOP 3 HIR_HNo, (SELECT COUNT(*) FROM dbo.IR_Prices P
                                           WHERE P.IR_HNo = HIR_HNo)
               FROM dbo.NEW_TPD_STRIDE HIR """)
for r in cur.fetchall():
    print("     stride HIR_HNo", r[0], "-> IR_Prices rows:", r[1])

print("\n=== BFSP schema ===")
cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='BFSP' ORDER BY ORDINAL_POSITION")
print("   ", [r[0] for r in cur.fetchall()])
cur.execute("SELECT TOP 2 * FROM dbo.BFSP")
names = [d[0] for d in cur.description]
for row in cur.fetchall():
    print("    sample:", dict(zip(names, [str(v)[:18] for v in row], strict=False)))

print("\n=== stride rows joined to their runner row in NEW_HIR ===")
cur.execute("""
    SELECT COUNT(*)
    FROM dbo.NEW_TPD_STRIDE S
    JOIN dbo.NEW_HIR H ON H.HIR_HNo = S.HIR_HNo AND H.HIR_RNo = S.RH_RNo
""")
print(f"   {cur.fetchone()[0]:,} of 48,814")
c.close()
