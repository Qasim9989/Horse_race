"""
HISTORICAL TEST OF THE TIPS RULE (weight drops / value qualifiers)
=================================================================
The Tips screen has never been measured historically - this does it on the
Racing Post database (686,730 runners, 2021-01-01 -> 2026-09-17), using only
information available BEFORE each race:

  * LTO   - the horse's previous run: weight, finish position, official rating
  * trip  - the same distance, matched exactly (the live rule uses a 2-character
            substring, so "1m" wrongly counts 1m2f and 1m4f as the same trip)
  * weight delta is only computed when the previous run was the SAME CODE
            (Flat vs Jumps).  Otherwise a hurdler switching to the Flat shows a
            fake -30 lb "drop" and gets flagged as a bet.

Categories, exactly as the cache builds them:
  Big Weight Drop  delta <= -8 lb and best TS >= 60
  Value Qualifier  (delta < 0 or LTO OR <= last-win OR) and trip placings >= 1
                   and LTO finish 1-4
  Placed at Trip   trip placings >= 3 and LTO finish 2-3 and best TS >= 60

Settlement: each-way, 1u win + 1u place at the Racing Post SP, place terms from
the field size (16+ handicap 4 places at 1/4, 8+ 3 places at 1/5, else 2 at 1/4).

    python scripts\\backtest_tips_history.py
    python scripts\\backtest_tips_history.py --from 2024-01-01
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import numpy as np
import pandas as pd

RP_DB = r"D:\RacingPost_Horse\racingpost_master.db"

JUMP_WORDS = ("hurdle", "chase", "bumper", "nh flat", "national hunt", "hunter",
              "steeplechase", "point-to-point", "juvenile")


def parse_sp(value):
    """'7/2' / '1/4F' / 'Evs' -> decimal."""
    text = str(value or "").strip().lower().replace("f", "").replace("j", "")
    if not text:
        return None
    if text in ("evs", "evens", "1/1"):
        return 2.0
    match = re.match(r"^(\d+)\s*/\s*(\d+)", text)
    if match:
        num, den = float(match.group(1)), float(match.group(2))
        return round(1.0 + num / den, 2) if den else None
    try:
        number = float(text)
        return number if number > 1 else None
    except ValueError:
        return None


def parse_pos(value):
    """'1'..'9' -> int; F/PU/UR/blank -> None."""
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def load_runners(date_from: str, date_to: str) -> pd.DataFrame:
    import sqlite3
    sql = f"""
        SELECT race_id, horse_name, race_date, race_time, race_title, race_class,
               meeting, distance, country, finish_pos, weight_lbs, official_rating,
               topspeed, rpr, sp_odds
        FROM race_results
        WHERE race_date >= '{date_from}' AND race_date <= '{date_to}'
          AND horse_name IS NOT NULL AND horse_name != ''
    """
    conn = sqlite3.connect(RP_DB)
    df = pd.read_sql(sql, conn)
    conn.close()

    df["horse"] = df["horse_name"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    df["distance"] = df["distance"].astype(str).str.strip().str.lower()
    df["race_date"] = df["race_date"].astype(str)
    df["pos"] = df["finish_pos"].map(parse_pos)
    df["sp"] = df["sp_odds"].map(parse_sp)
    for column in ("weight_lbs", "official_rating", "topspeed", "rpr"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    title = df["race_title"].astype(str).str.lower()
    df["code"] = np.where(title.str.contains("|".join(JUMP_WORDS), na=False), "jumps", "flat")
    df["field"] = df.groupby("race_id")["horse"].transform("size")
    handicap = df["race_title"].astype(str).str.lower().str.contains("handicap", na=False)
    df["places_paid"] = np.where((df["field"] >= 16) & handicap, 4,
                                 np.where(df["field"] >= 8, 3, 2))
    return df



def load_bsp(date_from: str, date_to: str) -> pd.DataFrame:
    """Real Betfair BSP from PRODB.dbo.BFSP (ours, loaded from Betfair's files)."""
    import pyodbc
    conn = pyodbc.connect(
        r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;"
        r"Database=PRODB;Trusted_Connection=yes;MultipleActiveResultSets=True;")
    sql = f"""
        SELECT RaceDate, HorseClean, BSP_TRUE
        FROM dbo.BFSP
        WHERE RaceDate >= '{date_from}' AND RaceDate <= '{date_to}' AND BSP_TRUE > 1
    """
    df = pd.read_sql(sql, conn)
    conn.close()
    df["race_date"] = df["RaceDate"].astype(str)
    df["horse"] = df["HorseClean"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    df["bsp"] = pd.to_numeric(df["BSP_TRUE"], errors="coerce")
    return df[["race_date", "horse", "bsp"]].drop_duplicates(["race_date", "horse"])


def add_history(df: pd.DataFrame) -> pd.DataFrame:
    """LTO fields, prior best TS, prior last-win OR, and prior trip record."""
    df = df.sort_values(["horse", "race_date", "race_time"]).copy()
    horse = df.groupby("horse", sort=False)

    for column in ("weight_lbs", "pos", "official_rating", "code", "distance"):
        df[f"lto_{column}"] = horse[column].shift(1)
    df["has_lto"] = horse["race_date"].shift(1).notna()

    df["prior_best_ts"] = df.groupby("horse")["topspeed"].transform(
        lambda s: s.cummax().shift(1))
    winning_or = df["official_rating"].where(df["pos"] == 1)
    df["last_win_or"] = winning_or.groupby(df["horse"]).ffill().groupby(df["horse"]).shift(1)

    for label, series in (("wins", (df["pos"] == 1).astype(int)),
                          ("places", df["pos"].isin((2, 3)).astype(int))):
        grouped = series.groupby([df["horse"], df["distance"]]).cumsum()
        df[f"trip_{label}"] = grouped.groupby([df["horse"], df["distance"]]).shift(1).fillna(0)

    df["same_code"] = df["code"] == df["lto_code"]
    df["delta_wgt"] = np.where(df["same_code"], df["weight_lbs"] - df["lto_weight_lbs"], np.nan)
    return df


def categorise(df: pd.DataFrame) -> pd.DataFrame:
    """The cache's if/elif chain, in the same order."""
    delta = df["delta_wgt"]
    best_ts = df["prior_best_ts"].fillna(0)
    trip_placings = df["trip_wins"].fillna(0) + df["trip_places"].fillna(0)
    lto_pos = df["lto_pos"]
    below_win_mark = ((delta < 0)
                      | (df["lto_official_rating"].notna() & df["last_win_or"].notna()
                         & (df["lto_official_rating"] <= df["last_win_or"])))

    big_drop = (delta <= -8) & (best_ts >= 60)
    value = (~big_drop) & below_win_mark & (trip_placings >= 1) & lto_pos.isin((1, 2, 3, 4))
    placed_at_trip = ((~big_drop) & (~value) & (trip_placings >= 3)
                      & lto_pos.isin((2, 3)) & (best_ts >= 60))

    df = df.copy()
    df["category"] = np.select([big_drop, value, placed_at_trip],
                               ["Big Weight Drop", "Value Qualifier", "Placed at Trip"],
                               default="")
    return df


def settle(df: pd.DataFrame, price_col: str = "sp", tag: str = "") -> pd.DataFrame:
    """Each-way P&L at `price_col`: 1u win + 1u place, terms from the field size."""
    df = df.copy()
    price = df[price_col]
    fraction = np.where(df["places_paid"] >= 4, 0.25, 0.20)
    place_odds = 1.0 + (price - 1.0) * fraction
    won = df["pos"] == 1
    placed = df["pos"].notna() & (df["pos"] <= df["places_paid"])
    df["won"] = won
    df["placed"] = placed
    df[f"ew_pl{tag}"] = np.select([won, placed],
                                  [(price - 1.0) + (place_odds - 1.0),
                                   (place_odds - 1.0) - 1.0],
                                  default=-2.0)
    return df


def report(label: str, rows: pd.DataFrame, lines: list, price_col: str = "sp",
           tag: str = "", need: str | None = None) -> None:
    drop_cols = ["pos"]
    if price_col != "sp":
        drop_cols.append(price_col)
    rows = rows.dropna(subset=drop_cols)
    if need:
        rows = rows.dropna(subset=[need])
    if rows.empty:
        lines.append(f"  {label:<20} no settled bets")
        return
    n = len(rows)
    wins = int(rows["won"].sum())
    places = int(rows["placed"].sum())
    pl = float(rows[f"ew_pl{tag}"].sum())
    stake = n * 2.0
    lines.append(f"  {label:<20} bets {n:>7,}  win% {wins / n * 100:>5.1f}  "
                 f"place% {places / n * 100:>5.1f}  EW P/L {pl:>+10.1f}  "
                 f"ROI {pl / stake * 100:>+6.1f}%")




def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2021-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-09-17")
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reports", "tips_history_backtest.csv"))
    args = ap.parse_args()

    print(f"Loading Racing Post runners {args.date_from} -> {args.date_to} ...")
    df = load_runners(args.date_from, args.date_to)
    print(f"  {len(df):,} runners in {df['race_id'].nunique():,} races")

    print("Loading real Betfair BSP for the same window ...")
    bsp = load_bsp(args.date_from, args.date_to)
    print(f"  {len(bsp):,} priced runners")
    df = df.merge(bsp, on=["race_date", "horse"], how="left")

    df = categorise(add_history(df))
    df = settle(df, "sp", "")
    df = settle(df, "bsp", "_bsp")
    scored = df[df["category"] != ""].copy()
    matched = int(scored["bsp"].notna().sum())
    print(f"  {len(scored):,} categorised picks "
          f"({len(scored) / max(len(df), 1) * 100:.1f}% of runners)")
    print(f"  matched to a real Betfair BSP: {matched:,} ({matched / max(len(scored), 1) * 100:.1f}%)")

    cats = ("Big Weight Drop", "Value Qualifier", "Placed at Trip")
    lines: list = ["=" * 104,
                   "  TIPS RULE, MEASURED HISTORICALLY - Racing Post SP vs real Betfair BSP",
                   "  each-way, 1u win + 1u place; place terms from field size",
                   "=" * 104,
                   "\n  CONTROLS (Racing Post SP)"]
    report("every runner", df, lines)
    report("any runner with LTO", df[df["has_lto"]], lines)

    lines.append("\n  AT RACING POST SP (all picks)")
    for category in cats:
        report(category, scored[scored["category"] == category], lines)

    lines.append(f"\n  AT BETFAIR BSP (the same picks, {matched:,} priced at BSP)")
    for category in cats:
        report(category, scored[scored["category"] == category], lines,
               price_col="bsp", tag="_bsp", need="bsp")

    lines.append("\n  SIDE BY SIDE - BSP only")
    for category in cats:
        subset = scored[scored["category"] == category].dropna(subset=["bsp", "pos"])
        if subset.empty:
            continue
        n = len(subset)
        sp_roi = subset["ew_pl"].sum() / (n * 2) * 100
        bsp_roi = subset["ew_pl_bsp"].sum() / (n * 2) * 100
        lines.append(f"  {category:<20} bets {n:>6,}   SP {sp_roi:>+6.1f}%   "
                     f"BSP {bsp_roi:>+6.1f}%   BSP advantage {bsp_roi - sp_roi:>+5.1f} pts")

    lines.append("\n  BY YEAR AT BETFAIR BSP")
    for year in sorted(scored["race_date"].str[:4].unique()):
        lines.append(f"\n  --- {year} ---")
        year_rows = scored[scored["race_date"].str[:4] == year]
        for category in cats:
            report(category, year_rows[year_rows["category"] == category], lines,
                   price_col="bsp", tag="_bsp", need="bsp")

    print("\n".join(lines))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    settled = scored.dropna(subset=["sp", "pos"]).copy()
    settled["year"] = settled["race_date"].str[:4]
    table = (settled.groupby(["year", "category"])
             .agg(bets=("ew_pl", "size"), wins=("won", "sum"), places=("placed", "sum"),
                  ew_pl_sp=("ew_pl", "sum"))
             .reset_index())
    bsp_rows = settled.dropna(subset=["bsp"])
    bsp_table = (bsp_rows.groupby(["year", "category"])
                 .agg(bets_bsp=("ew_pl_bsp", "size"), ew_pl_bsp=("ew_pl_bsp", "sum"))
                 .reset_index())
    table = table.merge(bsp_table, on=["year", "category"], how="left")
    table["roi_sp_pct"] = (table["ew_pl_sp"] / (table["bets"] * 2.0) * 100).round(1)
    table["roi_bsp_pct"] = (table["ew_pl_bsp"] / (table["bets_bsp"] * 2.0) * 100).round(1)
    table.to_csv(args.out, index=False)
    print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
