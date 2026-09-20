"""
HISTORICAL STEAM / DRIFT TEST
=============================
Does the morning -> off move exist over five years, and can it be traded?

Prices, both ends, from our own Betfair load (PRODB.dbo.BFSP):
  MorningWAP  the volume-weighted price in the morning
  BSP_TRUE    the Betfair Starting Price
  PPWAP       pre-post weighted average, as a second look

Picks come from the same harness as backtest_tips_history.py, so the categories are
the live ones (Big Weight Drop / Value Qualifier / Placed at Trip) regenerated on the
Racing Post frame from pre-race information only.

For each category:
  steam rate   share whose BSP came in shorter than the morning price
  the trade    back at MorningWAP, lay at BSP, full hedge, 2% commission
  the hold     the same each-way P&L taken at MorningWAP vs taken at BSP

    python scripts\\hist_steam_drift.py
    python scripts\\hist_steam_drift.py --from 2025-01-01
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_tips_history import (RP_DB, add_history, categorise, load_runners,  # noqa: E402
                                   settle)

pd.set_option("display.width", 220)
COMMISSION = 0.02
STEAM_TOL = 0.03          # inside 3% counts as solid, matching the live badge


def load_prices(date_from: str, date_to: str) -> pd.DataFrame:
    """MorningWAP / BSP / PPWAP per runner, keyed like the RP frame."""
    import pyodbc
    conn = pyodbc.connect(
        r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;"
        r"Database=PRODB;Trusted_Connection=yes;MultipleActiveResultSets=True;")
    sql = f"""
        SELECT CAST(RaceDate AS date) race_date, CourseClean, HorseClean,
               MorningWAP, BSP_TRUE, PPWAP
        FROM dbo.BFSP
        WHERE RaceDate >= '{date_from}' AND RaceDate <= '{date_to}'
    """
    df = pd.read_sql(sql, conn)
    conn.close()
    df["race_date"] = df["race_date"].astype(str)
    df["horse"] = df["HorseClean"].astype(str).str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    df["meeting"] = df["CourseClean"].astype(str).str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    for c in ("MorningWAP", "BSP_TRUE", "PPWAP"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["MorningWAP"].between(1.01, 1000) & df["BSP_TRUE"].between(1.01, 1000)]
    return df.drop_duplicates(["race_date", "meeting", "horse"])


def trade_pl(morning: pd.Series, bsp: pd.Series, stake: float = 1.0) -> pd.Series:
    """Back at the morning price, lay at BSP, full hedge, commission on the win."""
    return (morning / bsp - 1.0) * (1.0 - COMMISSION) * stake


def report(label: str, g: pd.DataFrame, stake: float = 2.0) -> None:
    n = len(g)
    if n < 20:
        print(f"  {label:<20} n={n} - too few to read")
        return
    move = (g["BSP_TRUE"] / g["MorningWAP"] - 1.0) * 100
    trade = trade_pl(g["MorningWAP"], g["BSP_TRUE"], stake)
    print(f"  {label:<20} n={n:>6}  steam {(move < -STEAM_TOL * 100).mean() * 100:>4.0f}%  "
          f"median move {move.median():>+7.1f}%  |  trade mean {trade.mean():>+6.2f} "
          f"median {trade.median():>+6.2f}  profitable {(trade > 0).mean() * 100:>4.0f}%  "
          f"total GBP{trade.sum():>+9.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2021-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-09-17")
    ap.add_argument("--stake", type=float, default=2.0)
    args = ap.parse_args()

    if not os.path.exists(RP_DB):
        print(f"Racing Post db not found at {RP_DB}")
        return

    print(f"loading runners {args.date_from} -> {args.date_to}")
    runners = add_history(load_runners(args.date_from, args.date_to))
    runners = categorise(runners)
    runners["meeting"] = runners["meeting"].astype(str).str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    prizes = load_prices(args.date_from, args.date_to)
    print(f"  {len(runners):,} runners, {len(prizes):,} priced runners")

    df = runners.merge(prizes[["race_date", "meeting", "horse", "MorningWAP", "BSP_TRUE", "PPWAP"]],
                       on=["race_date", "meeting", "horse"], how="inner")
    print(f"  {len(df):,} matched with both morning and off prices\n")

    print("STEAM / DRIFT, morning -> off (exchange both ends)")
    report("all runners", df, args.stake)
    for cat in ("Big Weight Drop", "Value Qualifier", "Placed at Trip"):
        report(cat, df[df["category"] == cat], args.stake)

    print("\nTHE TRADE by morning price band, all runners (GBP2 a trade)")
    for lo, hi in ((1, 3), (3, 6), (6, 12), (12, 25), (25, 1000)):
        g = df[df["MorningWAP"].between(lo, hi, inclusive="left")]
        report(f"{lo}-{hi}", g, args.stake)

    print("\nTHE HOLD: each-way P&L at the morning price vs at BSP (1u win + 1u place)")
    for name, g in [("all runners", df)] + [(c, df[df["category"] == c])
                                            for c in ("Big Weight Drop", "Value Qualifier", "Placed at Trip")]:
        if len(g) < 20:
            continue
        priced = g[g["MorningWAP"].notna() & g["BSP_TRUE"].notna()].copy()
        priced["sp"] = priced["MorningWAP"]
        at_morning = settle(priced, "sp", "_m")
        priced["sp"] = priced["BSP_TRUE"]
        at_bsp = settle(priced, "sp", "_b")
        m_stake = 2 * len(priced)
        print(f"  {name:<20} n={len(priced):>6}  "
              f"morning {at_morning['ew_pl_m'].sum() / m_stake * 100:>+6.1f}%  "
              f"BSP {at_bsp['ew_pl_b'].sum() / m_stake * 100:>+6.1f}%  "
              f"edge {(at_morning['ew_pl_m'].sum() - at_bsp['ew_pl_b'].sum()) / m_stake * 100:>+5.1f}pp")


if __name__ == "__main__":
    main()
