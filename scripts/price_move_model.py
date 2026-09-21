"""
PRICE MOVE MODEL - can the morning -> off move be predicted?
============================================================
Offline research only: nothing here is wired into the site.

Target: does the runner come in (steam) or go out (drift) between the morning price
and the Betfair Starting Price.  Features are restricted to what is knowable in the
morning - the morning price, its rank in the market, the field, and pre-race form.
Deliberately excluded: IPMin / IPMax (in-play) and PPTradedVol (only known at the
off, and a steam causes the volume rather than the other way round).

Validation is a strict time split: train 2021-01-01..2024-12-31, test 2025-01-01 onward.
A model only earns its place if it beats the price-band baseline, which already knows
that long prices drift.

    python scripts\\price_move_model.py
    python scripts\\price_move_model.py --rebuild
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_tips_history import RP_DB, add_history, categorise, load_runners

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "reports", "_price_move.pkl")
COMMISSION = 0.02
STEAM_TOL = 0.03
TRAIN_END = "2024-12-31"
TEST_START = "2025-01-01"

FEATS = ["log_morning", "mkt_rank", "gap_to_fav", "field_size", "is_fav",
         "official_rating", "topspeed", "rpr", "weight_lbs", "delta_wgt",
         "prior_best_ts", "lto_pos", "lto_official_rating", "trip_wins",
         "trip_places", "days_since", "runs_before", "race_class_num",
         "is_handicap", "is_jumps", "cat_big_drop", "cat_value", "cat_placed"]


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add what the first version was missing: within-race position, and course form.

    Raw RPR of 88 means nothing on its own - 88 is strong in one race and ordinary in
    another.  What the market moves on is where a horse sits in THIS field, so every
    rating gets a rank and a gap-to-field-mean inside its own race.  Also adds the
    horse's record at today's course, which was never in the model.
    """
    df = df.copy()
    race = "EventID" if "EventID" in df.columns else "race_id"

    # the cached frame carries `pos` but not `placed`
    if "placed" not in df.columns:
        df["placed"] = (pd.to_numeric(df.get("pos"), errors="coerce") <= 3).astype(int)
    if "meeting" not in df.columns and "CourseClean" in df.columns:
        df["meeting"] = df["CourseClean"]

    for col in ("rpr", "official_rating", "topspeed", "weight_lbs"):
        if col in df.columns:
            df[f"{col}_rank"] = df.groupby(race)[col].rank(ascending=False)
            df[f"{col}_vs_field"] = df[col] - df.groupby(race)[col].transform("mean")

    if "log_morning" in df.columns:
        df["price_vs_field"] = df["log_morning"] - df.groupby(race)["log_morning"].transform("mean")
        df["rpr_x_price"] = df["rpr_vs_field"] * df["price_vs_field"] if "rpr_vs_field" in df else np.nan

    df = df.sort_values(["horse", "race_date"])
    by_course = df.groupby(["horse", "meeting"], sort=False)
    df["course_runs"] = by_course.cumcount()
    df["course_places"] = by_course["placed"].transform(lambda s: s.shift(1).fillna(0).cumsum())
    df["course_place_pct"] = df["course_places"] / df["course_runs"].replace(0, np.nan)
    df["lto_winner"] = (by_course["pos"].transform(lambda s: s.shift(1)) == 1).astype(float)
    return df


DERIVED = ["rpr_rank", "rpr_vs_field", "official_rating_rank", "official_rating_vs_field",
           "topspeed_rank", "topspeed_vs_field", "weight_lbs_rank", "weight_lbs_vs_field",
           "price_vs_field", "rpr_x_price", "course_runs", "course_places",
           "course_place_pct", "lto_winner"]


