import pandas as pd
import pyodbc

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")
c = pyodbc.connect(CONN)
d = pd.read_sql("SELECT * FROM dbo.BetfairLive WHERE RaceDate='2026-09-15'", c)

print(f"rows {len(d)}")
print("\nnon-null counts per column:")
print(d.notna().sum().to_string())
print("\nLay1 < 900 :", int((d["Lay1"] < 900).sum()))
print("Lay1 ==1000:", int((d["Lay1"] >= 900).sum()))
print("LastTraded not null:", int(d["LastTraded"].notna().sum()))
print("Back1 not null:", int(d["Back1"].notna().sum()))
print("Back1Size > 0:", int((d["Back1Size"].fillna(0) > 0).sum()))

print("\n--- a near market: Punchestown 1m Hcap (12:40 UTC) ---")
cur = c.cursor()
cur.execute("SELECT DISTINCT MarketID, MarketName, StartUTC FROM dbo.BetfairLive "
            "WHERE RaceDate='2026-09-15' ORDER BY StartUTC")
mk = cur.fetchall()[:3]
for mid, nm, st in mk:
    print(f"\n{mid}  {nm}  {st}")
    q = ("SELECT HorseName, RunnerStatus, Back1, Back1Size, Lay1, Lay1Size, "
         "LastTraded FROM dbo.BetfairLive WHERE MarketID=? ORDER BY Back1")
    print(pd.read_sql(q, c, params=[mid]).head(6).to_string(index=False))
c.close()
