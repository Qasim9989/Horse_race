r"""
STRIDE-ONLY TEST ON SData, vs REAL PRICES
=========================================
SData holds per-horse, per-section: SL_* (stride length), STRK_* (stride count),
MPH_* (speed), FSP_* (finishing speed %), STDIFF_* (v par), LBL_* (lengths
behind), and the RK_* families which are already RANKS WITHIN THE RACE.

We take the FINISH section of each, rank the runners inside each race, and ask
what one-pick-per-race rules would have returned at the real BSP and at the
morning price.

IMPORTANT: these are IN-RACE figures, so this test uses information from the
race being bet - it is an UPPER BOUND on what stride can do, not a tradable
signal.  A tradable version must use the horse's PREVIOUS run instead.

    python scripts\sdata_stride_ev.py --month 2026-04
"""
from __future__ import annotations

import argparse
import re

import pandas as pd
import pyodbc

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)

PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=300;"
       "MultipleActiveResultSets=True;")


def clean(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


ap = argparse.ArgumentParser()
ap.add_argument("--month", default="2026-04")
a = ap.parse_args()
y, m = a.month.split("-")

c = pyodbc.connect(PRO)
cur = c.cursor()
cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='SData' ORDER BY ORDINAL_POSITION")
sdata_cols = [r[0] for r in cur.fetchall()]
finish_cols = [c0 for c0 in sdata_cols if c0.endswith("_Finish")]
print(f"SData *_Finish columns ({len(finish_cols)}): {finish_cols}")

q = f"""
SELECT S.SD_RNo, S.SD_HNo, RH.RH_DateTime, CN.C_Name AS Course,
       H.H_Name, H.H_Name_No_Anything AS HorseClean,
       {', '.join('S.[' + c0 + ']' for c0 in finish_cols)}
FROM dbo.SData S
JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.SD_RNo
JOIN dbo.NEW_H  H  ON H.H_No  = S.SD_HNo
LEFT JOIN dbo.NEW_C CN ON CN.C_ID = RH.RH_CNo
WHERE YEAR(RH.RH_DateTime) = {int(y)} AND MONTH(RH.RH_DateTime) = {int(m)}
"""
df = pd.read_sql(q, c)
print(f"\nSData rows for {a.month}: {len(df):,}  "
      f"races {df['SD_RNo'].nunique():,}")
if df.empty:
    raise SystemExit("no SData rows in that month")

df["RaceDate"] = pd.to_datetime(df["RH_DateTime"]).dt.date
df["RaceTime"] = pd.to_datetime(df["RH_DateTime"]).dt.time
df["CourseClean"] = df["Course"].map(clean)
df["HorseClean"] = df["HorseClean"].map(clean)

bsp = pd.read_sql("""
    SELECT RaceDate, RaceTime, CourseClean, HorseClean,
           BSP_TRUE, MorningWAP, PPWAP, WinLose
    FROM dbo.BFSP
""", c)
c.close()
bsp["RaceDate"] = pd.to_datetime(bsp["RaceDate"]).dt.date
# BFSP.RaceTime is a SQL `time` - pyodbc already returns datetime.time objects,
# so converting it again raises "time is not convertible to datetime".
if not isinstance(bsp["RaceTime"].iloc[0], dt_time := __import__("datetime").time):
    bsp["RaceTime"] = pd.to_datetime(bsp["RaceTime"]).dt.time
for col in ("BSP_TRUE", "MorningWAP", "PPWAP", "WinLose"):
    bsp[col] = pd.to_numeric(bsp[col], errors="coerce")

mrg = df.merge(bsp, on=["RaceDate", "RaceTime", "CourseClean", "HorseClean"],
               how="inner")
print(f"matched to BFSP prices: {len(mrg):,} "
      f"({len(mrg) / max(len(df), 1) * 100:.1f}% of stride rows), "
      f"races {mrg['SD_RNo'].nunique():,}")
if mrg.empty:
    print("\nsample of unmatched keys to debug:")
    print("  stride:", df[["RaceDate", "RaceTime", "CourseClean", "HorseClean"]]
          .head(5).to_dict("records"))
    print("  bfsp  :", bsp[["RaceDate", "RaceTime", "CourseClean", "HorseClean"]]
          .head(5).to_dict("records"))
    raise SystemExit(1)
mrg.to_pickle("reports/_sdata_month.pkl")
print("saved reports/_sdata_month.pkl")
