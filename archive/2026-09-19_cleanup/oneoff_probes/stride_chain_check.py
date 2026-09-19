"""Build the chain: stride row -> horse name -> BFSP (result + prices)."""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=180;"
                   "MultipleActiveResultSets=True;")
cur = c.cursor()


def cols(t):
    cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION", (t,))
    return [r[0] for r in cur.fetchall()]


print("NEW_H  cols:", cols("NEW_H")[:14])
print("\nNEW_RH cols:", cols("NEW_RH")[:18])

print("\nBFSP date range:")
cur.execute("SELECT MIN(RaceDate), MAX(RaceDate), COUNT(*) FROM dbo.BFSP")
print("   ", cur.fetchone())
cur.execute("SELECT TOP 2 * FROM dbo.BFSP WHERE RaceDate >= '2026-07-01'")
names = [d[0] for d in cur.description]
for r in cur.fetchall():
    print("   sample:", dict(zip(names, [str(v)[:16] for v in r], strict=False)))

print("\nstride row -> NEW_H name -> NEW_RH date/course:")
cur.execute("""
    SELECT TOP 8 S.RH_RNo, S.HIR_HNo, H.H_Name, RH.RH_DateTime
    FROM dbo.NEW_TPD_STRIDE S
    LEFT JOIN dbo.NEW_H H  ON H.H_No  = S.HIR_HNo
    LEFT JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.RH_RNo
    ORDER BY RH.RH_DateTime DESC
""")
for r in cur.fetchall():
    print("   ", r)
c.close()
