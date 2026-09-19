"""SData - the table the user meant. What is in it?"""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=180;"
                   "MultipleActiveResultSets=True;")
cur = c.cursor()

cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='SData' ORDER BY ORDINAL_POSITION")
cols = cur.fetchall()
print(f"SData has {len(cols)} columns:")
for n, t in cols:
    print(f"   {n:<28} {t}")

cur.execute("SELECT COUNT(*) FROM dbo.SData")
n = cur.fetchone()[0]
print(f"\nrows: {n:,}")

print("\npopulated counts per column:")
for name, _ in cols[:30]:
    try:
        cur.execute(f"SELECT COUNT([{name}]) FROM dbo.SData")
        filled = cur.fetchone()[0]
        print(f"   {name:<28} {filled:>10,}")
    except pyodbc.Error as e:
        print(f"   {name:<28} err {str(e)[:40]}")
c.close()
