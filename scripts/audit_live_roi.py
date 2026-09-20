r"""
AUDITED LIVE ROI - the ledger with its known defects removed
============================================================
Three defects have been found in how the live ROI has been computed, so this
script produces a figure with each one handled explicitly and reports how much
of the sample survives.

  1. PRICE OUTLIERS.  bf_odds holds a 1000 and bf_place_odds a 250; one such row
     swamps a small sample (AI System read +851% off a single runner).  Prices are
     range-checked and rows outside the limits are excluded and counted.
  2. VOIDS.  A non-runner is not a losing bet - the stake comes back.  Rows with
     NR/Void settle at 0.0 instead of -2.0.
  3. PLACE PRICE.  The place leg is paid at the price recorded for that pick
     (early_place_odds / bf_place_odds), never at a fraction of the win price.
     Picks with no place price are excluded from the each-way figure and counted,
     so the EW number always rests on prices that existed.

Win-only ROI needs no place price, so it is reported on the widest sample.

    python scripts\audit_live_roi.py
    python scripts\audit_live_roi.py --ledger path\to\results_ledger.csv
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEDGER = os.path.join(os.path.dirname(HERE), "cloud_app", "results_ledger.csv")

MAX_WIN = 500.0      # beyond this the price is a data error, not a price
MAX_PLACE = 100.0
COMMISSION = 0.02    # Betfair win commission


def load(path: str) -> pd.DataFrame:
    d = pd.read_csv(path)
    for c in ("early_odds", "sp_odds", "bf_odds", "won", "placed", "early_ew_pl",
              "sp_ew_pl", "early_place_odds", "bf_place_odds", "places_paid"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    # finish_pos is TEXT ('1st','2nd') - never coerce it, or every row looks unsettled
    d["pos_n"] = pd.to_numeric(d["finish_pos"].astype(str).str.extract(r"(\d+)")[0],
                               errors="coerce")
    d["is_void"] = d["finish_pos"].astype(str).str.lower().str.contains("void|nr", na=False)
    d["settled"] = d["pos_n"].notna() | d["is_void"]
    return d


def win_leg(odds: pd.Series, won: pd.Series, net_commission: bool) -> pd.Series:
    """P/L on a 1u win bet, with commission taken off the winning returns."""
    gain = (odds - 1.0) * (1.0 - COMMISSION) if net_commission else (odds - 1.0)
    return gain.where(won == 1, -1.0)


def audit(d: pd.DataFrame, price_col: str, place_col: str | None,
          net_commission: bool, label: str) -> None:
    s = d[d["settled"] & d[price_col].notna()].copy()
    if s.empty:
        print(f"  {label:<34} nothing settled with a price")
        return
    bad_price = (~s[price_col].between(1.01, MAX_WIN)) & (~s["is_void"])
    s = s[~bad_price]
    voids = int(s["is_void"].sum())
    live = s[~s["is_void"]]
    pl_win = win_leg(live[price_col], live["won"], net_commission)
    n = len(s)
    line = (f"  {label:<34} n={len(live):>4}  win% {live['won'].mean() * 100:>5.1f}  "
            f"win-only {pl_win.mean() / 1 * 100:>+7.1f}%")
    if place_col and place_col in s.columns:
        pw = s[s[place_col].between(1.01, MAX_PLACE)]
        no_place = len(s) - len(pw)
        lw = pw[~pw["is_void"]]
        if len(lw) >= 1:
            pl = win_leg(lw[price_col], lw["won"], net_commission) + \
                ((lw[place_col] - 1.0).where(lw["placed"] == 1, -1.0))
            line += (f"   EW(real place) {pl.mean() / 2 * 100:>+7.1f}%"
                     f"  [n={len(lw)}, no place price {no_place}]")
    print(line)
    if bad_price.any():
        print(f"        {int(bad_price.sum())} row(s) excluded: price outside "
              f"1.01-{MAX_WIN:.0f}")
    if voids:
        print(f"        {voids} void/non-runner settled at 0.0 instead of -2.0")


def main() -> int:
    ap = argparse.ArgumentParser(description="Audited ROI from the live ledger.")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    args = ap.parse_args()

    d = load(args.ledger)
    print("=" * 108)
    print(f"  AUDITED LIVE ROI - {os.path.basename(args.ledger)}")
    print(f"  {len(d)} logged picks, {int(d['settled'].sum())} settled, "
          f"{int(d['is_void'].sum())} void/non-runner")
    print("=" * 108)

    print("\nAT THE EARLY (RECORDED) PRICE")
    audit(d, "early_odds", "early_place_odds", False, "all systems")
    for system, g in d.groupby("system_name"):
        audit(g, "early_odds", "early_place_odds", False, system)

    print("\nAT BETFAIR BSP (2% commission on wins)")
    audit(d, "bf_odds", "bf_place_odds", True, "all systems")
    for system, g in d.groupby("system_name"):
        audit(g, "bf_odds", "bf_place_odds", True, system)

    print("\nSTRIKE vs IMPLIED (settled, real prices only) - the column that converges first")
    for basis, col in (("early", "early_odds"), ("bsp", "bf_odds"), ("sp", "sp_odds")):
        for system, g in d.groupby("system_name"):
            p = g[g["settled"] & g[col].between(1.01, MAX_WIN) & (~g["is_void"])]
            if len(p) < 5:
                continue
            imp = (1 / p[col]).mean() * 100
            gap = p["won"].mean() * 100 - imp
            print(f"  {system:<20} {basis:<6} n={len(p):>4}  strike {p['won'].mean() * 100:>5.1f}%  "
                  f"implied {imp:>5.1f}%  gap {gap:>+6.1f}pp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
