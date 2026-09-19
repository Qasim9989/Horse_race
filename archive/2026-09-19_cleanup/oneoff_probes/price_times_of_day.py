r"""
EVENING vs MORNING vs BSP - where is the price edge?
====================================================
All from PRODB.BFSP, one row per runner, no joins needed:
    MorningWAP  exchange morning (weighted average) price
    PPWAP       pre-off weighted average price
    PPMax       best pre-off price (what you could have taken)
    BSP_TRUE    real Betfair SP
    WinLose     the result
    IPTradedVol / PPTradedVol   matched volume

    python scripts\price_times_of_day.py --days 31
"""
from __future__ import annotations

import argparse

import pandas as pd
import pyodbc

pd.set_option("display.width", 220)
c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=180;")
ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=31)
a = ap.parse_args()

q = """
SELECT RaceDate, CourseClean, HorseClean,
       CAST(BSP_TRUE AS float) AS bsp,
       CAST(MorningWAP AS float) AS morning,
       CAST(PPWAP AS float) AS ppwap,
       CAST(PPMax AS float) AS ppmax,
       CAST(PPTradedVol AS float) AS vol,
       WinLose
FROM dbo.BFSP
WHERE RaceDate >= DATEADD(day, -?, CAST(GETDATE() AS date))
"""
df = pd.read_sql(q, c, params=[a.days])
c.close()

df["win"] = pd.to_numeric(df["WinLose"], errors="coerce").fillna(0).clip(0, 1)
for col in ("bsp", "morning", "ppwap", "ppmax"):
    df = df[df[col].notna() & (df[col] > 1)]
print(f"window: last {a.days} days -> {len(df):,} runner rows with all prices")
if df.empty:
    raise SystemExit("no rows with all four prices in that window")

# price ratios: how much bigger is the early price than BSP?
df["m_over_bsp"] = df["morning"] / df["bsp"]
df["pp_over_bsp"] = df["ppwap"] / df["bsp"]
df["max_over_bsp"] = df["ppmax"] / df["bsp"]


def roi(price, label, frame=None):
    f = df if frame is None else frame
    if f.empty:
        return {"selection": label, "runners": 0}
    r = (f[price] * f["win"] - 1).mean() * 100
    return {"selection": label, "runners": len(f),
                "strike": round(f["win"].mean() * 100, 2),
                "roi": round(r, 2)}


rows = [
    roi("morning", "ALL: back at MORNING price"),
    roi("ppwap", "ALL: back at PRE-OFF price (PPWAP)"),
    roi("ppmax", "ALL: back at BEST pre-off price"),
    roi("bsp", "ALL: back at real BSP"),
]
tab = pd.DataFrame(rows)

print("\n=== backing every runner, one month, at each price point ===")
print(tab.to_string(index=False))

print("\n=== is the early price actually bigger than BSP? ===")
print(f"   mean morning/BSP  {df['m_over_bsp'].mean():.3f}   "
      f"(median {df['m_over_bsp'].median():.3f})")
print(f"   mean pre-off/BSP  {df['pp_over_bsp'].mean():.3f}   "
      f"(median {df['pp_over_bsp'].median():.3f})")
print(f"   mean best/BSP     {df['max_over_bsp'].mean():.3f}   "
      f"(median {df['max_over_bsp'].median():.3f})")
print(f"   share with morning price > BSP: "
      f"{(df['m_over_bsp'] > 1).mean() * 100:.1f}%")

print("\n=== ROI at BSP, split by how the price moved into the off ===")
short = df[df["m_over_bsp"] > 1.10]
flat = df[(df["m_over_bsp"] >= 0.90) & (df["m_over_bsp"] <= 1.10)]
drift = df[df["m_over_bsp"] < 0.90]
rows = [roi("bsp", "SHORTENED >10% (morning -> BSP)", short),
        roi("bsp", "steady +/-10%", flat),
        roi("bsp", "DRIFTED >10%", drift),
        roi("morning", "SHORTENED: back at morning instead", short),
        roi("morning", "DRIFTED: back at morning instead", drift)]
print(pd.DataFrame(rows).to_string(index=False))

print("\n=== only runners with real volume (vol > 500) ===")
b = df[df["vol"] > 500]
rows = [roi("morning", "vol>500: morning", b),
        roi("ppwap", "vol>500: pre-off", b),
        roi("bsp", "vol>500: BSP", b)]
print(pd.DataFrame(rows).to_string(index=False))
