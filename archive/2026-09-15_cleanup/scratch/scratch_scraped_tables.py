import pandas as pd
import pyodbc

c = pyodbc.connect(r"Driver={ODBC Driver 17 for SQL Server};"
                   r"Server=(localdb)\MSSQLLocalDB;Database=SCRAPED_PRODB;"
                   r"Trusted_Connection=yes;")
cur = c.cursor()
cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES")
tabs = [r[0] for r in cur.fetchall()]
print("SCRAPED_PRODB tables:", tabs)

for t in tabs:
    if "Result" in t or "Racecard" in t or "Scraped" in t:
        cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_NAME=?", (t,))
        cols = [r[0] for r in cur.fetchall()]
        print(f"\n--- {t} ({len(cols)} cols) ---")
        print("   " + ", ".join(cols[:40]))
        try:
            d = pd.read_sql(f"SELECT COUNT(*) AS n, MIN(RaceDate) AS lo, "
                            f"MAX(RaceDate) AS hi FROM dbo.{t}", c)
            print("   rows:", d.to_dict("records"))
        except Exception as e:                                 # noqa: BLE001
            print("   (no RaceDate)", e)
c.close()
