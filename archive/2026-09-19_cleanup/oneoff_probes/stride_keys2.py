"""Every TPD / stride table, and what its keys actually look like."""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=120;"
                   "MultipleActiveResultSets=True;")
cur = c.cursor()

cur.execute("""
    SELECT t.name, SUM(p.rows)
    FROM sys.tables t
    JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1)
    WHERE t.name LIKE '%TPD%' OR t.name LIKE '%STRIDE%' OR t.name LIKE '%SECTION%'
    GROUP BY t.name ORDER BY SUM(p.rows) DESC
""")
tables = cur.fetchall()
print("TPD / stride tables:")
for n, r in tables:
    print(f"   {n:<28} {r:>10,}")

for n, r in tables:
    cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION", (n,))
    cols = [x[0] for x in cur.fetchall()]
    print(f"\n=== {n} ({r:,} rows) ===")
    print("   cols:", ", ".join(cols))
    # cardinality of each key-looking column
    for col in cols:
        if any(k in col for k in ("RNo", "HNo", "ID", "Ref")):
            try:
                cur.execute(f"SELECT COUNT(DISTINCT [{col}]) FROM dbo.[{n}]")
                print(f"      distinct {col:<12} {cur.fetchone()[0]:,}")
            except pyodbc.Error:
                pass
c.close()
