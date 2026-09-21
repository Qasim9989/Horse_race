"""
RACEIQ SYSTEM - telemetry + form, tested at BSP
===============================================
Combines the scraped RaceIQ readings with the form angles that have a record
(weight drop vs last time out, trip record, course record), and asks one question:
does the model find runners the Betfair BSP has priced wrong?

Every feature is pre-race:

  RaceIQ   the horse's most recent EARLIER reading - TopSpeedMph, StrideM, FspPct,
           Accel0To20S, AvgFrequencySps, EntrySpeedMph, SpeedLostMph, JumpIndex,
           LgjLengths - plus its rank for speed and stride inside today's field
  form     age, weight, official rating, weight delta vs last time out, days since,
           last-time-out finish and rating, career runs/wins/places, strike rate at
           today's course and at today's trip

No price is a feature: the BSP itself is the market's probability, so the model has to
beat the price without being told it. A bet fires when the model's chance exceeds the
price's implied chance by a margin, and it settles at BSP net 2% commission.

Train 2023-03-01..2025-12-31, test 2026-01-01 onward - a strict time split.

    python scripts\\raceiq_system.py
    python scripts\\raceiq_system.py --margin 0.20
"""
from __future__ import annotations

import argparse
import re

import numpy as np
import pandas as pd
import pyodbc

COMMISSION = 0.02
RTV = (r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;"
       r"Database=RACINGTV_2023_2026;Trusted_Connection=yes;")
PRO = (r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;"
       r"Database=PRODB;Trusted_Connection=yes;")
SUFFIX = re.compile(r"\((?:ire|gb|fr|usa|can|ger|ity|spa|aus|nz|jpn|hk|swe|den|nor|bel|hol)\)\s*$")
DIST_RE = re.compile(r"(\d+)m(?:\s*(\d+)f)?|(\d+)f", re.I)

TRAIN_END = "2025-12-31"
TEST_FROM = "2026-01-01"

FEATURES = ["TopSpeedMph", "StrideM", "FspPct", "Accel0To20S", "AvgFrequencySps",
            "EntrySpeedMph", "SpeedLostMph", "JumpIndex", "LgjLengths",
            "speed_rank", "stride_rank", "tops_speed", "longest_stride",
            "age", "weight", "or_now", "wgt_delta", "days_since", "lto_pos",
            "lto_or", "runs", "wins", "places", "win_pct", "place_pct",
            "course_runs", "course_places", "course_place_pct",
            "trip_runs", "trip_places", "trip_place_pct", "field_size"]


def norm(value) -> str:
    return re.sub(r"[^a-z0-9]", "", SUFFIX.sub("", str(value or "").strip()).lower())


def trip_of(title: str) -> str:
    """'1m2f' / '7f' / '2m4f' from a race title, so trip form can be measured."""
    m = DIST_RE.search(str(title or ""))
    if not m:
        return ""
    if m.group(3):
        return f"{m.group(3)}f"
    return f"{m.group(1)}m{m.group(2) or '0'}f"


