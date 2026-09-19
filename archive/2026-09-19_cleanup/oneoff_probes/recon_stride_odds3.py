"""
RECON 3: the price table (evening / morning / BSP) and stride join coverage
"""
import pyodbc

BASE = ("Driver={ODBC Driver 17 for SQL Server};"
        "Server=(localdb)\\MSSQLLocalDB;Database=")
MARS = ";Trusted_Connection=yes;Connection Timeout=60;MultipleActiveResultSets=True;"


def conn(db):
    return pyodbc.connect(BASE + db + MARS)


c = conn("PRODB")
cur = c.cursor()

for table in ("IR_Prices", "BookOdds"):
    print(f"\n=== PRODB.{table} ===")
    cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION", (table,))
    cols = [r[0] for r in cur.fetchall()]
    print("   cols:", ", ".join(cols))
    cur.execute(f"SELECT COUNT(*) FROM dbo.{table}")
    n = cur.fetchone()[0]
    print(f"   rows: {n:,}")
    if n:
        for col in cols:
            try:
                cur.execute(f"SELECT COUNT([{col}]) FROM dbo.{table}")
                filled = cur.fetchone()[0]
                if filled:
                    print(f"      {col:<24} populated {filled:,}")
            except pyodbc.Error:
                pass
        # a sample so we can see what a row looks like
        cur.execute(f"SELECT TOP 3 * FROM dbo.{table}")
        names = [d[0] for d in cur.description]
        for row in cur.fetchall():
            print("      sample:", dict(zip(names, [str(v)[:22] for v in row], strict=False)))

print("\n=== date range of the stride table ===")
cur.execute("""
    SELECT MIN(RH.RH_DateTime), MAX(RH.RH_DateTime), COUNT(*)
    FROM dbo.NEW_TPD_STRIDE S JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.RH_RNo
""")
print("   ", cur.fetchone())

print("\n=== can we back-test stride against real BSP? ===")
cur.execute("""
    SELECT COUNT(DISTINCT S.HIR_HNo) AS horses,
           COUNT(DISTINCT S.RH_RNo) AS races,
           COUNT(*) AS stride_rows
    FROM dbo.NEW_TPD_STRIDE S
""")
h, r, n = cur.fetchone()
print(f"   stride rows {n:,} across {r:,} races, {h:,} horses")
cur.execute("""
    SELECT COUNT(*) FROM dbo.NEW_TPD_STRIDE S
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_HNo = S.HIR_HNo AND HIR.HIR_RNo = S.RH_RNo
""")
print(f"   stride rows that join a NEW_HIR runner row: {cur.fetchone()[0]:,}")
c.close()
