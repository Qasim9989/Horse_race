import pyodbc

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
        r"Trusted_Connection=yes;")
c = pyodbc.connect(CONN)
cur = c.cursor()

print("-- rows per book (2026-09-15) --")
cur.execute("SELECT BookmakerName, COUNT(*) n, MIN(PriceDecimal), MAX(PriceDecimal) "
            "FROM dbo.BookOdds WHERE RaceDate='2026-09-15' "
            "GROUP BY BookmakerName ORDER BY BookmakerName")
for r in cur.fetchall():
    print("   %-16s %6d  min %7.2f  max %7.2f" % (r[0], r[1], r[2], r[3]))

print("\n-- sample: Punchestown 13:40, one horse across all books --")
cur.execute("""SELECT BookmakerName, PriceDecimal, PriceFractional, Fluctuation,
                      EWPlaces, EWDenominator, HorseName, JockeyName, Age, Weight,
                      TimeformRating
               FROM dbo.BookOdds
               WHERE RaceDate='2026-09-15' AND CourseClean='punchestown'
                 AND HorseClean='exceptionally'
               ORDER BY PriceDecimal DESC""")
rows = cur.fetchall()
print("   horse:", rows[0][6], "| jockey:", rows[0][7], "| age:", rows[0][8],
      "| wgt:", rows[0][9], "| TF:", rows[0][10]) if rows else print("   none")
for r in rows:
    print("   %-16s %8.2f %8s %-11s ew 1/%s %s places"
          % (r[0], r[1], r[2], r[3], r[5], r[4]))

print("\n-- runner-field coverage (the fields the old HTML scrape got wrong) --")
cur.execute("""SELECT COUNT(*) total,
                      SUM(CASE WHEN Age IS NULL THEN 1 ELSE 0 END) no_age,
                      SUM(CASE WHEN TimeformRating IS NULL THEN 1 ELSE 0 END) no_tf,
                      SUM(CASE WHEN JockeyName IS NULL THEN 1 ELSE 0 END) no_jock
               FROM dbo.BookOdds WHERE RaceDate='2026-09-15'""")
print("   ", [tuple(r) for r in cur.fetchall()])
c.close()
