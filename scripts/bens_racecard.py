"""
BEN-STYLE RACE CARD
===================
Rebuilds the "daily sheet" Ben uses, from PRODB, and flags his selection
criteria.  Usage:  python scripts/bens_racecard.py [YYYY-MM-DD]
"""
import datetime
import os
import sys
import warnings

import numpy as np
import pandas as pd
import pyodbc

warnings.filterwarnings("ignore")

CONN = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;"
)

CARD_SQL = """
SELECT
  RH.RH_RNo, RH.RH_DateTime, C.C_Name AS CourseName, RH.RH_Name AS RaceTitle,
  RH.RH_NoOfRunners, RH.RH_ClassNum, RH.RH_DistanceID,
  H.H_No AS HorseID, H.H_Name_No_Anything AS HorseName,
  HIR.HIR_Horse_iD AS HorseKey,
  HIR.HIR_Jockey_name, HIR.HIR_Trainer_name, HIR.HIR_Age, HIR.HIR_Pounds,
  HIR.HIR_JockeysClaim, HIR.HIR_OfficialRating, HIR.HIR_OfficialRating_RANK,
  HIR.HIR_DSLR, HIR.HIR_PaceAbbrev, HIR.HIR_PaceRating,
  HIR.HIR_PowerRating, HIR.HIR_PowerRating_RANK, HIR.HIR_WGT_RANK,
  HIR.HIR_BSP, HIR.HIR_BSP_TRUE
FROM dbo.NEW_RH RH
JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
LEFT JOIN dbo.NEW_C C ON C.C_ID = RH.RH_CNo
WHERE RH.RH_DateTime >= '{d1}' AND RH.RH_DateTime < '{d2}'
  AND RH.RH_Name LIKE '%Handicap%'
  AND RH.RH_NoOfRunners >= 5
"""

HIST_SQL = """
SELECT HIR.HIR_Horse_iD AS HorseKey, RH.RH_DateTime, RH.RH_DistanceID,
       HIR.HIR_OfficialRating, HIR.HIR_PositionNo, HIR.HIR_BSP
FROM dbo.NEW_HIR HIR
JOIN dbo.NEW_RH RH ON RH.RH_RNo = HIR.HIR_RNo
WHERE HIR.HIR_Horse_iD IN ({})
"""


def horse_history(conn, horse_ids):
    if not horse_ids:
        return pd.DataFrame()
    ids = ",".join(str(int(i)) for i in horse_ids)
    return pd.read_sql(HIST_SQL.format(ids), conn)


def ben_features(card, conn):
    if card.empty:
        return card
    horse_ids = [int(i) for i in card["HorseKey"].dropna().unique()]
    hist = horse_history(conn, horse_ids)

    # each horse's OWN current race time, so history is strictly before it
    # (a day's max time would wrongly include a horse's own early race)
    cur = card[["HorseKey", "RH_DateTime"]].rename(columns={"RH_DateTime": "cur_dt"})
    hist = hist.merge(cur, on="HorseKey", how="left")
    hist = hist[hist["RH_DateTime"] < hist["cur_dt"]].sort_values("RH_DateTime")

    g = hist.groupby("HorseKey")
    agg = pd.DataFrame({
        "OR_LTO": g["HIR_OfficialRating"].last(),
        "OR_career_max": g["HIR_OfficialRating"].max(),
        "OR_last_win": g.apply(lambda d: d[d["HIR_PositionNo"] == 1]
                               ["HIR_OfficialRating"].iloc[-1]
                               if (d["HIR_PositionNo"] == 1).any() else np.nan),
        "BSP_min_prior": g["HIR_BSP"].min(),
    }).reset_index()
    card = card.merge(agg, on="HorseKey", how="left")

    card["MarkChange"] = card["HIR_OfficialRating"] - card["OR_LTO"]
    card["BelowLastWinMark"] = card["HIR_OfficialRating"] < card["OR_last_win"]
    card["BelowCareerMax"] = card["HIR_OfficialRating"] < card["OR_career_max"]

    proven = (hist[hist["HIR_PositionNo"].between(1, 3)]
              .groupby(["HorseKey", "RH_DistanceID"]).size().reset_index()
              .rename(columns={0: "prior_placings_at_dist"}))
    card = card.merge(proven, on=["HorseKey", "RH_DistanceID"], how="left")
    card["ProvenAtTrip"] = card["prior_placings_at_dist"].fillna(0) > 0
    card["PreviouslyShort"] = card["BSP_min_prior"] <= 4.0

    card["RanTop4LTO"] = hist.groupby("HorseKey")["HIR_PositionNo"].last() \
        .reindex(card["HorseKey"]).between(1, 4).to_numpy()
    card["Claimer"] = card["HIR_JockeysClaim"].fillna(0) > 0

    card["BEN_flag"] = (
        card["MarkChange"].lt(0)
        & card["BelowLastWinMark"].fillna(False)
        & card["BelowCareerMax"].fillna(True)
        & card["ProvenAtTrip"]
        & card["RanTop4LTO"].fillna(False)
    )
    card["BEN_flag_soft"] = (
        card["MarkChange"].lt(0)
        & card["BelowCareerMax"].fillna(True)
        & card["ProvenAtTrip"]
    )
    return card




