"""
WHAT WOULD FIXING THE TIPS FLAWS DO TO THE ROI?
==============================================
Ablation over 2021-01-01 to 2026-09-17 on the Racing Post data.  Each variant
adds one fix to the live rule, so the ROI move can be attributed:

  V0  the live rule as it runs today
        - trip record matched on the first 2 characters of today's distance
          ("1m" catches 1m2f and 1m4f; the "6x Trip Placed" inflation)
        - weight delta computed even when the last run was a different code
          (a hurdler dropping to the Flat shows a fake -30lb)
        - LTO finishing position 1-4 counts as "in form" (4th qualifies)
        - no recency check (a 3-month-old run still qualifies)
  V1  + exact distance matching for the trip record
  V2  + weight delta only when the last run was the same code (Flat/Jumps)
  V3  + LTO position must be 1-3 (4th no longer "in form")
  V4  + the last run must be within 60 days
  V5  + the value branch also needs best TS >= 60 (as the other two already do)

Settlement is each-way at the Racing Post SP and at real Betfair BSP.  The
frame's own all-runner baseline is printed first - compare against that, not
zero.

    python scripts\\backtest_tips_variants.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("tips_bt", os.path.join(HERE, "backtest_tips_history.py"))
assert spec is not None and spec.loader is not None  # local file, always present
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

CATEGORIES = ("Big Weight Drop", "Value Qualifier", "Placed at Trip")


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """LTO fields, prior ratings, and trip counts both ways (live vs exact)."""
    df = df.sort_values(["horse", "race_date", "race_time"]).copy()
    horse = df.groupby("horse", sort=False)

    for column in ("weight_lbs", "pos", "official_rating", "code", "distance", "race_date"):
        df[f"lto_{column}"] = horse[column].shift(1)
    df["has_lto"] = df["lto_race_date"].notna()
    df["days_since"] = (pd.to_datetime(df["race_date"]) - pd.to_datetime(df["lto_race_date"])).dt.days

    df["prior_best_ts"] = horse["topspeed"].transform(lambda s: s.cummax().shift(1))
    winning_or = df["official_rating"].where(df["pos"] == 1)
    df["last_win_or"] = winning_or.groupby(df["horse"]).ffill().groupby(df["horse"]).shift(1)

    # exact-distance trip record: wins / placings at the identical distance string
    for label, series in (("wins", (df["pos"] == 1).astype(int)),
                          ("places", df["pos"].isin((2, 3)).astype(int))):
        grouped = series.groupby([df["horse"], df["distance"]]).cumsum()
        df[f"trip_{label}_exact"] = grouped.groupby([df["horse"], df["distance"]]).shift(1).fillna(0)

    # the live rule matches on the first 2 characters anywhere in the distance text
    prefixes = df["distance"].str[:2].value_counts().index[:30]
    cnt_wins, cnt_places, prefix_codes = {}, {}, {p: i for i, p in enumerate(prefixes)}
    for prefix in prefixes:
        mask = df["distance"].str.contains(prefix, regex=False)
        cum = mask.groupby(df["horse"]).cumsum().groupby(df["horse"]).shift(1).fillna(0)
        cnt_wins[prefix] = cum.where(mask, 0).values
        # placings need the position flag as well
        placed = (mask & df["pos"].isin((1, 2, 3)))
        cum_p = placed.groupby(df["horse"]).cumsum().groupby(df["horse"]).shift(1).fillna(0)
        cnt_places[prefix] = cum_p.where(mask, 0).values

    codes = np.asarray(df["distance"].str[:2].map(prefix_codes).fillna(-1).values, dtype=float)
    if len(prefixes) > 0:
        win_matrix = np.column_stack([np.asarray(cnt_wins[p], dtype=float) for p in prefixes])
        place_matrix = np.column_stack([np.asarray(cnt_places[p], dtype=float) for p in prefixes])
    else:
        win_matrix = np.zeros((len(df), 1))
        place_matrix = np.zeros((len(df), 1))
    safe = np.clip(codes, 0, max(len(prefixes) - 1, 0)).astype(int)
    rows = np.arange(len(df))
    df["trip_wins_live"] = win_matrix[rows, safe] if len(prefixes) else 0
    df["trip_places_live"] = place_matrix[rows, safe] if len(prefixes) else 0

    df["same_code"] = df["code"] == df["lto_code"]
    return df



def categorise(df: pd.DataFrame, live_trip: bool, same_code_only: bool,
               pos_max: int, recency_days, ts_floor_value: bool) -> pd.Series:
    """The rule, with the fixes switched on or off."""
    trip_placings = (df["trip_wins_exact"] + df["trip_places_exact"] if not live_trip
                     else df["trip_wins_live"] + df["trip_places_live"])
    if same_code_only:
        delta = pd.Series(np.where(df["same_code"], df["weight_lbs"] - df["lto_weight_lbs"], np.nan),
                          index=df.index)
    else:
        delta = df["weight_lbs"] - df["lto_weight_lbs"]
    best_ts = df["prior_best_ts"].fillna(0)
    lto_pos = df["lto_pos"]
    marks = (df["lto_official_rating"].notna() & df["last_win_or"].notna()
             & (df["lto_official_rating"] <= df["last_win_or"]))
    in_form = lto_pos.isin(tuple(range(1, pos_max + 1)))
    fresh = (df["days_since"] <= recency_days) if recency_days else pd.Series(True, index=df.index)

    big = (delta <= -8) & (best_ts >= 60)
    value = (~big) & ((delta < 0) | marks) & (trip_placings >= 1) & in_form
    if ts_floor_value:
        value = value & (best_ts >= 60)
    value = value & fresh
    placed_trip = ((~big) & (~value) & (trip_placings >= 3)
                   & lto_pos.isin((2, 3)) & (best_ts >= 60) & fresh)

    return pd.Series(np.select([big, value, placed_trip], list(CATEGORIES), default=""),
                     index=df.index)


VARIANTS: list[tuple[str, dict[str, Any]]] = [
    ("V0  live rule", {"live_trip": True, "same_code_only": False, "pos_max": 4,
                       "recency_days": None, "ts_floor_value": False}),
    ("V1  + exact trip", {"live_trip": False, "same_code_only": False, "pos_max": 4,
                          "recency_days": None, "ts_floor_value": False}),
    ("V2  + same-code weight", {"live_trip": False, "same_code_only": True, "pos_max": 4,
                                "recency_days": None, "ts_floor_value": False}),
    ("V3  + LTO 1-3 only", {"live_trip": False, "same_code_only": True, "pos_max": 3,
                            "recency_days": None, "ts_floor_value": False}),
    ("V4  + 60-day recency", {"live_trip": False, "same_code_only": True, "pos_max": 3,
                              "recency_days": 60, "ts_floor_value": False}),
    ("V5  + TS floor on value", {"live_trip": False, "same_code_only": True, "pos_max": 3,
                                 "recency_days": 60, "ts_floor_value": True}),
]


def line(label: str, rows: pd.DataFrame, price: str, width: int = 30) -> str:
    rows = rows.dropna(subset=[price, "pos"])
    if rows.empty:
        return f"  {label:<{width}} no bets"
    n = len(rows)
    wins = int((rows["pos"] == 1).sum())
    places = int(rows["placed"].sum())
    pl = float(rows[f"ew_pl_{price}"].sum())
    return (f"  {label:<{width}} bets {n:>7,}  win% {wins / n * 100:>5.1f}  "
            f"place% {places / n * 100:>5.1f}  ROI {pl / (n * 2) * 100:>+6.1f}%")


def main() -> int:
    print("Loading Racing Post runners 2021-01-01 -> 2026-09-17 ...")
    df = bt.load_runners("2021-01-01", "2026-09-17")
    print(f"  {len(df):,} runners in {df['race_id'].nunique():,} races")
    df = df.merge(bt.load_bsp("2021-01-01", "2026-09-17"), on=["race_date", "horse"], how="left")
    df = add_features(df)
    df = bt.settle(df, "sp", "_sp")
    df = bt.settle(df, "bsp", "_bsp")

    out = ["=" * 108,
           "  TIPS RULE - WHAT FIXING THE FLAWS DOES (Racing Post data, 2021-2026)",
           "  each-way, 1u win + 1u place; place terms from field size", "=" * 108]
    out.append(line("every runner (SP baseline)", df, "sp"))
    out.append(line("every runner (BSP baseline)", df, "bsp"))

    for name, flags in VARIANTS:
        frame = df.assign(category=categorise(df, **flags))
        scored = frame[frame["category"] != ""]
        out.append("")
        out.append(name)
        out.append(line("   all categories @BSP", scored, "bsp"))
        out.append(line("   all categories @SP", scored, "sp"))
        for category in CATEGORIES:
            out.append(line(f"   {category} @BSP", scored[scored["category"] == category], "bsp"))

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
