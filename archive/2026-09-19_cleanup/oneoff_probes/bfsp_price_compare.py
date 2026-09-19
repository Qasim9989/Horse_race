r"""
MORNING vs PRE-OFF vs BSP - measured on real exchange prices
============================================================
BFSP holds, for every runner: MorningWAP (morning weighted average price),
PPWAP (pre-off WAP), PPMax/PPMin, BSP_TRUE (real BSP) and WinLose.

So we can answer directly:
  * what does backing everything return at each of the three prices?
  * how often is the morning price better than BSP?
  * where (which price band) is early money better?

    python scripts\bfsp_price_compare.py
"""
import pandas as pd
import pyodbc

pd.set_option("display.width", 200)
c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=300;"
                   "MultipleActiveResultSets=True;")

q = """
SELECT RaceDate, BSP_TRUE, MorningWAP, PPWAP, PPMax, PPMin,
       IPTradedVol, PPTradedVol, WinLose
FROM dbo.BFSP
WHERE BSP_TRUE > 1 AND MorningWAP > 1 AND PPWAP > 1
"""
df = pd.read_sql(q, c)
c.close()
for col in ("BSP_TRUE", "MorningWAP", "PPWAP", "PPMax", "PPMin",
            "IPTradedVol", "PPTradedVol", "WinLose"):
    df[col] = pd.to_numeric(df[col], errors="coerce")
df["RaceDate"] = pd.to_datetime(df["RaceDate"])
df = df.dropna(subset=["BSP_TRUE", "MorningWAP", "PPWAP", "WinLose"])
df["win"] = df["WinLose"].astype(int)

print(f"runners with all three prices: {len(df):,}")
print(f"date range: {df['RaceDate'].min():%Y-%m-%d} -> {df['RaceDate'].max():%Y-%m-%d}")
print(f"distinct race days: {df['RaceDate'].nunique():,}")


def roi(price):
    return (df[price] * df["win"] - 1).mean() * 100


print("\n=== backing EVERY runner at each price ===")
print(f"   morning WAP   : ROI {roi('MorningWAP'):+7.2f}%   "
      f"avg price {df['MorningWAP'].mean():6.2f}")
print(f"   pre-off WAP   : ROI {roi('PPWAP'):+7.2f}%   "
      f"avg price {df['PPWAP'].mean():6.2f}")
print(f"   BSP           : ROI {roi('BSP_TRUE'):+7.2f}%   "
      f"avg price {df['BSP_TRUE'].mean():6.2f}")

df["morning_vs_bsp"] = df["MorningWAP"] / df["BSP_TRUE"]
df["preoff_vs_bsp"] = df["PPWAP"] / df["BSP_TRUE"]
print("\n=== is the early price better than BSP? ===")
print(f"   morning better than BSP : "
      f"{(df['morning_vs_bsp'] > 1).mean() * 100:5.1f}% of runners")
print(f"   pre-off better than BSP : "
      f"{(df['preoff_vs_bsp'] > 1).mean() * 100:5.1f}% of runners")
print(f"   mean morning/BSP ratio  : {df['morning_vs_bsp'].mean():.3f}")
print(f"   mean pre-off/BSP ratio  : {df['preoff_vs_bsp'].mean():.3f}")

print("\n=== by BSP band (where does early money help?) ===")
bands = [(1, 2), (2, 3), (3, 5), (5, 8), (8, 13), (13, 21), (21, 34), (34, 1000)]
rows = []
for lo, hi in bands:
    s = df[(df["BSP_TRUE"] >= lo) & (df["BSP_TRUE"] < hi)]
    if len(s) < 200:
        continue
    rows.append({
        "BSP band": f"{lo}-{hi}",
        "runners": len(s),
        "strike%": round(s["win"].mean() * 100, 1),
        "ROI morning": round((s["MorningWAP"] * s["win"] - 1).mean() * 100, 2),
        "ROI pre-off": round((s["PPWAP"] * s["win"] - 1).mean() * 100, 2),
        "ROI BSP": round((s["BSP_TRUE"] * s["win"] - 1).mean() * 100, 2),
        "morning/BSP": round((s["MorningWAP"] / s["BSP_TRUE"]).mean(), 3),
    })
print(pd.DataFrame(rows).to_string(index=False))

print("\n=== yearly, at BSP vs morning (backing everything) ===")
df["year"] = df["RaceDate"].dt.year
yr = df.groupby("year").apply(lambda s: pd.Series({
    "runners": len(s),
    "ROI morning": round((s["MorningWAP"] * s["win"] - 1).mean() * 100, 2),
    "ROI BSP": round((s["BSP_TRUE"] * s["win"] - 1).mean() * 100, 2),
    "morning/BSP": round((s["MorningWAP"] / s["BSP_TRUE"]).mean(), 3),
}))
print(yr.to_string())
