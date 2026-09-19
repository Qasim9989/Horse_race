"""
DAILY SYSTEM REPORT - one row per day, per system and per tip category
=====================================================================
Reads cloud_app/results_ledger.csv (the file the tabs log picks into and
settle_daily_results.py fills with outcomes) and answers "is this working?"
by day rather than by impression:

  * per day x system: bets, settled, wins, places, EW P/L, running total
  * per day x tip category: the same, so the weight-drop / value / placed-at-trip
    buckets can be compared against each other and against Power Rank #1

P/L is each-way on 1 unit win + 1 unit place per pick at the recorded early
price (and at SP for comparison), exactly as the settlement engine computes it.

    python scripts\\daily_system_report.py                 # last 14 days
    python scripts\\daily_system_report.py --days 30
    python scripts\\daily_system_report.py --out reports\\daily_system_report.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_CSV = os.path.join(PROJECT_DIR, "cloud_app", "results_ledger.csv")
DEFAULT_OUT = os.path.join(PROJECT_DIR, "reports", "daily_system_report.csv")

PENDING_MARKERS = ("", "-", "nan", "none", "⏳ running today", "pending")


def load_ledger() -> pd.DataFrame:
    if not os.path.exists(LEDGER_CSV):
        raise SystemExit(f"ledger not found: {LEDGER_CSV}")
    df = pd.read_csv(LEDGER_CSV)
    df["race_date"] = df["race_date"].astype(str)
    for column in ("won", "placed", "early_ew_pl", "sp_ew_pl", "early_win_pl",
                   "sp_win_pl", "early_odds", "sp_odds"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    position = df.get("finish_pos", pd.Series("", index=df.index)).astype(str).str.strip().str.lower()
    df["settled"] = ~position.isin(PENDING_MARKERS)
    df["void"] = position.str.contains("void", na=False)
    return df


def summarise(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Bets / wins / places / P&L per group, settled rows only."""
    live = df[df["settled"] & ~df["void"]]
    if live.empty:
        return pd.DataFrame()
    grouped = live.groupby(keys, dropna=False)
    out = grouped.agg(
        bets=("horse_name", "size"),
        wins=("won", "sum"),
        places=("placed", "sum"),
        staked=("won", lambda s: len(s) * 2.0),
        ew_pl=("early_ew_pl", "sum"),
        ew_pl_sp=("sp_ew_pl", "sum"),
    ).reset_index()
    out["strike_pct"] = (out["wins"] / out["bets"] * 100).round(1)
    out["roi_pct"] = (out["ew_pl"] / out["staked"] * 100).round(1)
    out["roi_sp_pct"] = (out["ew_pl_sp"] / out["staked"] * 100).round(1)
    out["cum_ew_pl"] = out["ew_pl"].cumsum().round(2)
    return out


def show(title: str, table: pd.DataFrame, labels: list[str]) -> None:
    print("\n" + "=" * 116)
    print(f"  {title}")
    print("=" * 116)
    if table.empty:
        print("  nothing settled yet")
        return
    print(f"  {labels[0]:<12} {labels[1]:<24} {'bets':>5} {'won':>5} {'placed':>7} "
          f"{'strike':>7} {'EW P/L':>10} {'ROI%':>7} {'ROI%@SP':>8} {'cum':>10}")
    print("  " + "-" * 112)
    for _, row in table.iterrows():
        print(f"  {str(row[labels[0]])[:12]:<12} {str(row[labels[1]])[:24]:<24} "
              f"{int(row['bets']):>5} {int(row['wins']):>5} {int(row['places']):>7} "
              f"{row['strike_pct']:>6.1f}% {row['ew_pl']:>+10.2f} {row['roi_pct']:>+6.1f}% "
              f"{row['roi_sp_pct']:>+7.1f}% {row['cum_ew_pl']:>+10.2f}")


def totals(df: pd.DataFrame, label: str) -> None:
    live = df[df["settled"] & ~df["void"]]
    if live.empty:
        return
    pl = live["early_ew_pl"].sum()
    stake = len(live) * 2.0
    print(f"\n  {label}: {len(live)} settled bets | {int(live['won'].sum())} won | "
          f"{int(live['placed'].sum())} placed | EW P/L {pl:+.2f} on {stake:.0f} staked "
          f"({pl / stake * 100:+.1f}%) | at SP {live['sp_ew_pl'].sum():+.2f}")



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    ledger = load_ledger()
    cutoff = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    window = ledger[ledger["race_date"] >= cutoff].copy()
    if window.empty:
        print(f"No ledger rows on or after {cutoff}.")
        return 1

    print(f"DAILY SYSTEM REPORT   {cutoff} -> {dt.date.today().isoformat()}   "
          f"({len(window)} ledger rows, {int(window['settled'].sum())} settled)")
    print("P/L = each-way (1u win + 1u place) at the recorded early price; "
          "ROI% is on the 2u EW stake.")

    by_day_system = summarise(window, ["race_date", "system_name"])
    show("PER DAY x SYSTEM", by_day_system, ["race_date", "system_name"])

    tips = window[window["system_name"] == "Tips"]
    by_day_category = summarise(tips, ["race_date", "sub_system"])
    show("PER DAY x TIP CATEGORY", by_day_category, ["race_date", "sub_system"])

    print("\n" + "=" * 116)
    print("  TOTALS FOR THE WINDOW")
    print("=" * 116)
    for system_name, group in window.groupby("system_name"):
        totals(group, f"{system_name:<18}")
    for category, group in tips.groupby("sub_system"):
        totals(group, f"Tips {str(category)[:16]:<16}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    combined = pd.concat(
        [by_day_system.assign(group_type="system"),
         by_day_category.assign(group_type="tip_category")],
        ignore_index=True,
    )
    combined.to_csv(args.out, index=False)
    print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
