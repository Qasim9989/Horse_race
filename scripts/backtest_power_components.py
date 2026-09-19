"""
WHICH FACTOR CARRIES THE AI, AND CAN IT BE IMPROVED?
====================================================
The app ranks runners by

    Power_Score = 0.35*Best_TS + 0.35*Avg_TS3 + 0.30*Best_RPR - 0.15*Weight

This tests each ingredient on its own, picking ONE horse per race from the same
data, so the composite can be compared against its parts (Racing Post data
2021-2026, prior runs only, each-way at the Racing Post SP and at real BSP).

If a single factor beats the composite, the AI is improvable by simplifying it;
if nothing beats backing the field, it is not improvable with these inputs.

    python scripts\\backtest_power_components.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("tips_bt", os.path.join(HERE, "backtest_tips_history.py"))
assert spec is not None and spec.loader is not None  # local file, always present
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def add_factors(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["horse", "race_date", "race_time"]).copy()
    g = df.groupby("horse", sort=False)

    df["best_ts"] = g["topspeed"].transform(lambda s: s.cummax().shift(1))
    df["avg_ts3"] = g["topspeed"].transform(lambda s: s.shift(1).rolling(3).mean())
    df["best_rpr"] = g["rpr"].transform(lambda s: s.cummax().shift(1))
    df["lto_pos"] = g["pos"].shift(1)
    df["lto_or"] = g["official_rating"].shift(1)
    df["lto_date"] = g["race_date"].shift(1)
    df["lto_weight"] = g["weight_lbs"].shift(1)
    df["lto_code"] = g["code"].shift(1)

    df["mark_change"] = df["official_rating"] - df["lto_or"]          # negative = dropping
    df["weight_change"] = np.where(df["code"] == df["lto_code"],
                                   df["weight_lbs"] - df["lto_weight"], np.nan)
    df["days_since"] = (pd.to_datetime(df["race_date"]) - pd.to_datetime(df["lto_date"])).dt.days

    df["power"] = (0.35 * df["best_ts"].fillna(0) + 0.35 * df["avg_ts3"].fillna(0)
                   + 0.30 * df["best_rpr"].fillna(0) - 0.15 * df["weight_lbs"].fillna(0))
    return df


# (label, column, highest-wins?)
FACTORS = [
    ("Power_Score (the composite)", "power", True),
    ("Best Topspeed", "best_ts", True),
    ("Avg of last 3 Topspeed", "avg_ts3", True),
    ("Best RPR", "best_rpr", True),
    ("Official rating (today)", "official_rating", True),
    ("Lightest weight (today)", "weight_lbs", False),
    ("Best last-run position", "lto_pos", False),
    ("Biggest mark drop (Ben-style)", "mark_change", False),
    ("Biggest weight drop", "weight_change", False),
    ("Shortest time since a run", "days_since", False),
]


def card_one(df: pd.DataFrame, column: str, highest: bool) -> pd.DataFrame:
    rows = df[df[column].notna()]
    if rows.empty:
        return rows
    rows = rows.sort_values(["race_id", column], ascending=[True, not highest])
    return rows.groupby("race_id", as_index=False).head(1)


def main() -> int:
    print("Loading Racing Post runners 2021-01-01 -> 2026-09-17 ...")
    df = bt.load_runners("2021-01-01", "2026-09-17")
    df = df.merge(bt.load_bsp("2021-01-01", "2026-09-17"), on=["race_date", "horse"], how="left")
    df = add_factors(df)
    df = bt.settle(df, "sp", "_sp")
    df = bt.settle(df, "bsp", "_bsp")
    print(f"  {len(df):,} runners in {df['race_id'].nunique():,} races")

    out = ["=" * 104,
           "  ONE HORSE PER RACE BY EACH FACTOR (each-way, 2021-2026)",
           "=" * 104]
    for price in ("sp", "bsp"):
        base = df.dropna(subset=[price, "pos"])
        pl = base[f"ew_pl_{price}"].mean() * 50
        out.append(f"  {'every runner at ' + price.upper():<32} bets {len(base):>8,}   "
                   f"win% {(base['pos'] == 1).mean() * 100:>4.1f}   ROI {pl:>+6.1f}%")
    out.append("")
    out.append(f"  {'ranking factor':<32}{'picks':>9}{'win%':>7}{'place%':>8}{'ROI@SP':>9}{'ROI@BSP':>9}")

    for label, column, highest in FACTORS:
        picks = card_one(df, column, highest).dropna(subset=["pos"])
        sp_rows = picks.dropna(subset=["sp"])
        bsp_rows = picks.dropna(subset=["bsp"])
        if sp_rows.empty:
            out.append(f"  {label:<32} no picks")
            continue
        win = (sp_rows["pos"] == 1).mean() * 100
        placed = sp_rows["placed"].mean() * 100
        out.append(f"  {label:<32}{len(sp_rows):>9,}{win:>6.1f}%{placed:>7.1f}%"
                   f"{sp_rows['ew_pl_sp'].mean() * 50:>+8.1f}%{bsp_rows['ew_pl_bsp'].mean() * 50:>+8.1f}%")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
