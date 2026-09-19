"""Why is the stride join empty - no rows in window, or id mismatch?"""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=180;"
                   "MultipleActiveResultSets=True;")
cur = c.cursor()

print("=== stride rows per month (no joins) ===")
cur.execute("""
    SELECT YEAR(RecordTime) AS y, MONTH(RecordTime) AS m, COUNT(*) AS rows_
    FROM dbo.NEW_TPD_STRIDE
    GROUP BY YEAR(RecordTime), MONTH(RecordTime)
    ORDER BY y DESC, m DESC
""")
for y, m, n in cur.fetchall()[:18]:
    print(f"   {y}-{m:02d}  {n:,}")

print("\n=== does RH_RNo match NEW_RH? ===")
cur.execute("SELECT TOP 5 RH_RNo, HIR_HNo, RecordTime FROM dbo.NEW_TPD_STRIDE "
            "ORDER BY RecordTime DESC")
sample = cur.fetchall()
for r in sample:
    print("   stride:", r)
for r in sample:
    cur.execute("SELECT COUNT(*) FROM dbo.NEW_RH WHERE RH_RNo = ?", (r[0],))
    in_rh = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM dbo.NEW_HIR WHERE HIR_RNo = ? AND HIR_HNo = ?",
                (r[0], r[1]))
    in_hir = cur.fetchone()[0]
    print(f"   RH_RNo {r[0]} -> NEW_RH {in_rh} row(s), NEW_HIR {in_hir} row(s)")

print("\n=== what does a NEW_HIR row look like for the newest stride race? ===")
cur.execute("SELECT TOP 3 HIR_RNo, HIR_HNo, HIR_HorseName, HIR_PositionNo "
            "FROM dbo.NEW_HIR ORDER BY HIR_RNo DESC")
for r in cur.fetchall():
    print("   NEW_HIR:", r)
cur.execute("SELECT TOP 3 RH_RNo, RH_DateTime FROM dbo.NEW_RH ORDER BY RH_RNo DESC")
for r in cur.fetchall():
    print("   NEW_RH  :", r)
c.close()
