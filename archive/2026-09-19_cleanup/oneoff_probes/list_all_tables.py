"""Every table in the database, with rows - what did I actually use?"""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=180;")
cur = c.cursor()
cur.execute("SELECT DB_NAME()")
print("database:", cur.fetchone()[0])
cur.execute("""
    SELECT t.name, SUM(p.rows) AS rows_
    FROM sys.tables t
    JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1)
    GROUP BY t.name
    ORDER BY rows_ DESC
""")
print(f"\nall tables ({len(cur.fetchall())} shown below):")
cur.execute("""
    SELECT t.name, SUM(p.rows) AS rows_
    FROM sys.tables t
    JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1)
    GROUP BY t.name
    ORDER BY rows_ DESC
""")
rows = cur.fetchall()
for n, r in rows:
    print(f"   {n:<34} {r:>10,}")
print(f"\ntotal tables: {len(rows)}")

# which of these did the stride/price work read?
used = ["NEW_TPD_STRIDE", "NEW_RH", "NEW_H", "NEW_HIR", "BFSP", "IR_Prices"]
print("\ncolumns of BFSP (the table behind the price numbers):")
cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='BFSP' ORDER BY ORDINAL_POSITION")
for n, t in cur.fetchall():
    print(f"   {n:<18} {t}")
print("\n'Source' values in BFSP:")
try:
    cur.execute("SELECT Source, COUNT(*) FROM dbo.BFSP GROUP BY Source")
    for n, cnt in cur.fetchall():
        print(f"   {n!r:<12} {cnt:,}")
except pyodbc.Error as e:
    print("   ", str(e)[:80])
c.close()