def load_frame(date_from: str) -> pd.DataFrame:
    rtv = pyodbc.connect(RTV)
    runners = pd.read_sql(f"""
        SELECT CAST(RaceDate AS date) race_date, RaceTime, CourseName, RaceTitle,
               HorseName, PosNo, SP, Age, Weight, OfficialRating
        FROM dbo.Scraped_Results WHERE RaceDate >= '{date_from}'
    """, rtv)
    tele = pd.read_sql(f"""
        SELECT CAST(RaceDate AS date) race_date, Horse, TopSpeedMph, StrideM, FspPct,
               Accel0To20S, AvgFrequencySps, EntrySpeedMph, SpeedLostMph, JumpIndex, LgjLengths
        FROM dbo.Scraped_RaceIQ_v2 WHERE RaceDate >= '{date_from}'
    """, rtv)
    rtv.close()

    runners["race_date"] = pd.to_datetime(runners["race_date"])
    runners["horse"] = runners["HorseName"].map(norm)
    runners["venue"] = runners["CourseName"].map(norm)
    runners["time"] = runners["RaceTime"].astype(str)
    runners["trip"] = runners["RaceTitle"].map(trip_of)
    runners["pos"] = pd.to_numeric(runners["PosNo"].astype(str).str.extract(r"^(\d+)")[0],
                                   errors="coerce")
    runners["won"] = (runners["pos"] == 1).astype(int)
    runners["placed"] = (runners["pos"] <= 3).astype(int)
    for c in ("Age", "Weight", "OfficialRating"):
        runners[c] = pd.to_numeric(runners[c], errors="coerce")
    runners = runners.rename(columns={"Age": "age", "Weight": "weight",
                                      "OfficialRating": "or_now"})
    runners["field_size"] = runners.groupby(["race_date", "venue", "time"])["horse"] \
                                  .transform("size")
    runners = runners[(runners["horse"] != "") & (runners["field_size"] >= 5)].copy()

    # --- form, all of it strictly from earlier rows for the same horse
    runners = runners.sort_values(["horse", "race_date", "time"])
    g = runners.groupby("horse", sort=False)
    runners["lto_pos"] = g["pos"].shift(1)
    runners["lto_or"] = g["or_now"].shift(1)
    runners["lto_weight"] = g["weight"].shift(1)
    runners["lto_date"] = g["race_date"].shift(1)
    runners["wgt_delta"] = runners["weight"] - runners["lto_weight"]
    runners["days_since"] = (runners["race_date"] - runners["lto_date"]).dt.days
    runners["runs"] = g.cumcount()
    runners["wins"] = g["won"].transform(lambda s: s.shift(1).fillna(0).cumsum())
    runners["places"] = g["placed"].transform(lambda s: s.shift(1).fillna(0).cumsum())
    runners["win_pct"] = runners["wins"] / runners["runs"].replace(0, np.nan)
    runners["place_pct"] = runners["places"] / runners["runs"].replace(0, np.nan)

    for label, keys in (("course", ["horse", "venue"]), ("trip", ["horse", "trip"])):
        sub = runners.groupby(keys, sort=False)
        runners[f"{label}_runs"] = sub.cumcount()
        runners[f"{label}_places"] = sub["placed"].transform(
            lambda s: s.shift(1).fillna(0).cumsum())
        runners[f"{label}_place_pct"] = (runners[f"{label}_places"]
                                        / runners[f"{label}_runs"].replace(0, np.nan))

    # --- RaceIQ: the most recent EARLIER reading (no look-ahead)
    tele["race_date"] = pd.to_datetime(tele["race_date"])
    tele["horse"] = tele["Horse"].map(norm)
    for c in ("TopSpeedMph", "StrideM", "FspPct", "Accel0To20S", "AvgFrequencySps",
              "EntrySpeedMph", "SpeedLostMph", "JumpIndex", "LgjLengths"):
        tele[c] = pd.to_numeric(tele[c], errors="coerce")
    tele = tele[tele["horse"] != ""].sort_values("race_date")
    runners = pd.merge_asof(runners.sort_values("race_date"),
                            tele[["horse", "race_date", "TopSpeedMph", "StrideM", "FspPct",
                                  "Accel0To20S", "AvgFrequencySps", "EntrySpeedMph",
                                  "SpeedLostMph", "JumpIndex", "LgjLengths"]]
                            .rename(columns={"race_date": "reading_date"}),
                            left_on="race_date", right_on="reading_date", by="horse",
                            direction="backward", allow_exact_matches=False)

    keys = ["race_date", "venue", "time"]
    runners["speed_rank"] = runners.groupby(keys)["TopSpeedMph"].rank(ascending=False)
    runners["stride_rank"] = runners.groupby(keys)["StrideM"].rank(ascending=False)
    runners["tops_speed"] = (runners["speed_rank"] == 1).astype(int)
    runners["longest_stride"] = (runners["stride_rank"] == 1).astype(int)
    return runners


