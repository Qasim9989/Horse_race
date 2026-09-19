"""
HISTORICAL TEST OF THE "TOP-3 POWER PICKS" STRATEGY
===================================================
The app's Racecard view ranks every runner by

    Power_Score = 0.35*Best_TS + 0.35*Avg_TS3 + 0.30*Best_RPR - 0.15*Weight_lbs

and shows the top three as the 1st / 2nd / 3rd Place cards.  The idea being
tested here is "back all three each-way - normally one of them places, so the
one placer covers the two losers".

Everything is built from a horse's PRIOR runs only (no look-ahead), on the
Racing Post database, and settled each-way at the Racing Post SP and at the
real Betfair BSP (PRODB.dbo.BFSP).

Break-even for the idea, with 1u win + 1u place on each of 3 picks (6u staked):
a single placer must return (odds-1)*fraction >= 4u, i.e. about 21.0 at 1/5 or
17.0 at 1/4 - two placers still lose unless each pays ~11.0.

    python scripts\\backtest_power_rank_top3.py
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)

spec = importlib.util.spec_from_file_location(
    "tips_bt", os.path.join(HERE, "backtest_tips_history.py"))
assert spec is not None and spec.loader is not None  # local file, always present
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def add_power(df: pd.DataFrame) -> pd.DataFrame:
    """Prior-run features and the app's Power_Score, per runner."""
    df = df.sort_values(["horse", "race_date", "race_time"]).copy()
    grouped = df.groupby("horse", sort=False)
    df["best_ts"] = grouped["topspeed"].transform(lambda s: s.cummax().shift(1))
    df["avg_ts3"] = grouped["topspeed"].transform(lambda s: s.shift(1).rolling(3).mean())
    df["best_rpr"] = grouped["rpr"].transform(lambda s: s.cummax().shift(1))

    df["power"] = (0.35 * df["best_ts"].fillna(0)
                   + 0.35 * df["avg_ts3"].fillna(0)
                   + 0.30 * df["best_rpr"].fillna(0)
                   - 0.15 * df["weight_lbs"].fillna(0)).round(1)
    # a pick needs a real prior record on all three inputs, like the app's cards
    df["scored"] = (df["best_ts"].notna() & df["avg_ts3"].notna()
                    & df["best_rpr"].notna() & df["weight_lbs"].notna())
    return df


def top3(df: pd.DataFrame) -> pd.DataFrame:
    ranked = df[df["scored"]].sort_values(["race_id", "power"], ascending=[True, False])
    return ranked.groupby("race_id", as_index=False).head(3)


def summarise(label: str, rows: pd.DataFrame, lines: list, price: str) -> None:
    rows = rows.dropna(subset=[price, "pos"])
    if rows.empty:
        lines.append(f"  {label:<26} no settled picks")
        return
    n = len(rows)
    wins = int(rows["won"].sum())
    places = int(rows["placed"].sum())
    pl = float(rows[f"ew_pl_{price}"].sum())
    lines.append(f"  {label:<26} picks {n:>7,}  win% {wins / n * 100:>5.1f}  "
                 f"place% {places / n * 100:>5.1f}  EW ROI {pl / (n * 2) * 100:>+6.1f}%")


def race_level(df: pd.DataFrame, lines: list, price: str, label: str) -> None:
    """The 'one of the three places' claim, measured per race."""
    rows = df.dropna(subset=[price, "pos"])
    if rows.empty:
        return
    per_race = rows.groupby("race_id").agg(
        picks=("pos", "size"), won=("won", "sum"), placed=("placed", "sum"),
        pl=("ew_pl_" + price, "sum"))
    staked = per_race["picks"] * 2.0
    lines.append(f"\n  {label}")
    lines.append(f"    races            {len(per_race):,}")
    lines.append(f"    races with >=1 winner  {int((per_race['won'] >= 1).sum()):,} "
                 f"({(per_race['won'] >= 1).mean() * 100:.1f}%)")
    lines.append(f"    races with >=1 placer  {int((per_race['placed'] >= 1).sum()):,} "
                 f"({(per_race['placed'] >= 1).mean() * 100:.1f}%)   <- the claim")
    lines.append(f"    races where the placer covered the other two (P/L >= 0) "
                 f"{int((per_race['pl'] >= 0).sum()):,} ({(per_race['pl'] >= 0).mean() * 100:.1f}%)")
    lines.append(f"    P/L per race     {per_race['pl'].mean() / staked.mean() * 100:+.1f}% of "
                 f"the {staked.mean():.1f}u staked")



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2021-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-09-17")
    args = ap.parse_args()

    print(f"Loading Racing Post runners {args.date_from} -> {args.date_to} ...")
    df = bt.load_runners(args.date_from, args.date_to)
    print(f"  {len(df):,} runners in {df['race_id'].nunique():,} races")
    print("Loading real Betfair BSP ...")
    df = df.merge(bt.load_bsp(args.date_from, args.date_to),
                  on=["race_date", "horse"], how="left")

    df = add_power(df)
    picks = top3(df)
    picks = bt.settle(picks, "sp", "_sp")
    picks = bt.settle(picks, "bsp", "_bsp")
    races = picks["race_id"].nunique()
    print(f"  {len(picks):,} top-3 picks in {races:,} races "
          f"({len(picks) / max(races, 1):.1f} per race)")

    lines = ["=" * 104,
             "  TOP-3 POWER PICKS, EACH-WAY (1u win + 1u place on each of three picks)",
             "=" * 104,
             "\n  POOLED"]
    summarise("top-3 at Racing Post SP", picks, lines, "sp")
    summarise("top-3 at Betfair BSP", picks, lines, "bsp")
    lines.append("\n  BY YEAR - Betfair BSP")
    for year in sorted(picks["race_date"].str[:4].unique()):
        summarise(str(year), picks[picks["race_date"].str[:4] == year], lines, "bsp")
    lines.append("\n  BY YEAR - Racing Post SP")
    for year in sorted(picks["race_date"].str[:4].unique()):
        summarise(str(year), picks[picks["race_date"].str[:4] == year], lines, "sp")

    race_level(picks, lines, "bsp", "AT BETFAIR BSP - the 'one placer covers two losers' claim")

    lines.append("\n  WHICH CARD MATTERS (Betfair BSP)")
    ranked = picks.copy()
    ranked["rank"] = ranked.groupby("race_id")["power"].rank(ascending=False, method="first")
    for rank in (1, 2, 3):
        summarise(f"card #{int(rank)}", ranked[ranked["rank"] == rank], lines, "bsp")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
