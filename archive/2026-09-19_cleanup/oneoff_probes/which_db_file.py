"""Confirm which database/file the stride + price tables live in."""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=120;")
cur = c.cursor()

cur.execute("SELECT DB_NAME(), @@SERVERNAME")
print("connected to database:", cur.fetchone())

print("\nphysical files of PRODB:")
cur.execute("SELECT name, physical_name, SIZE / 128.0 AS size_mb "
            "FROM sys.database_files")
for n, p, mb in cur.fetchall():
    print(f"   {n:<10} {p}   ({mb:,.0f} MB)")

print("\nthe tables I have been reading, all inside PRODB:")
cur.execute("""
    SELECT t.name,
           SUM(p.rows) AS rows_
    FROM sys.tables t
    JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1)
    WHERE t.name IN ('NEW_TPD_STRIDE','NEW_RH','NEW_H','NEW_HIR',
                     'BFSP','IR_Prices','BookOdds','NEW_TPD')
    GROUP BY t.name ORDER BY rows_ DESC
""")
for n, r in cur.fetchall():
    print(f"   {n:<20} {r:>10,}")
c.close()
