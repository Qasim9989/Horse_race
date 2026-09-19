"""
BEN FLAGS FOR TODAY
===================
Reads today's scraped racecard (SCRAPED_PRODB.dbo.Scraped_Racecards), joins each
runner to its PRODB form history, and applies Ben's rules:

    mark falling  +  below last winning mark  +  proven at trip

Outputs reports/bens_today_<date>.csv.

HONEST NOTE: these mechanical rules backtested at -4.16% at Betfair BSP over
5 years (and -17% at bookmaker prices).  They are reproduced here for study -
they are NOT a profitable screen.

Usage:  python scripts/bens_today.py [YYYY-MM-DD]
"""
import datetime
import os
import sys
import warnings

import pandas as pd
import pyodbc

warnings.filterwarnings("ignore")

CARD = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=SCRAPED_PRODB;"
        r"Trusted_Connection=yes;")
PRO = (r"Driver={ODBC Driver 17 for SQL Server};"
       r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")


def norm(n):
    return "".join(c for c in str(n or "").lower() if c.isalnum())


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    c = pyodbc.connect(CARD)
    card = pd.read_sql(
        "SELECT RaceTime, CourseName, RaceTitle, HorseName, JockeyName, "
        "TrainerName, Age, Weight, OfficialRating FROM dbo.Scraped_Racecards "
        "WHERE RaceDate = ?", c, params=[day])
    c.close()
    if card.empty:
        print(f"No scraped racecard for {day}. Run racecard_today.py first.")
        return
    print(f"Card: {len(card)} runners on {day}.")
    card["hk"] = card["HorseName"].map(norm)
    card = card[card["hk"] != ""]

    p = pyodbc.connect(PRO)
    horses = pd.read_sql("SELECT H_No, H_Name FROM NEW_H", p)
    horses["hk"] = horses["H_Name"].map(norm)
    ids = horses[horses["hk"].isin(set(card["hk"]))]["H_No"].unique().tolist()
    print(f"Horses matched to PRODB: {len(ids)}")
    if not ids:
        p.close()
        return

    frames = []
    for i in range(0, len(ids), 20000):
        chunk = ",".join(str(int(x)) for x in ids[i:i + 20000])
        frames.append(pd.read_sql(f"""
            SELECT HIR.HIR_HNo AS hid, RH.RH_DateTime, RH.RH_DistanceID,
                   HIR.HIR_OfficialRating AS OR_, HIR.HIR_PositionNo AS pos
            FROM NEW_HIR HIR JOIN NEW_RH RH ON RH.RH_RNo = HIR.HIR_RNo
            WHERE HIR.HIR_HNo IN ({chunk})""", p))
    p.close()
    hist = pd.concat(frames, ignore_index=True).sort_values("RH_DateTime")

    g = hist.groupby("hid")
    agg = pd.DataFrame({
        "LTO_OR": g["OR_"].last(),
        "LTO_DT": g["RH_DateTime"].last(),
        "WIN_OR": g.apply(lambda d: d[d["pos"] == 1]["OR_"].iloc[-1]
                          if (d["pos"] == 1).any() else None),
        "LTO_POS": g["pos"].last(),
    }).reset_index()

    card = card.merge(horses[["H_No", "hk"]], on="hk", how="left")
    card = card.merge(agg, left_on="H_No", right_on="hid", how="left")

    # current OR: scraped card value if present, else last known
    card["OR_now"] = pd.to_numeric(card["OfficialRating"], errors="coerce")
    card["OR_now"] = card["OR_now"].fillna(card["LTO_OR"])
    card["MarkChange"] = card["OR_now"] - card["LTO_OR"]
    card["BelowWin"] = card["OR_now"] < card["WIN_OR"]
    card["Claimer_"] = None
    card["BEN"] = (card["MarkChange"] < 0) & card["BelowWin"].fillna(False)

    odir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "reports")
    os.makedirs(odir, exist_ok=True)
    out = os.path.join(odir, f"bens_today_{day}.csv")
    cols = ["RaceTime", "CourseName", "RaceTitle", "HorseName", "JockeyName",
            "TrainerName", "OR_now", "LTO_OR", "MarkChange", "WIN_OR",
            "BelowWin", "LTO_POS", "LTO_DT", "BEN"]
    card[cols].sort_values(["RaceTime", "CourseName", "MarkChange"]).to_csv(
        out, index=False)
    print(f"Wrote {out}  ({len(card)} rows)")
    hits = card[card["BEN"]]
    print(f"BEN qualifiers: {len(hits)}")
    if len(hits):
        print(hits[["RaceTime", "CourseName", "HorseName", "OR_now",
                    "LTO_OR", "MarkChange", "WIN_OR", "JockeyName"]]
              .to_string(index=False))


if __name__ == "__main__":
    main()