def main():
    conn = pyodbc.connect(CONN)
    if len(sys.argv) > 1:
        day = datetime.date.fromisoformat(sys.argv[1])
    else:
        day = pd.read_sql(
            "SELECT MAX(RH_DateTime) d FROM dbo.NEW_RH", conn)["d"][0].date()
    d1 = datetime.datetime.combine(day, datetime.time.min)
    d2 = d1 + datetime.timedelta(days=1)

    card = pd.read_sql(
        CARD_SQL.format(d1=d1.strftime("%Y-%m-%d"), d2=d2.strftime("%Y-%m-%d")),
        conn)

    print(f"Loaded {len(card):,} handicap runners across "
          f"{card['RH_RNo'].nunique():,} races on {day}.")
    if card.empty:
        print(f"\n  PRODB holds no races for {day}.  PRODB's form data stops on "
              "2026-05-22, so the full five-condition system cannot be run for "
              "later dates.\n  Use the scraped card instead (scripts/"
              "bens_today.py), or backfill PRODB, or run this for a date it "
              "does cover.")
        conn.close()
        return
    card = ben_features(card, conn)
    conn.close()

    cols = ["RH_DateTime", "CourseName", "RaceTitle", "HorseName",
            "HIR_Jockey_name", "HIR_Trainer_name", "HIR_Age", "HIR_Pounds",
            "HIR_JockeysClaim", "HIR_OfficialRating", "MarkChange",
            "OR_last_win", "OR_career_max", "HIR_DSLR", "HIR_PaceAbbrev",
            "HIR_PowerRating_RANK", "HIR_WGT_RANK", "HIR_BSP",
            "HIR_BSP_TRUE", "ProvenAtTrip", "PreviouslyShort", "Claimer",
            "RanTop4LTO", "BEN_flag", "BEN_flag_soft"]
    out = card[cols].sort_values(
        ["RH_DateTime", "CourseName", "HIR_OfficialRating"])

    odir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "reports")
    os.makedirs(odir, exist_ok=True)
    path = os.path.join(odir, f"bens_racecard_{day}.csv")
    out.to_csv(path, index=False)
    print(f"Wrote {path}  ({len(out):,} rows)")

    picks = out[out["BEN_flag"]]
    soft = out[out["BEN_flag_soft"]]
    print(f"\nBEN_flag (hard): {len(picks):,} runners")
    print(f"BEN_flag (soft): {len(soft):,} runners")
    if len(picks):
        print("\nHARD picks:")
        print(picks[["RH_DateTime", "CourseName", "HorseName",
                     "HIR_OfficialRating", "MarkChange", "OR_last_win",
                     "HIR_Jockey_name", "HIR_BSP_TRUE"]].to_string(index=False))


if __name__ == "__main__":
    main()
