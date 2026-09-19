import pyodbc

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
        r"Trusted_Connection=yes;")
c = pyodbc.connect(CONN)
cur = c.cursor()

print("-- snapshots stored for 2026-09-15 --")
cur.execute("SELECT SnapshotAt, COUNT(*) FROM dbo.BookOdds "
            "WHERE RaceDate='2026-09-15' GROUP BY SnapshotAt ORDER BY SnapshotAt")
snaps = cur.fetchall()
for r in snaps:
    print("   ", r[0], r[1], "prices")

if len(snaps) > 1:
    first, last = snaps[0][0], snaps[-1][0]
    print(f"\n-- price movement {first} -> {last} (Punchestown 13:40) --")
    cur.execute("""
        SELECT a.HorseName, a.BookmakerName, a.PriceDecimal AS t1, b.PriceDecimal AS t2
        FROM dbo.BookOdds a
        JOIN dbo.BookOdds b
          ON b.RaceDate=a.RaceDate AND b.CourseClean=a.CourseClean
         AND b.RaceTime=a.RaceTime AND b.HorseClean=a.HorseClean
         AND b.BookmakerName=a.BookmakerName AND b.SnapshotAt=?
        WHERE a.SnapshotAt=? AND a.CourseClean='punchestown' AND a.RaceTime='13:40:00'
          AND b.PriceDecimal <> a.PriceDecimal
        ORDER BY a.HorseName, a.BookmakerName""", (last, first))
    changed = cur.fetchall()
    print(f"   {len(changed)} prices changed")
    for r in changed[:18]:
        arrow = "in " if r[3] < r[2] else "out"
        print("   %-20s %-14s %7.2f -> %7.2f  (%s)"
              % (r[0], r[1], r[2], r[3], arrow))
c.close()
