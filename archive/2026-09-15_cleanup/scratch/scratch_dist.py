import pyodbc

c = pyodbc.connect(r"Driver={ODBC Driver 17 for SQL Server};"
                   r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
                   r"Trusted_Connection=yes;")
cur = c.cursor()
cur.execute("SELECT TOP 8 RH_DateTime, RH_Name, RH_Exact_Race_Distance, "
            "RH_DistanceID FROM dbo.NEW_RH ORDER BY RH_DateTime DESC")
print("recent races (distance columns):")
for r in cur.fetchall():
    print("   ", r[0], "|", str(r[1])[:44], "| exact:", r[2], "| distID:", r[3])
cur.execute("SELECT COUNT(DISTINCT RH_Exact_Race_Distance) FROM dbo.NEW_RH")
print("\ndistinct RH_Exact_Race_Distance values:", cur.fetchone()[0])
cur.execute("SELECT TOP 12 RH_Exact_Race_Distance, COUNT(*) c FROM dbo.NEW_RH "
            "GROUP BY RH_Exact_Race_Distance ORDER BY c DESC")
print("most common:", [tuple(r) for r in cur.fetchall()])
c.close()
