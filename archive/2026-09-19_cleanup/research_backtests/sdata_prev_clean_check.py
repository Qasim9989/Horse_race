r"""
STRIDE SIGNAL - DOES THE EDGE SURVIVE PRICE CLEANING?
=====================================================
scripts\sdata_prev_significance.py flagged three things that can manufacture an
edge on their own:

  * winner BSP as high as 999.46.  At level stakes one such winner adds
    ~1000/22,704 = 4.4% to a per-race ROI, so two or three bogus prices can
    account for the whole "fastest previous run" result.
  * 584 duplicate (race, horse) rows - a fan-out on the SData -> BFSP join.
  * 1,519 races with no flagged winner.

It also found the tell-tale sign that something is wrong: backing *every*
runner at BSP shows +4.02% here, when the full BFSP population is -1.75%.  A
market cannot pay 4% on all runners, so this frame is not a clean sample.

This re-prices the market and the two lead rules after each cleaning step, and
shows the largest single-stake payouts so the edge cannot hide in a few rows.

    python scripts\sdata_prev_clean_check.py
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

PICKLE = "reports/_sdata_prev.pkl"
PRICE_CAP = 100.0
RULES = {"fastest prev run": "p_MPH_Finish",
         "longest prev stride": "p_SL_Finish"}


def load() -> pd.DataFrame:
    d = pd.read_pickle(PICKLE)
    d = d[d["BSP_TRUE"].notna() & d["p_SL_Finish"].notna()].copy()
    d["year"] = pd.to_datetime(d["RaceDate"]).dt.year
    return d


def ret(d: pd.DataFrame) -> pd.Series:
    """Level-stake return of backing every row in `d` at BSP."""
    win = d["WinLose"] == 1
    return pd.Series(np.where(win, d["BSP_TRUE"] - 1.0, -1.0), index=d.index)


def picks(d: pd.DataFrame, col: str) -> pd.DataFrame:
    return d.loc[d.groupby("SD_RNo")[col].idxmax()]


def roi(x: pd.Series) -> float:
    return float(x.mean() * 100.0)


def ret_at(d: pd.DataFrame, price: str) -> pd.Series:
    """Level-stake return of backing every row in `d` at `price`."""
    win = d["WinLose"] == 1
    return pd.Series(np.where(win, d[price] - 1.0, -1.0), index=d.index)


def one_sided(t: float) -> float:
    """Normal-approximation one-sided p-value (no scipy here)."""
    return 0.5 * math.erfc(t / math.sqrt(2.0))


def price_sanity(d: pd.DataFrame) -> pd.Series:
    """Sentinel-price census; returns the mask of races with one winner."""
    print("=" * 74)
    print("A. PRICE SANITY - how much of the sample is not a real price?")
    print("=" * 74)
    for cap in (50, 100, 300, 900):
        print(f"  runners priced above {cap:>3} : "
              f"{int((d['BSP_TRUE'] > cap).sum()):>6,}")
    w = d[d["WinLose"] == 1]
    print(f"  winners priced above  50 : {int((w['BSP_TRUE'] > 50).sum()):>6,}"
          f"   (of {len(w):,} winners)")
    print(f"  winners priced above 100 : {int((w['BSP_TRUE'] > 100).sum()):>6,}")
    print(f"  winner BSP max           : {w['BSP_TRUE'].max():.2f}")

    r = ret(d)
    top = r.sort_values(ascending=False).head(10)
    tot = float(r.sum())
    print(f"\n  total staked {len(r):,} units, returned {tot + len(r):,.0f}"
          f" -> ROI {roi(r):+.2f}%")
    print(f"  top 10 winning rows alone return "
          f"{float(top.sum()):,.0f} units = "
          f"{float(top.sum()) / len(r) * 100:.2f}% of ROI")
    cols = ["RaceDate", "CourseClean", "HorseClean", "BSP_TRUE"]
    show = d.loc[top.index, cols].copy()
    show["stake_return"] = top.to_numpy()
    print("\n  the ten biggest single wins (a real price never exceeds ~100):")
    print(show.to_string(index=False))
    print()
    return d.groupby("SD_RNo")["WinLose"].transform("sum") == 1


def variants(d: pd.DataFrame, win_ok: pd.Series) -> None:
    """ROI of the market and both rules after each cleaning step."""
    print("=" * 74)
    print("B. THE SAME TEST AFTER CLEANING")
    print("=" * 74)
    sets: dict[str, pd.Series] = {
        "as published": pd.Series(True, index=d.index),
        "prices <= 100": d["BSP_TRUE"] <= PRICE_CAP,
        "no duplicate rows": ~d.duplicated(["SD_RNo", "HorseClean"]),
        "one winner per race": win_ok,
        "all three": ((d["BSP_TRUE"] <= PRICE_CAP) & win_ok
                      & ~d.duplicated(["SD_RNo", "HorseClean"])),
    }
    head = f"{'sample':<22}{'races':>7}{'runners':>9}{'baseline':>10}"
    for name in RULES:
        head += f"{name:>21}"
    print(head)
    print("-" * len(head))
    for label, mask in sets.items():
        sub = d[mask]
        line = (f"{label:<22}{sub['SD_RNo'].nunique():>7,}{len(sub):>9,}"
                f"{roi(ret(sub)):>9.2f}%")
        for col in RULES.values():
            p = picks(sub, col)
            line += f"{roi(ret(p)):>20.2f}%"
        print(line)
    print()


def per_year(d: pd.DataFrame, win_ok: pd.Series) -> None:
    """Yearly ROI, raw vs cleaned - a one-year fluke shows up here."""
    clean = ((d["BSP_TRUE"] <= PRICE_CAP) & win_ok
             & ~d.duplicated(["SD_RNo", "HorseClean"]))
    print("=" * 74)
    print("C. YEAR BY YEAR - raw vs cleaned")
    print("=" * 74)
    head = f"{'year':<7}{'races':>7}{'base raw':>10}{'base clean':>12}"
    for name in RULES:
        head += f"{name + ' raw':>21}{name + ' clean':>23}"
    print(head)
    print("-" * len(head))
    for year in sorted(int(y) for y in d["year"].unique()):
        g = d[d["year"] == year]
        line = f"{year:<7}{g['SD_RNo'].nunique():>7,}"
        line += f"{roi(ret(g)):>9.2f}%{roi(ret(g[clean.loc[g.index]])):>11.2f}%"
        for col in RULES.values():
            raw_p = picks(g, col)
            cl_p = picks(g[clean.loc[g.index]], col)
            line += f"{roi(ret(raw_p)):>20.2f}%{roi(ret(cl_p)):>22.2f}%"
        print(line)
    print()


def paired(d: pd.DataFrame, win_ok: pd.Series) -> None:
    """The bias-free statistic: pick minus the same race's field average.

    Whatever the join is missing - first-time-out horses, the winner in races
    where it had no stride history - it is missing from *both* sides of the
    subtraction, so a level bias cancels and only the rule's own information
    is left.
    """
    clean = ((d["BSP_TRUE"] <= PRICE_CAP) & win_ok
             & ~d.duplicated(["SD_RNo", "HorseClean"]))
    c = d[clean]
    print("=" * 74)
    print("D. PAIRED EXCESS AFTER CLEANING - the number the bias cannot touch")
    print("=" * 74)
    print(f"{'rule':<22}{'price':<9}{'bets':>7}{'field avg':>11}"
          f"{'pick ROI':>10}{'excess':>9}{'t':>7}{'p':>9}")
    print("-" * 74)
    for name, col in RULES.items():
        p = picks(c, col)
        for price, label in (("BSP_TRUE", "BSP"), ("MorningWAP", "morning"),
                             ("PPWAP", "pre-off")):
            r = ret_at(p, price)
            base = ret_at(c, price).groupby(c["SD_RNo"]).mean()
            base = base.loc[p["SD_RNo"].to_numpy()]
            diff = r.to_numpy() - base.to_numpy()
            sd = float(np.std(diff, ddof=1))
            t = float(np.mean(diff) / (sd / math.sqrt(len(diff))))
            print(f"{name:<22}{label:<9}{len(diff):>7,}"
                  f"{roi(base):>10.2f}%{roi(r):>9.2f}%"
                  f"{float(np.mean(diff) * 100):>8.2f}%{t:>7.2f}"
                  f"{one_sided(t):>9.4f}")
    print()


def calibration(d: pd.DataFrame, win_ok: pd.Series) -> None:
    """Does the pick win more often than its own BSP implies?

    This is the only check the frame's missing runners cannot touch: neither
    the win flag nor 1/BSP depends on who was dropped.  If the field's implied
    probabilities sum to well under 1.00 the race is incomplete, and the pick's
    gap between actual wins and implied probability is the real edge.
    """
    c = d[(d["BSP_TRUE"] <= PRICE_CAP) & win_ok
          & ~d.duplicated(["SD_RNo", "HorseClean"])].copy()
    c["imp"] = 1.0 / c["BSP_TRUE"]
    per_race = c.groupby("SD_RNo")["imp"].sum()
    print("=" * 74)
    print("E. CALIBRATION - actual wins vs the probability BSP implies")
    print("=" * 74)
    print(f"field implied probability per race : {per_race.mean():.3f}"
          "   (a complete, efficient field is ~1.00)")
    print(f"races in the clean sample           : {len(per_race):,}")
    print()
    print(f"{'rule':<22}{'picks':>7}{'win %':>8}{'BSP says':>10}"
          f"{'edge':>8}{'rel':>7}")
    print("-" * 74)
    for name, col in RULES.items():
        p = c.loc[c.groupby("SD_RNo")[col].idxmax()]
        imp = float((1.0 / p["BSP_TRUE"]).mean())
        winrate = float((p["WinLose"] == 1).mean())
        print(f"{name:<22}{len(p):>7,}{winrate * 100:>7.2f}%"
              f"{imp * 100:>9.2f}%{(winrate - imp) * 100:>7.2f}%"
              f"{winrate / imp:>7.2f}x")
    print()
    print(f"{'a random runner':<22}{len(c):>7,}"
          f"{float((c['WinLose'] == 1).mean()) * 100:>7.2f}%"
          f"{float(c['imp'].mean()) * 100:>9.2f}%"
          f"{float((c['WinLose'] == 1).mean() - c['imp'].mean()) * 100:>7.2f}%"
          f"{float((c['WinLose'] == 1).mean() / c['imp'].mean()):>7.2f}x")
    print()


def main() -> None:
    d = load()
    win_ok = price_sanity(d)
    variants(d, win_ok)
    per_year(d, win_ok)
    paired(d, win_ok)
    calibration(d, win_ok)
    print("The question is not 'is the ROI positive' but 'is it positive once")
    print("the fake prices, the duplicated rows and the winner-less races are")
    print("gone'. Line B tells you that; line C tells you if one year did it.")


if __name__ == "__main__":
    main()

