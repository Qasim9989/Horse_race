"""Small, fast checks on the stride table."""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=120;"
                   "MultipleActiveResultSets=True;")
cur = c.cursor()

cur.execute("""SELECT TOP 5 RH.RH_DateTime, S.RH_RNo, S.HIR_HNo
               FROM dbo.NEW_TPD_STRIDE S
               JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.RH_RNo
               ORDER BY RH.RH_DateTime DESC""")
print("newest stride rows:")
for r in cur.fetchall():
    print("   ", r)

cur.execute("""SELECT TOP 5 RH.RH_DateTime, S.RH_RNo
               FROM dbo.NEW_TPD_STRIDE S
               JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.RH_RNo
               ORDER BY RH.RH_DateTime ASC""")
print("oldest stride rows:")
for r in cur.fetchall():
    print("   ", r)

cur.execute("SELECT MIN(RH_RNo), MAX(RH_RNo), COUNT(DISTINCT RH_RNo) "
            "FROM dbo.NEW_TPD_STRIDE")
print("stride RH_RNo range:", cur.fetchone())
cur.execute("SELECT MIN(IR_RNo), MAX(IR_RNo), COUNT(DISTINCT IR_RNo) "
            "FROM dbo.IR_Prices")
print("IR_Prices IR_RNo range:", cur.fetchone())
c.close()