def attach_bsp(frame: pd.DataFrame, date_from: str) -> pd.DataFrame:
    bfsp = pd.read_sql(f"""SELECT CAST(RaceDate AS date) race_date, CourseClean, HorseClean,
                                  BSP_TRUE FROM dbo.BFSP WHERE RaceDate >= '{date_from}'""",
                       pyodbc.connect(PRO))
    bfsp["race_date"] = pd.to_datetime(bfsp["race_date"])
    bfsp["horse"] = bfsp["HorseClean"].map(norm)
    bfsp["venue"] = bfsp["CourseClean"].map(norm)
    bfsp["bsp"] = pd.to_numeric(bfsp["BSP_TRUE"], errors="coerce")
    bfsp = bfsp.drop_duplicates(["race_date", "venue", "horse"])
    return frame.merge(bfsp[["race_date", "venue", "horse", "bsp"]],
                       on=["race_date", "venue", "horse"], how="left")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2023-03-01")
    ap.add_argument("--margin", type=float, default=0.15,
                    help="model chance must exceed the BSP implied chance by this much")
    args = ap.parse_args()

    df = attach_bsp(load_frame(args.date_from), args.date_from)
    df = df[df["bsp"].between(1.05, 500) & df["pos"].notna()]
    print(f"{len(df):,} runners with a BSP, {df['race_date'].min():%Y-%m-%d} to "
          f"{df['race_date'].max():%Y-%m-%d}")
    print(f"with a prior RaceIQ reading: {df['TopSpeedMph'].notna().sum():,}")

    train = df[df["race_date"] <= TRAIN_END]
    test = df[df["race_date"] >= TEST_FROM].copy()
    print(f"train {len(train):,}  test {len(test):,}\n")

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    model = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.06,
                                           min_samples_leaf=200, random_state=0)
    model.fit(train[FEATURES], train["won"])
    test["p"] = model.predict_proba(test[FEATURES])[:, 1]
    test["implied"] = 1 / test["bsp"]
    print(f"model AUC (winners): {roc_auc_score(test['won'], test['p']) * 100:.1f}")

    def book(label, g, margin):
        if len(g) < 30:
            print(f"  {label:<26} {len(g)} bets - too few")
            return
        won = g["won"].to_numpy()
        pl = np.where(won == 1, (g["bsp"] - 1) * (1 - COMMISSION), -1.0)
        print(f"  {label:<26} bets {len(g):>6}  win {won.mean() * 100:>5.1f}%  "
              f"avg BSP {g['bsp'].mean():>7.2f}  ROI {pl.sum() / len(g) * 100:>+7.2f}%  "
              f"P/L {pl.sum():>+9.1f}u")

    print("\n=== TEST 2026, settled at Betfair BSP, 1u win-only, net 2% ===")
    book("back every runner", test, 0.0)
    for margin in (0.05, 0.10, 0.15, 0.25, 0.40):
        flagged = test[test["p"] > test["implied"] * (1 + margin)]
        book(f"model edge >= {margin * 100:.0f}%", flagged, margin)

    print("\nby price band at the 15% margin:")
    flagged = test[test["p"] > test["implied"] * 1.15]
    for lo, hi in ((1, 6), (6, 12), (12, 25), (25, 500)):
        book(f"  BSP {lo}-{hi}", flagged[flagged["bsp"].between(lo, hi, inclusive="left")], 0.15)

    from sklearn.inspection import permutation_importance
    sample = test.sample(min(15000, len(test)), random_state=0)
    imp = permutation_importance(model, sample[FEATURES], sample["won"], n_repeats=3,
                                 random_state=0, scoring="roc_auc")
    print("\nwhat the model leans on (AUC lost when a column is shuffled):")
    for i in np.argsort(imp.importances_mean)[::-1][:10]:
        print(f"  {FEATURES[i]:<22} {imp.importances_mean[i]:+.4f}")


if __name__ == "__main__":
    main()
