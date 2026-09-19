"""
SYSTEM STATS - monthly P&L, drawdown, losing runs, expectancy
============================================================
The numbers you need before staking anything, for:

  * Our System's forward book - 1,759 real bets at the price taken and at BSP
  * Power Rank card #1 - the historical rule (Racing Post data, 2021-2026)
  * Big Weight Drop - the one Tips bucket with an edge
  * the field itself - the baseline every system has to beat

Drawdown is in stake units and as a percentage of turnover; losing runs are in
bets.  Level stakes, 1u win + 1u place (2u per pick) for the each-way books.

    python scripts\\system_stats.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)
LEDGER = os.path.join(PROJECT_DIR, "cloud_app", "our_system_forward_ledger.csv")

spec = importlib.util.spec_from_file_location("tips_bt", os.path.join(HERE, "backtest_tips_history.py"))
assert spec is not None and spec.loader is not None  # local file, always present
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

spec2 = importlib.util.spec_from_file_location("pr", os.path.join(HERE, "backtest_power_rank_top3.py"))
assert spec2 is not None and spec2.loader is not None  # local file, always present
pr = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(pr)


def drawdown(returns: pd.Series) -> float:
    curve = returns.cumsum()
    return float((curve.cummax() - curve).max()) if len(curve) else 0.0


def losing_run(returns: pd.Series) -> int:
    best = run = 0
    for value in returns:
        run = run + 1 if value < 0 else 0
        best = max(best, run)
    return best


def stats(label: str, rows: pd.DataFrame, pl_col: str, odds_col: str | None,
          win_col: str | None) -> dict:
    rows = rows.dropna(subset=[pl_col])
    if rows.empty:
        return {"label": label}
    pl = rows[pl_col].astype(float)
    turnover = len(rows) * 2.0
    monthly = rows.assign(_pl=pl).groupby(rows["_month"]).agg(
        bets=("_pl", "size"), pl=("_pl", "sum"))
    wins = monthly[monthly["pl"] > 0]
    return {
        "label": label,
        "bets": len(rows),
        "turnover": turnover,
        "pl": pl.sum(),
        "roi": pl.sum() / turnover * 100,
        "avg_odds": rows[odds_col].astype(float).mean() if odds_col and odds_col in rows else np.nan,
        "win_pct": rows[win_col].astype(float).mean() * 100 if win_col and win_col in rows else np.nan,
        "max_dd": drawdown(pl),
        "dd_pct_turnover": drawdown(pl) / turnover * 100,
        "worst_run": losing_run(pl),
        "best_bet": pl.max(),
        "worst_bet": pl.min(),
        "sd_per_bet": pl.std(ddof=1) if len(pl) > 1 else np.nan,
        "months": len(monthly),
        "positive_months": len(wins),
        "best_month": monthly["pl"].max() if len(monthly) else np.nan,
        "worst_month": monthly["pl"].min() if len(monthly) else np.nan,
        "monthly": monthly,
    }


def show(label: str, rows: pd.DataFrame, pl_col: str, odds_col=None, win_col=None) -> dict:
    s = stats(label, rows, pl_col, odds_col, win_col)
    if "bets" not in s:
        print(f"  {label:<28} no bets")
        return s
    print(f"  {label:<28} bets {s['bets']:>6,}  turnover {s['turnover']:>8.0f}u  "
          f"P/L {s['pl']:>+9.1f}u  ROI {s['roi']:>+6.1f}%  avg odds {s['avg_odds']:>5.1f}  "
          f"win% {s['win_pct']:>4.1f}")
    print(f"  {'':<28} max DD {s['max_dd']:>7.1f}u ({s['dd_pct_turnover']:.1f}% of turnover)  "
          f"worst run {s['worst_run']:>3} bets  sd/bet {s['sd_per_bet']:.2f}  "
          f"months {s['positive_months']}/{s['months']} positive  best {s['best_month']:+.0f}u  "
          f"worst {s['worst_month']:+.0f}u")
    return s


def monthly_table(label: str, s: dict) -> None:
    if "monthly" not in s or s["monthly"].empty:
        return
    print(f"\n  {label} - monthly P&L")
    cumulative = s["monthly"]["pl"].cumsum()
    for (month, row), cum in zip(s["monthly"].iterrows(), cumulative, strict=True):
        bar = "#" * min(int(abs(row["pl"]) / 5), 40)
        print(f"    {month}  bets {int(row['bets']):>4}  P/L {row['pl']:>+8.1f}u   cum {cum:>+8.1f}u  {bar}")



def main() -> int:
    print("=" * 112)
    print("  SYSTEM STATS   (level stakes; each-way books are 1u win + 1u place = 2u per pick)")
    print("=" * 112)

    # --- Our System's forward book -------------------------------------------------
    print("\n  OUR SYSTEM - forward book (real bets)")
    if os.path.exists(LEDGER):
        book = pd.read_csv(LEDGER)
        for column in ("Odds", "Stake", "BSP_TRUE", "won", "PL_taken", "PL_bsp"):
            if column in book.columns:
                book[column] = pd.to_numeric(book[column], errors="coerce")
        book["_month"] = book["Date"].astype(str).str[:7]
        taken = show("  at the price taken", book.assign(_pl_taken=book["PL_taken"]),
                     "_pl_taken", "Odds", "won")
        show("  at Betfair BSP", book.assign(_pl_bsp=book["PL_bsp"]), "_pl_bsp", "BSP_TRUE", "won")
        monthly_table("forward book, at the price taken", taken)
    else:
        print("  (forward book file not found)")

    # --- Historical rules ----------------------------------------------------------
    print("\n  HISTORICAL (Racing Post data, 2021-01-01 to 2026-09-17)")
    df = bt.load_runners("2021-01-01", "2026-09-17")
    df = df.merge(bt.load_bsp("2021-01-01", "2026-09-17"), on=["race_date", "horse"], how="left")
    df = bt.settle(df, "sp", "_sp")
    df = bt.settle(df, "bsp", "_bsp")

    power = pr.add_power(df)
    picks = pr.top3(power)
    picks = bt.settle(picks, "sp", "_sp")
    picks = bt.settle(picks, "bsp", "_bsp")
    picks["_month"] = picks["race_date"].str[:7]
    ranked = picks.copy()
    ranked["rank"] = ranked.groupby("race_id")["power"].rank(ascending=False, method="first")
    card1 = ranked[ranked["rank"] == 1].copy()
    card1["_pl"] = card1["ew_pl_bsp"]

    weight_drop = bt.categorise(bt.add_history(df.copy()))
    drop = weight_drop[weight_drop["category"] == "Big Weight Drop"].copy()
    drop["_month"] = drop["race_date"].str[:7]
    drop["_pl"] = drop["ew_pl_bsp"]

    df["_month"] = df["race_date"].str[:7]
    df["_pl"] = df["ew_pl_bsp"]

    show("  every runner @BSP (baseline)", df, "_pl", "bsp", "won")
    c1 = show("  Power Rank card #1 @BSP", card1, "_pl", "bsp", "won")
    wd = show("  Big Weight Drop @BSP", drop, "_pl", "bsp", "won")
    monthly_table("Power Rank card #1 @BSP", c1)
    monthly_table("Big Weight Drop @BSP", wd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
