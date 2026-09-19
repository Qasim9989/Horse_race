"""
RECON 2: the stride table and the historical price table
=======================================================
PRODB turns out to hold:
    NEW_TPD_STRIDE   48,814 rows   (TPD = Total Performance Data: stride)
    IR_Prices     2,181,213 rows   (industry prices - how many time-of-day
                                    snapshots? BSP? morning?)
    BFSP            697,212 rows   (real Betfair BSP)
    NEW_HIR       3,115,568 rows   (marks + populated pace columns)
"""
import pyodbc

BASE = ("Driver={ODBC Driver 17 for SQL Server};"
        "Server=(localdb)\\MSSQLLocalDB;Database=")


def conn(db):
    return pyodbc.connect(BASE + db + ";Trusted_Connection=yes;"
                                         "Connection Timeout=60;")


def describe(c, table, label):
    cur = c.cursor()
    cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION", (table,))
    cols = cur.fetchall()
    print(f"\n=== {label}: {table} ===")
    print("   cols:", ", ".join(f"{n}:{t}" for n, t in cols))
    return [n for n, _ in cols]


c = conn("PRODB")
cur = c.cursor()

# --- stride ---
cols = describe(c, "NEW_TPD_STRIDE", "stride")
cur.execute("SELECT COUNT(*) FROM dbo.NEW_TPD_STRIDE")
n = cur.fetchone()[0]
print(f"   rows: {n:,}")
for col in cols:
    try:
        cur.execute(f"SELECT COUNT([{col}]) FROM dbo.NEW_TPD_STRIDE")
        filled = cur.fetchone()[0]
        if filled:
            print(f"      {col:<26} populated {filled:,}")
    except pyodbc.Error:
        pass

# --- prices ---
cols = describe(c, "IR_Prices", "historical prices")
cur.execute("SELECT COUNT(*) FROM dbo.IR_Prices")
n = cur.fetchone()[0]
print(f"   rows: {n:,}")
for col in cols:
    try:
        cur.execute(f"SELECT COUNT([{col}]) FROM dbo.IR_Prices")
        filled = cur.fetchone()[0]
        if filled == n:
            print(f"      {col:<26} populated {filled:,}")
    except pyodbc.Error:
        pass
c.close()
