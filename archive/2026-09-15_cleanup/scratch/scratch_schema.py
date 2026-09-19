import pyodbc

c = pyodbc.connect(r"Driver={ODBC Driver 17 for SQL Server};"
                   r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
                   r"Trusted_Connection=yes;")
cur = c.cursor()
for tbl in ("NEW_RH", "NEW_HIR"):
    cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION", (tbl,))
    cols = [r[0] for r in cur.fetchall()]
    print(f"--- {tbl} ({len(cols)} cols) ---")
    interesting = [x for x in cols if any(k in x.lower() for k in
                   ("dist", "furl", "name", "class", "going", "noof",
                    "official", "pounds", "wgt", "bsp", "pace", "power",
                    "runners", "jockey", "trainer", "age", "position"))]
    print("   " + ", ".join(interesting))
cur.execute("SELECT TOP 1 RH_RNo, RH_Name, RH_NoOfRunners, RH_ClassNum, "
            "RH_DistanceID FROM NEW_RH ORDER BY RH_DateTime DESC")
print("\nsample NEW_RH:", tuple(cur.fetchone()))
cur.execute("SELECT TOP 1 * FROM NEW_HIR")
print("\nNEW_HIR first row:", [d[0] for d in cur.description])
c.close()
