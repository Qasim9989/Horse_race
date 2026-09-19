import pandas as pd
import pyodbc

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")
c = pyodbc.connect(CONN)

sus = ("Redrosecastana", "Don't Go Mad", "Pink Walls", "Cosmocrat",
       "Sol Dorado", "Sumatran Tiger")
q = ("SELECT HorseName, Venue, MarketStatus, RunnerStatus, Back1, Back1Size, "
     "Lay1, Lay1Size, BSP, TradedVolume FROM dbo.BetfairLive "
     "WHERE RaceDate='2026-09-15' AND HorseName IN ("
     + ",".join("?" * len(sus)) + ") ORDER BY HorseName")
print("--- the suspicious 'edge' runners ---")
print(pd.read_sql(q, c, params=list(sus)).to_string(index=False))

print("\n--- RunnerStatus counts ---")
print(pd.read_sql("SELECT RunnerStatus, COUNT(*) AS n FROM dbo.BetfairLive "
                  "WHERE RaceDate='2026-09-15' GROUP BY RunnerStatus", c)
      .to_string(index=False))

print("\n--- MarketStatus counts ---")
print(pd.read_sql("SELECT MarketStatus, COUNT(*) AS n FROM dbo.BetfairLive "
                  "WHERE RaceDate='2026-09-15' GROUP BY MarketStatus", c)
      .to_string(index=False))

print("\n--- spread sanity: back vs lay on active runners ---")
d = pd.read_sql("SELECT * FROM dbo.BetfairLive WHERE RaceDate='2026-09-15' "
                "AND RunnerStatus='ACTIVE' AND Back1 IS NOT NULL "
                "AND Lay1 IS NOT NULL", c)
d["spread"] = d["Lay1"] / d["Back1"]
print(f"   runners {len(d)}   median lay/back spread {d['spread'].median():.3f}"
      f"   widest {d['spread'].max():.1f}")
print("   most extreme spreads:")
print(d.nlargest(8, "spread")[["HorseName", "Venue", "Back1", "Lay1",
                               "Back1Size", "Lay1Size", "TradedVolume"]]
      .to_string(index=False))
c.close()
