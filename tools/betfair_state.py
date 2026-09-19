import os
import sys
import time

import pyodbc

sys.path.insert(0, r"E:\Test\racing-form-system\scripts")
import betfair_creds as bc

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
        r"Trusted_Connection=yes;")

c = pyodbc.connect(CONN)
cur = c.cursor()
cur.execute("SELECT MAX(RaceDate), COUNT(*) FROM dbo.BFSP")
mx, n = cur.fetchone()
print(f"PRODB.dbo.BFSP : {n:,} rows, latest race date {mx}")
for rng in ("2026-09-14", "2026-09-15", "2026-09-16"):
    cur.execute("SELECT COUNT(*) FROM dbo.BFSP WHERE RaceDate=?", (rng,))
    print(f"   rows for {rng}: {cur.fetchone()[0]:,}")
cur.execute("SELECT TOP 3 RaceDate, COUNT(*) FROM dbo.BFSP "
            "GROUP BY RaceDate ORDER BY RaceDate DESC")
print("   last 3 days in the table:", [tuple(r) for r in cur.fetchall()])
cur.execute("SELECT COUNT(*) FROM dbo.BookOdds WHERE RaceDate='2026-09-15'")
print(f"\nPRODB.dbo.BookOdds (bookmaker prices) 2026-09-15: "
      f"{cur.fetchone()[0]:,} rows")
c.close()

cfg = r"E:\CGMBET\betfair_api_config.json"
print(f"\ncredential file : {cfg}")
print(f"   last modified : "
      f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(cfg)))}"
      f"   ({int((time.time()-os.path.getmtime(cfg))/60)} min ago)")
print("   fields        :")
for k in ("app_key", "username", "password", "session"):
    print(f"      {k:9s}: {bc.mask(bc.get(k))}")
print("\nNOTE: changing the password on the Betfair site does NOT update this "
      "file.")
