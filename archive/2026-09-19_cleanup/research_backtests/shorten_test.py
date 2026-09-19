"""
SHORTENING TEST
===============
Can a form-based selection (known at 10am) identify horses that SHORTEN
between the early bookmaker price and the off - and does backing them win?

Field baseline: 28.4% of runners shorten, and backing the whole field at the
early price loses ~16% (bookmaker margin + drift).

For each signal we report:
  shorten%  = share where the real Betfair BSP < the early bookmaker price
  ROI@early = back at the early bookmaker price, settle at the result
  ROI@BSP   = back at the real Betfair BSP, settle at the result (control)
  gap       = actual win% - early-price implied%  (must be > 0 to win)
"""
import warnings

import numpy as np
import pandas as pd
import pyodbc

warnings.filterwarnings("ignore")

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")

SQL = """
SELECT RH.RH_RNo, RH.RH_DateTime, RH.RH_NoOfRunners, RH.RH_DistanceID,
       H.H_Name_No_Anything AS HorseName,
       HIR.HIR_OfficialRating, HIR.HIR_JockeysClaim, HIR.HIR_PositionNo,
       HIR.HIR_MorningPrice, HIR.HIR_BreakfastPrice, HIR.HIR_EveningPrice,
       HIR.HIR_BSP_TRUE,
       LTO.LTO_OR, LTO.LTO_POS, W.WIN_OR, T.TRIP_CNT
FROM dbo.NEW_RH RH
JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
OUTER APPLY (SELECT TOP 1 H2.HIR_OfficialRating AS LTO_OR,
                    H2.HIR_PositionNo AS LTO_POS
             FROM dbo.NEW_HIR H2 JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
             WHERE H2.HIR_HNo = HIR.HIR_HNo AND R2.RH_DateTime < RH.RH_DateTime
             ORDER BY R2.RH_DateTime DESC) LTO
OUTER APPLY (SELECT TOP 1 H2.HIR_OfficialRating AS WIN_OR
             FROM dbo.NEW_HIR H2 JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
             WHERE H2.HIR_HNo = HIR.HIR_HNo AND R2.RH_DateTime < RH.RH_DateTime
               AND H2.HIR_PositionNo = 1
             ORDER BY R2.RH_DateTime DESC) W
OUTER APPLY (SELECT COUNT(*) AS TRIP_CNT
             FROM dbo.NEW_HIR H2 JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
             WHERE H2.HIR_HNo = HIR.HIR_HNo AND R2.RH_DateTime < RH.RH_DateTime
               AND H2.HIR_PositionNo BETWEEN 1 AND 3
               AND R2.RH_DistanceID = RH.RH_DistanceID) T
WHERE RH.RH_DateTime >= '2021-01-01' AND RH.RH_DateTime < '2026-06-01'
  AND RH.RH_Name LIKE '%Handicap%' AND RH.RH_NoOfRunners >= 5
  AND HIR.HIR_PositionNo IS NOT NULL AND HIR.HIR_PositionNo > 0
"""


def stats(name, d, early):
    if len(d) == 0:
        print(f"  {name:<44} 0")
        return
    p = d[early]
    won = d["HIR_PositionNo"] == 1
    roi_early = np.where(won, p - 1, -1.0).mean() * 100
    ok = d["HIR_BSP_TRUE"] > 1.0
    dd = d[ok]
    roi_bsp = np.where(dd["HIR_PositionNo"] == 1, dd["HIR_BSP_TRUE"] - 1, -1.0).mean() * 100 if len(dd) else np.nan
    short = (dd["HIR_BSP_TRUE"] < dd[early]).mean() * 100 if len(dd) else np.nan
    imp = (1.0 / p).mean() * 100
    gap = won.mean() * 100 - imp
    print(f"  {name:<44} {len(d):>7,}  shorten% {short:5.1f}  "
          f"ROI@early {roi_early:+7.2f}%  ROI@BSP {roi_bsp:+6.2f}%  "
          f"gap {gap:+5.2f}pt")


def main():
    conn = pyodbc.connect(CONN)
    d = pd.read_sql(SQL, conn)
    conn.close()
    print(f"Loaded {len(d):,} handicap runners with early price.")
    d["MarkFall"] = d["HIR_OfficialRating"] - d["LTO_OR"]
    d["BelowWin"] = d["HIR_OfficialRating"] < d["WIN_OR"]
    d["Trip"] = d["TRIP_CNT"].fillna(0) > 0
    d["Claimer"] = d["HIR_JockeysClaim"].fillna(0) > 0
    d["Top4LTO"] = d["LTO_POS"].fillna(99).between(1, 4)
    d["MF"] = d["MarkFall"] < 0

    for early in ("HIR_BreakfastPrice", "HIR_EveningPrice", "HIR_MorningPrice"):
        m = d[d[early] > 1.0].copy()
        print(f"\n=== entry at {early} ===")
        stats("WHOLE FIELD (baseline)", m, early)
        stats("mark falling", m[m["MF"]], early)
        stats("below last winning mark", m[m["BelowWin"].fillna(False)], early)
        stats("proven at trip", m[m["Trip"]], early)
        stats("claimer", m[m["Claimer"]], early)
        stats("ran top-4 LTO", m[m["Top4LTO"]], early)
        stats("MF + BelowWin", m[m["MF"] & m["BelowWin"].fillna(False)], early)
        stats("MF + BelowWin + Trip (BEN)",
              m[m["MF"] & m["BelowWin"].fillna(False) & m["Trip"]], early)
        stats("MF + Top4LTO + Claimer",
              m[m["MF"] & m["Top4LTO"] & m["Claimer"]], early)
        stats("claimers that placed at trip",
              m[m["Claimer"] & m["Trip"]], early)


if __name__ == "__main__":
    main()
