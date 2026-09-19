"""
BEN'S SYSTEM, backtested at BOOKMAKER early prices (PRODB HIR_MorningPrice /
HIR_BreakfastPrice / HIR_EveningPrice) - full field, not just his picks.

Applies his selection rules and settles win-only AND each-way at the early
bookmaker price, so we finally see whether the RULES are profitable at the
price he actually takes (as opposed to +2.69% at BSP).
"""
import warnings

import numpy as np
import pandas as pd
import pyodbc

warnings.filterwarnings("ignore")

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")

CARD_SQL = """
SELECT RH.RH_RNo, RH.RH_DateTime, RH.RH_NoOfRunners, RH.RH_DistanceID,
       H.H_No AS HorseID, H.H_Name_No_Anything AS HorseName,
       HIR.HIR_Horse_iD AS HorseKey, HIR.HIR_HNo,
       HIR.HIR_OfficialRating, HIR.HIR_JockeysClaim, HIR.HIR_PositionNo,
       HIR.HIR_MorningPrice, HIR.HIR_BreakfastPrice, HIR.HIR_EveningPrice,
       HIR.HIR_BSP_TRUE,
       -- prior-run features, strictly before this run (per-row, no look-ahead)
       LTO.LTO_OR,
       W.WIN_OR,
       M.MAX_OR,
       T.TRIP_CNT
FROM dbo.NEW_RH RH
JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
OUTER APPLY (
    SELECT TOP 1 H2.HIR_OfficialRating AS LTO_OR
    FROM dbo.NEW_HIR H2 JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
    WHERE H2.HIR_HNo = HIR.HIR_HNo AND R2.RH_DateTime < RH.RH_DateTime
    ORDER BY R2.RH_DateTime DESC
) LTO
OUTER APPLY (
    SELECT TOP 1 H2.HIR_OfficialRating AS WIN_OR
    FROM dbo.NEW_HIR H2 JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
    WHERE H2.HIR_HNo = HIR.HIR_HNo AND R2.RH_DateTime < RH.RH_DateTime
      AND H2.HIR_PositionNo = 1
    ORDER BY R2.RH_DateTime DESC
) W
OUTER APPLY (
    SELECT MAX(H2.HIR_OfficialRating) AS MAX_OR
    FROM dbo.NEW_HIR H2 JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
    WHERE H2.HIR_HNo = HIR.HIR_HNo AND R2.RH_DateTime < RH.RH_DateTime
) M
OUTER APPLY (
    SELECT COUNT(*) AS TRIP_CNT
    FROM dbo.NEW_HIR H2 JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
    WHERE H2.HIR_HNo = HIR.HIR_HNo AND R2.RH_DateTime < RH.RH_DateTime
      AND H2.HIR_PositionNo BETWEEN 1 AND 3
      AND R2.RH_DistanceID = RH.RH_DistanceID
) T
WHERE RH.RH_DateTime >= '{d1}' AND RH.RH_DateTime < '{d2}'
  AND RH.RH_Name LIKE '%Handicap%'
  AND RH.RH_NoOfRunners >= 5
  AND HIR.HIR_PositionNo IS NOT NULL AND HIR.HIR_PositionNo > 0
"""


def ben_features(card):
    """Ben's flags from SQL-provided prior-run features (per-row, no look-ahead)."""
    if card.empty:
        return card
    card["MarkChange"] = card["HIR_OfficialRating"] - card["LTO_OR"]
    card["BelowLastWinMark"] = card["HIR_OfficialRating"] < card["WIN_OR"]
    card["BelowCareerMax"] = card["HIR_OfficialRating"] < card["MAX_OR"]
    card["ProvenAtTrip"] = card["TRIP_CNT"].fillna(0) > 0
    card["Claimer"] = card["HIR_JockeysClaim"].fillna(0) > 0
    card["BEN"] = (card["MarkChange"].lt(0)
                   & card["BelowLastWinMark"].fillna(False)
                   & card["ProvenAtTrip"])
    return card


def settle_ew(df, price_col):
    """Each-way: win part at price, place part at 1/5 odds, 3 places (8+ runners)."""
    p = df[price_col]
    won = df["HIR_PositionNo"] == 1
    placed = df["HIR_PositionNo"].between(2, 3) & (df["RH_NoOfRunners"] >= 8)
    # win leg (0.5u) + place leg (0.5u)
    win_pl = np.where(won, (p - 1) * 0.5, -0.5)
    place_odds = 1 + (p - 1) * 0.2
    place_pl = np.where(placed, (place_odds - 1) * 0.5, -0.5)
    return win_pl + place_pl


def report(name, sub, price_col):
    if len(sub) == 0:
        print(f"  {name:<40} 0 bets")
        return
    win = sub["HIR_PositionNo"] == 1
    ew_pl = settle_ew(sub, price_col)
    # win-only ROI: back 1u, + (price-1) if won else -1
    win_pl = np.where(win, sub[price_col] - 1, -1.0)
    print(f"  {name:<40} {len(sub):>7,}  win% {win.mean()*100:5.2f}  "
          f"WIN_ROI {win_pl.mean()*100:+6.2f}%  "
          f"EW_ROI {ew_pl.mean()*100:+6.2f}%  "
          f"(price med {sub[price_col].median():.2f})")


def yearly(name, sub, price_col):
    sub = sub.copy()
    sub["Yr"] = pd.to_datetime(sub["RH_DateTime"]).dt.year
    print(f"\n  {name} by year (win% vs mean(1/price), EW_ROI):")
    for yr, g in sub.groupby("Yr"):
        win = (g["HIR_PositionNo"] == 1).mean() * 100
        imp = (1.0 / g[price_col]).mean() * 100
        ew = settle_ew(g, price_col).mean() * 100
        print(f"    {yr}: n={len(g):>6,}  win% {win:5.2f}  implied {imp:5.2f}  "
              f"gap {win-imp:+5.2f}  EW_ROI {ew:+7.2f}%")



def main():
    d1, d2 = "2021-01-01", "2026-06-01"
    conn = pyodbc.connect(CONN)
    card = pd.read_sql(CARD_SQL.format(d1=d1, d2=d2), conn)
    print(f"Loaded {len(card):,} handicap runners.")
    conn.close()
    card = ben_features(card)

    # restrict to rows with a valid morning (early bookmaker) price
    for pc in ("HIR_MorningPrice", "HIR_BreakfastPrice", "HIR_EveningPrice",
               "HIR_BSP_TRUE"):
        m = card[card[pc] > 1.0].copy()
        pc.replace("HIR_", "").replace("Price", " price")
        print(f"\n=== {pc}  (each-way 1/5, 3 places) ===")
        report("WHOLE FIELD (control)", m, pc)
        report("BEN (mark fall + below last win + trip)", m[m["BEN"]], pc)
        report("mark falling only", m[m["MarkChange"].lt(0)], pc)
        print(f"  BEN bets: {m['BEN'].sum():,} of {len(m):,} ({m['BEN'].mean()*100:.1f}%)")
        # calibration: does the price match the actual win rate for this subset?
        if len(m[m["BEN"]]):
            b = m[m["BEN"]]
            print(f"  BEN win% { (b['HIR_PositionNo']==1).mean()*100:.2f}  "
                  f"vs mean(1/price) { (1.0/b[pc]).mean()*100:.2f}  "
                  f"-> gap {( (b['HIR_PositionNo']==1).mean() - (1.0/b[pc]).mean())*100:+.2f}pt")
            yearly("BEN", b, pc)
            yearly("mark falling only", m[m["MarkChange"].lt(0)], pc)


if __name__ == "__main__":
    main()