def build_frame() -> pd.DataFrame:
    """BFSP morning price + BSP, with morning-known market structure per event."""
    import pyodbc
    conn = pyodbc.connect(
        r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;"
        r"Database=PRODB;Trusted_Connection=yes;MultipleActiveResultSets=True;")
    prices = pd.read_sql("""
        SELECT CAST(RaceDate AS date) race_date, CourseClean, HorseClean, EventID,
               MorningWAP, BSP_TRUE
        FROM dbo.BFSP
        WHERE RaceDate >= '2021-01-01'
    """, conn)
    conn.close()
    prices["race_date"] = prices["race_date"].astype(str)
    prices["horse"] = prices["HorseClean"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    prices["meeting"] = prices["CourseClean"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    for c in ("MorningWAP", "BSP_TRUE"):
        prices[c] = pd.to_numeric(prices[c], errors="coerce")
    prices = prices[prices["MorningWAP"].between(1.0, 1000, inclusive="neither")
                    & prices["BSP_TRUE"].between(1.0, 1000, inclusive="neither")]
    prices = prices.drop_duplicates(["race_date", "meeting", "horse"])
    prices["field_size"] = prices.groupby("EventID")["horse"].transform("size")
    prices = prices[prices["field_size"] >= 4]
    prices["mkt_rank"] = prices.groupby("EventID")["MorningWAP"].rank(method="first")
    fav = prices.groupby("EventID")["MorningWAP"].transform("min")
    prices["gap_to_fav"] = np.log(prices["MorningWAP"] / fav)
    prices["is_fav"] = (prices["mkt_rank"] == 1).astype(int)
    return prices


def feature_frame() -> pd.DataFrame:
    """Form features from the Racing Post frame, joined to the market structure."""
    if os.path.exists(CACHE):
        df = pd.read_pickle(CACHE)
        print(f"loaded cached frame: {len(df):,} runners")
        return df
    if not os.path.exists(RP_DB):
        raise SystemExit(f"Racing Post db not found at {RP_DB}")

    print("building frame (BFSP + Racing Post form) ...")
    prices = build_frame()
    runners = categorise(add_history(load_runners("2021-01-01", "2026-09-17")))
    runners["meeting"] = runners["meeting"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    df = runners.merge(prices, on=["race_date", "meeting", "horse"], how="inner")

    df["lto_date"] = df.groupby("horse")["race_date"].shift(1)
    df["days_since"] = (pd.to_datetime(df["race_date"]) - pd.to_datetime(df["lto_date"])).dt.days
    df["runs_before"] = df.groupby("horse").cumcount()
    df["race_class_num"] = pd.to_numeric(
        df["race_class"].astype(str).str.extract(r"(\d)")[0], errors="coerce")
    title = df["race_title"].astype(str).str.lower()
    df["is_handicap"] = title.str.contains("handicap", na=False).astype(int)
    df["is_jumps"] = (df["code"] == "jumps").astype(int)
    df["cat_big_drop"] = (df["category"] == "Big Weight Drop").astype(int)
    df["cat_value"] = (df["category"] == "Value Qualifier").astype(int)
    df["cat_placed"] = (df["category"] == "Placed at Trip").astype(int)
    df["log_morning"] = np.log(df["MorningWAP"])
    df["move"] = df["BSP_TRUE"] / df["MorningWAP"] - 1.0
    df["steam"] = (df["move"] < -STEAM_TOL).astype(int)
    df.to_pickle(CACHE)
    print(f"built {len(df):,} runners -> {CACHE}")
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="ignore the cached frame")
    args = ap.parse_args()
    if args.rebuild and os.path.exists(CACHE):
        os.remove(CACHE)

    df = feature_frame()
    df = df[df["move"].notna() & df["log_morning"].notna()]
    df = add_derived_features(df)
    feats = FEATS + DERIVED
    print(f"features: {len(feats)} ({len(DERIVED)} new - within-race ranks, gap to the "
          f"field mean, course form)")
    train, test = df[df["race_date"] <= TRAIN_END], df[df["race_date"] >= TEST_START]
    print(f"train {len(train):,} ({train['race_date'].min()}..{train['race_date'].max()})  "
          f"test {len(test):,} ({test['race_date'].min()}..{test['race_date'].max()})")
    print(f"steam base rate: train {train['steam'].mean() * 100:.1f}%  "
          f"test {test['steam'].mean() * 100:.1f}%\n")

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    model = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.06,
                                           min_samples_leaf=200, random_state=0)
    model.fit(train[feats], train["steam"])
    test = test.assign(p=model.predict_proba(test[feats])[:, 1])

    base = test["steam"].mean()
    band = (test["log_morning"] > np.log(10)).astype(int)
    print("BASELINES (test set)")
    print(f"  always 'drift'              accuracy {(1 - base) * 100:>5.1f}%")
    print(f"  band only (drift if >10.0)  accuracy {(band == test['steam']).mean() * 100:>5.1f}%  "
          f"AUC {roc_auc_score(test['steam'], test['log_morning']) * 100:>5.1f}")
    print(f"\nMODEL  AUC {roc_auc_score(test['steam'], test['p']) * 100:.1f}  "
          f"accuracy {((test['p'] > 0.5) == test['steam']).mean() * 100:.1f}%")

    test["decile"] = pd.qcut(test["p"], 10, labels=False, duplicates="drop")
    cal = test.groupby("decile").agg(runners=("steam", "size"), predicted=("p", "mean"),
                                     actual=("steam", "mean"), median_move=("move", "median"))
    for c in ("predicted", "actual", "median_move"):
        cal[c] = (cal[c] * 100).round(1)
    print("\ncalibration, test set (decile 9 = model's most likely steamers)")
    print(cal.to_string())

    print("\nTHE TRADE on the model's picks (back morning, lay BSP, 2% commission, GBP2)")
    for label, g in (("all test runners", test), ("top decile", test[test["decile"] == 9]),
                     ("top 3 deciles", test[test["decile"] >= 7]),
                     ("bottom decile", test[test["decile"] == 0])):
        pl = (g["MorningWAP"] / g["BSP_TRUE"] - 1.0) * (1 - COMMISSION) * 2
        print(f"  {label:<18} n={len(g):>6}  mean {pl.mean():>+6.2f}  median {pl.median():>+6.2f}  "
              f"profitable {(pl > 0).mean() * 100:>4.0f}%  total GBP{pl.sum():>+10.2f}")

    print("\nBREAK-EVEN: how much execution cost the top decile can absorb")
    print("  (back price made s% shorter - a stand-in for the spread you pay to get filled)")
    top = test[test["decile"] == 9]
    for slip in (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15):
        pl = (top["MorningWAP"] * (1 - slip) / top["BSP_TRUE"] - 1.0) * (1 - COMMISSION) * 2
        print(f"  back price {slip * 100:>4.0f}% worse   mean GBP{pl.mean():>+6.2f} a trade   "
              f"median GBP{pl.median():>+6.2f}   profitable {(pl > 0).mean() * 100:>4.0f}%   "
              f"total GBP{pl.sum():>+9.2f}")

    print("\nON OUR OWN RULE PICKS (test set): does the model sort them?")
    for cat in ("Big Weight Drop", "Value Qualifier", "Placed at Trip"):
        g = test[test["category"] == cat]
        if len(g) < 200:
            continue
        hi = g[g["p"] >= g["p"].quantile(0.75)]
        lo = g[g["p"] < g["p"].quantile(0.25)]
        for label, s in (("  best quarter", hi), ("  worst quarter", lo)):
            pl = (s["MorningWAP"] / s["BSP_TRUE"] - 1.0) * (1 - COMMISSION) * 2
            print(f"  {cat:<18}{label:<14} n={len(s):>5}  mean GBP{pl.mean():>+6.2f}  "
                  f"total GBP{pl.sum():>+8.2f}  steam {s['steam'].mean() * 100:>4.0f}%")

    from sklearn.ensemble import HistGradientBoostingRegressor
    print("\nBY PREDICTED MOVE instead of predicted direction (regression on log BSP/morning)")
    reg = HistGradientBoostingRegressor(max_iter=250, learning_rate=0.06,
                                        min_samples_leaf=200, random_state=0)
    reg.fit(train[feats], np.log(train["BSP_TRUE"] / train["MorningWAP"]))
    test["pm"] = reg.predict(test[feats])
    print("  (negative means the price is expected to come in)")
    test["mdecile"] = pd.qcut(test["pm"], 10, labels=False, duplicates="drop") \
        if test["pm"].nunique() > 10 else 0
    for label, g in (("most likely to come in", test[test["mdecile"] == 0]),
                     ("top 3 deciles", test[test["mdecile"] <= 2]),
                     ("most likely to drift", test[test["mdecile"] == 9])):
        pl = (g["MorningWAP"] / g["BSP_TRUE"] - 1.0) * (1 - COMMISSION) * 2
        print(f"  {label:<24} n={len(g):>6}  predicted move {g['pm'].median() * 100:>+6.1f}%  "
              f"actual {((g['BSP_TRUE'] / g['MorningWAP'] - 1).median()) * 100:>+6.1f}%  "
              f"mean GBP{pl.mean():>+6.2f} a trade  profitable {(pl > 0).mean() * 100:>4.0f}%  "
              f"total GBP{pl.sum():>+9.2f}")

    print("\nBLEND: direction and magnitude together (rank average, lowest = best)")
    test["blend"] = test["p"].rank() - test["pm"].rank()
    test["bdecile"] = pd.qcut(test["blend"], 10, labels=False, duplicates="drop")
    for label, g in (("blend top decile", test[test["bdecile"] == 9]),
                     ("blend top 3 deciles", test[test["bdecile"] >= 7])):
        pl = (g["MorningWAP"] / g["BSP_TRUE"] - 1.0) * (1 - COMMISSION) * 2
        print(f"  {label:<24} n={len(g):>6}  mean GBP{pl.mean():>+6.2f} a trade  "
              f"median GBP{pl.median():>+6.2f}  profitable {(pl > 0).mean() * 100:>4.0f}%  "
              f"total GBP{pl.sum():>+9.2f}")
    for s in (0.04, 0.08, 0.12):
        g = test[test["bdecile"] == 9]
        pl = (g["MorningWAP"] * (1 - s) / g["BSP_TRUE"] - 1.0) * (1 - COMMISSION) * 2
        print(f"    blend top decile at {s * 100:.0f}% worse fills: mean GBP{pl.mean():+.2f} "
              f"total GBP{pl.sum():+.2f}")

    from sklearn.inspection import permutation_importance
    sample = test.sample(min(20000, len(test)), random_state=0)
    imp = permutation_importance(model, sample[feats], sample["steam"], n_repeats=3,
                                 random_state=0, scoring="roc_auc")
    print("\nwhat the model leans on (AUC lost when a column is shuffled)")
    for i in np.argsort(imp.importances_mean)[::-1][:10]:
        print(f"  {feats[i]:<24} {imp.importances_mean[i]:+.4f}")


if __name__ == "__main__":
    main()
