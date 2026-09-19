"""What id space do the stride rows live in?"""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=120;"
                   "MultipleActiveResultSets=True;")
cur = c.cursor()

cur.execute("SELECT TOP 5 RH_RNo, HIR_HNo, RecordTime, HL17_Position "
            "FROM dbo.NEW_TPD_STRIDE ORDER BY RecordTime DESC")
rows = cur.fetchall()
print("sample stride rows (RH_RNo, HIR_HNo, RecordTime, in-race pos):")
for r in rows:
    print("   ", r)

print("\nid ranges:")
cur.execute("SELECT MIN(RH_RNo), MAX(RH_RNo), COUNT(DISTINCT RH_RNo), "
            "MIN(HIR_HNo), MAX(HIR_HNo), COUNT(DISTINCT HIR_HNo) "
            "FROM dbo.NEW_TPD_STRIDE")
print("   stride       :", cur.fetchone())
cur.execute("SELECT MIN(RH_RNo), MAX(RH_RNo), COUNT(DISTINCT RH_RNo) "
            "FROM dbo.NEW_RH")
print("   NEW_RH races :", cur.fetchone())
cur.execute("SELECT MIN(HIR_HNo), MAX(HIR_HNo), COUNT(DISTINCT HIR_HNo) "
            "FROM dbo.NEW_HIR")
print("   NEW_HIR horse:", cur.fetchone())

print("\ndo the sample stride ids exist in NEW_RH / NEW_HIR?")
for rh, hno, _rt, _pos in rows:
    cur.execute("SELECT COUNT(*) FROM dbo.NEW_RH WHERE RH_RNo=?", (rh,))
    in_rh = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM dbo.NEW_HIR WHERE HIR_HNo=?", (hno,))
    in_hir = cur.fetchone()[0]
    print(f"   stride RH_RNo={rh} in NEW_RH: {in_rh}   "
          f"HIR_HNo={hno} in NEW_HIR: {in_hir}")
c.close()
