"""Which table holds which prices, and how many snapshots of each."""
import pandas as pd
import pyodbc

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")
c = pyodbc.connect(CONN)
for tbl, col in (("BookOdds", "BookmakerName"), ("BetfairLive", "MarketID")):
    d = pd.read_sql(f"SELECT SnapshotAt, PriceDecimal FROM dbo.BookOdds"
                    if tbl == "BookOdds" else
                    "SELECT SnapshotAt, Back1 FROM dbo.BetfairLive", c)
    g = d.groupby("SnapshotAt").size()
    print(f"--- {tbl} ({col}) : {len(g)} snapshot(s) ---")
    for t, n in g.items():
        print(f"    {t}  rows {n}")
c.close()
