r"""
STRIDE SIGNAL - INTEGRITY, PAIRED TEST, SIGNIFICANCE
====================================================
Part two of scripts\sdata_prev_stride_ev.py, which built the lagged
(previous-run) stride frame and priced it against BFSP.  This reads its saved
output and answers the three questions that decide whether the +12.89%
(longest previous stride) figure is real:

  1. JOIN INTEGRITY - one winner per race, winner rows consistent, and the
     winner's BSP sane.  A mismatched join would fake an edge instantly.
  2. PAIRED TEST    - the pick's return vs the *same race's* average runner
     return, so market-wide drift cannot be mistaken for signal.
  3. SIGNIFICANCE   - bootstrap CI and a t-stat on the per-race returns, plus
     the same-race ROI at the morning and pre-off prices.

    python scripts\sdata_prev_significance.py
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

PICKLE = "reports/_sdata_prev.pkl"
RULES = {
    "longest prev stride": "p_SL_Finish",
    "fastest prev run": "p_MPH_Finish",
    "best prev FSP": "p_FSP_Finish",
    "biggest prev STDIFF": "p_STDIFF_Finish",
}
PRICES = ["BSP_TRUE", "MorningWAP", "PPWAP"]
PRICE_LABEL = {"BSP_TRUE": "BSP", "MorningWAP": "morning", "PPWAP": "pre-off"}
N_BOOT = 20000
SEED = 20260916


def load() -> pd.DataFrame:
    """The saved stride frame, restricted to rows the EV test used."""
    d = pd.read_pickle(PICKLE)
    d = d[d["BSP_TRUE"].notna() & d["p_SL_Finish"].notna()].copy()
    return d


def returns(d: pd.DataFrame, price: str) -> pd.Series:
    """Level-stake return of backing every row in `d` at `price`."""
    win = d["WinLose"] == 1
    return pd.Series(np.where(win, d[price] - 1.0, -1.0), index=d.index)


def picks(d: pd.DataFrame, col: str) -> pd.DataFrame:
    """Best runner in each race on `col` (one bet per race)."""
    return d.loc[d.groupby("SD_RNo")[col].idxmax()]


def roi(x: pd.Series) -> float:
    return float(x.mean() * 100.0)


def one_sided_p(t: float) -> float:
    """Normal-approximation p-value (no scipy dependency)."""
    return 0.5 * math.erfc(t / math.sqrt(2.0))


def bootstrap(x: pd.Series, races: pd.Series) -> tuple[float, float, float,
                                                       float]:
    """SE, 95% CI and P(ROI<=0) from resampling *races*, not runners.

    Runners in a race are not independent - every race has exactly one winner -
    so the resampling unit is the race, and each draw re-weights whole races.
    Level stakes mean a draw's ROI is total return / total bets.
    """
    frame = pd.DataFrame({"r": x.to_numpy(), "race": races.to_numpy()})
    g = frame.groupby("race")["r"].agg(sums="sum", counts="size")
    sums = g["sums"].to_numpy()
    counts = g["counts"].to_numpy()
    n = len(sums)
    rng = np.random.default_rng(SEED)
    draws = np.empty(N_BOOT)
    # chunked so the index matrix stays small on a 9,000-race sample
    for start in range(0, N_BOOT, 500):
        k = min(500, N_BOOT - start)
        take = rng.integers(0, n, size=(k, n))
        draws[start:start + k] = (sums[take].sum(axis=1)
                                  / counts[take].sum(axis=1) * 100.0)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return (float(draws.std(ddof=1)), float(lo), float(hi),
            float((draws <= 0).mean()))


def integrity(d: pd.DataFrame) -> None:
    print("=" * 74)
    print("1. JOIN INTEGRITY")
    print("=" * 74)
    g = d.groupby("SD_RNo")["WinLose"].agg(winners="sum", runners="size")
    odd = g[g["winners"] != 1]
    print(f"races                      : {len(g):,}")
    print(f"runners                    : {len(d):,}")
    print(f"races with != 1 winner     : {len(odd):,}")
    if not odd.empty:
        print(odd.head(10).to_string())
    print(f"races with a single runner : {int((g['runners'] == 1).sum()):,}")
    print(f"runners per race (median)  : {g['runners'].median():.0f}")
    w = d[d["WinLose"] == 1]
    print(f"winner rows present        : {len(w):,}"
          f"  (BSP known on {w['BSP_TRUE'].notna().mean() * 100:.2f}%)")
    print(f"winner BSP min/med/max     : {w['BSP_TRUE'].min():.2f} / "
          f"{w['BSP_TRUE'].median():.2f} / {w['BSP_TRUE'].max():.2f}")
    rank = d.groupby("SD_RNo")["BSP_TRUE"].rank(method="min")
    sizes = d.groupby("SD_RNo")["BSP_TRUE"].transform("count")
    wrank = rank[d["WinLose"] == 1]
    wsizes = sizes[d["WinLose"] == 1]
    print(f"winner was favourite       : {(wrank == 1).mean() * 100:.1f}%"
          "   (sanity only - favourites win ~30% of the time)")
    print(f"winner was longest price   : {(wrank == wsizes).mean() * 100:.2f}%")
    print(f"duplicate (race,horse) rows: "
          f"{int(d.duplicated(['SD_RNo', 'HorseClean']).sum()):,}")
    print()
    allr = returns(d, "BSP_TRUE")
    print(f"market baseline: {len(allr):,} bets backing every runner at BSP"
          f" -> ROI {roi(allr):+.2f}%")
    print()


def main() -> None:
    d = load()
    integrity(d)

    print("=" * 74)
    print("2. PAIRED TEST - pick vs the same race's average runner")
    print("=" * 74)
    print(f"{'rule':<22}{'price':<9}{'bets':>6}{'ROI':>9}"
          f"{'race avg':>10}{'paired':>9}{'t':>7}{'p(1-sided)':>11}")
    print("-" * 74)
    for name, col in RULES.items():
        p = picks(d, col)
        for price in PRICES:
            r = returns(p, price)
            base = returns(d, price).groupby(d["SD_RNo"]).mean()
            base = base.loc[p["SD_RNo"].to_numpy()]
            diff = r.to_numpy() - base.to_numpy()
            n = len(diff)
            sd = float(np.std(diff, ddof=1))
            t = float(np.mean(diff) / (sd / math.sqrt(n)))
            print(f"{name:<22}{PRICE_LABEL[price]:<9}{n:>6}"
                  f"{roi(r):>8.2f}%{roi(base):>9.2f}%"
                  f"{roi(pd.Series(diff)):>8.2f}%{t:>7.2f}"
                  f"{one_sided_p(t):>11.4f}")
    print()

    print("=" * 74)
    print("3. SIGNIFICANCE - bootstrap over races (not runners)")
    print("=" * 74)
    print(f"{'rule':<22}{'price':<9}{'ROI':>9}{'SE':>8}"
          f"{'95% CI low':>12}{'95% CI high':>13}{'P(ROI<=0)':>11}")
    print("-" * 74)
    for name, col in RULES.items():
        p = picks(d, col)
        for price in PRICES:
            r = returns(p, price)
            se, lo, hi, p_zero = bootstrap(r, p["SD_RNo"])
            print(f"{name:<22}{PRICE_LABEL[price]:<9}{roi(r):>8.2f}%"
                  f"{se:>8.2f}{lo:>11.2f}%{hi:>12.2f}%{p_zero:>11.4f}")
    print()
    print("Read it like this: a rule only counts if the paired test is positive")
    print("AND the bootstrap CI clears zero. Anything not clearly better than")
    print("the printed baseline is just the market.")


if __name__ == "__main__":
    main()

