import pandas as pd
import pyodbc

s = pyodbc.connect(r"Driver={ODBC Driver 17 for SQL Server};"
                   r"Server=(localdb)\MSSQLLocalDB;Database=SCRAPED_PRODB;"
                   r"Trusted_Connection=yes;")
d = pd.read_sql("SELECT TOP 12 RaceDate, CourseName, RaceTime, RaceTitle, "
                "HorseName, PosNo, OfficialRating, Weight, BSP "
                "FROM dbo.Scraped_Results WHERE RaceDate >= '2026-08-10' "
                "ORDER BY RaceDate DESC, RaceTime", s)
print(d.to_string(index=False))
c = s.cursor()
c.execute("SELECT COUNT(*) FROM dbo.Scraped_Results WHERE "
          "OfficialRating IS NOT NULL AND OfficialRating > 0")
print("\nrows with a usable OR:", c.fetchone()[0])
c.execute("SELECT COUNT(DISTINCT HorseName) FROM dbo.Scraped_Results")
print("distinct horses:", c.fetchone()[0])
c.execute("SELECT MAX(RaceDate) FROM dbo.Scraped_Results WHERE RaceDate < '2026-09-15'")
print("last results date:", c.fetchone()[0])
s.close()
