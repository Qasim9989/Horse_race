r"""
STRIDE ONLY - PREVIOUS RUN PROFILE -> NEXT RUN, priced at real prices
=====================================================================
SData gives per-horse, per-race stride/sectional figures.  This uses each
horse's PREVIOUS run (no look-ahead) and asks: does ranking the runners in a
race on last-time-out stride/sectional shape find value at the BSP, at the
morning price, and at the pre-off price?

    python scripts\sdata_prev_stride_ev.py --from 2024-01-01

Part 1: pull SData, build lagged (previous-run) metrics, join BFSP prices.
"""
from __future__ import annotations

import argparse

import pandas as pd
import pyodbc

PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=300;"
       "MultipleActiveResultSets=True;")

METRICS = ["SL_Finish", "STRK_Finish", "FSP_Finish", "STDIFF_Finish",
           "MPH_Finish", "SF_Finish", "LBL_Finish", "SPOS_Finish",
           "ST_Finish", "NOS_Finish"]

ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="d_from", default="2024-01-01")
ap.add_argument("--to", dest="d_to", default="2026-12-31")
a = ap.parse_args()

c = pyodbc.connect(PRO)
cur = c.cursor()
cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='SData'")
have = {r[0] for r in cur.fetchall()}
metrics = [m for m in METRICS if m in have]
print("metrics used:", metrics)

q = f"""
SELECT S.SD_RNo, S.SD_HNo, S.SD_HNo AS HorseId, RH.RH_DateTime,
       CN.C_Name AS Course, H.H_Name_No_Anything AS HorseClean,
       {', '.join('S.[' + m + '] AS [prev_' + m + ']' for m in metrics)}
FROM dbo.SData S
JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.SD_RNo
JOIN dbo.NEW_H  H  ON H.H_No  = S.SD_HNo
LEFT JOIN dbo.NEW_C CN ON CN.C_ID = RH.RH_CNo
"""
df = pd.read_sql(q, c)
print(f"\nSData rows pulled: {len(df):,}")

bsp = pd.read_sql("""
    SELECT RaceDate, RaceTime, CourseClean, HorseClean,
           BSP_TRUE, MorningWAP, PPWAP, PPMax, WinLose, PPTradedVol
    FROM dbo.BFSP
""", c)
c.close()

df["RaceDate"] = pd.to_datetime(df["RH_DateTime"]).dt.date
df["RaceTime"] = pd.to_datetime(df["RH_DateTime"]).dt.time
df["when"] = pd.to_datetime(df["RH_DateTime"])
df["HorseClean"] = df["HorseClean"].str.lower().str.replace(
    r"[^a-z0-9]", "", regex=True)
df["CourseClean"] = df["Course"].str.lower().str.replace(
    r"[^a-z0-9]", "", regex=True)

# ---- previous run per horse -------------------------------------------
df = df.sort_values(["SD_HNo", "when"])
for m in metrics:
    df[f"p_{m}"] = df.groupby("SD_HNo")[f"prev_{m}"].shift(1)
df["days_since"] = (df["when"] - df.groupby("SD_HNo")["when"].shift(1)
                    ).dt.days
print(f"rows with a previous run: {df['p_SL_Finish'].notna().sum():,}")

# ---- prices ------------------------------------------------------------
bsp["RaceDate"] = pd.to_datetime(bsp["RaceDate"]).dt.date
for col in ("BSP_TRUE", "MorningWAP", "PPWAP", "PPMax", "WinLose",
            "PPTradedVol"):
    bsp[col] = pd.to_numeric(bsp[col], errors="coerce")
mrg = df.merge(bsp, on=["RaceDate", "RaceTime", "CourseClean", "HorseClean"],
               how="inner")
print(f"matched to BFSP: {len(mrg):,} "
      f"({len(mrg) / max(len(df), 1) * 100:.1f}%)  "
      f"races {mrg['SD_RNo'].nunique():,}")

mrg = mrg[mrg["p_SL_Finish"].notna() & mrg["BSP_TRUE"].notna()]
mrg = mrg[(mrg["RaceDate"] >= pd.Timestamp(a.d_from).date())
          & (mrg["RaceDate"] <= pd.Timestamp(a.d_to).date())]
print(f"in window {a.d_from}..{a.d_to}: {len(mrg):,} runners, "
      f"{mrg['SD_RNo'].nunique():,} races")
mrg.to_pickle("reports/_sdata_prev.pkl")
print("saved reports/_sdata_prev.pkl")
