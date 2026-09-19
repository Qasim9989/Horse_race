import pyodbc

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
        r"Trusted_Connection=yes;")

c = pyodbc.connect(CONN)
cur = c.cursor()
for q in (
    "SELECT MAX(RaceDate) AS max_date, COUNT(*) AS n FROM dbo.BFSP",
    "SELECT COUNT(*) FROM dbo.BFSP WHERE RaceDate >= '2026-09-12'",
    "SELECT RaceDate, COUNT(*) FROM dbo.BFSP WHERE RaceDate >= '2026-09-10' "
    "GROUP BY RaceDate ORDER BY RaceDate",
    "SELECT COUNT(*) FROM sys.tables WHERE name = 'BookOdds'",
    "SELECT COUNT(*) FROM dbo.BookOdds",
):
    try:
        cur.execute(q)
        print(q[:70], "->", [tuple(r) for r in cur.fetchall()])
    except Exception as e:                                     # noqa: BLE001
        print(q[:70], "-> ERR", e)
c.close()
