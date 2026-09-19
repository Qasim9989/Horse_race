r"""
THE TWO STRIDE RULES AS ONE SYSTEM
==================================
The lagged stride frame has two rules that each worked on their own:

  fastest prev run      max p_MPH_Finish   - last run's top speed
  longest prev stride   max p_SL_Finish    - last run's stride length

Three ways to fuse them, all priced at BSP on the same like-for-like subset
(every price present and sane), and compared against the two singles:

  agree   bet only when one horse tops BOTH metrics - fewer bets, hopefully
          higher precision (the two agreeing is the strongest possible read)
  union   both picks, one unit each per race - twice the turnover, same edges
  score   one bet per race on the best sum of within-race percentile ranks,
          so a horse that is 2nd on both can beat a horse that is 1st on one

For every variant: bets, ROI, drawdown, plus the two bias-free yardsticks from
the earlier work - the paired excess over the race's own field average, and the
calibration gap between actual wins and what the BSP implied.

    python scripts\stride_combined.py
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from stride_drawdown_prices import like_for_like, load, picks, ret

METRICS = ("p_MPH_Finish", "p_SL_Finish")


def rank_pct(d: pd.DataFrame, col: str) -> pd.Series:
    """Within-race percentile rank, 1.0 = best in that race."""
    return d.groupby("SD_RNo")[col].rank(pct=True)


def agree(d: pd.DataFrame) -> pd.DataFrame:
    """Races where the same horse tops both metrics."""
    a = d.groupby("SD_RNo")[METRICS[0]].idxmax()
    b = d.groupby("SD_RNo")[METRICS[1]].idxmax()
    keep = a.loc[a.index.isin(b.index)]
    same = keep[keep.to_numpy() == b.loc[keep.index].to_numpy()]
    return d.loc[same.to_numpy()]


def union(d: pd.DataFrame) -> pd.DataFrame:
    """Both picks, one unit each - the same horse twice is two units."""
    both = pd.concat([picks(d, METRICS[0]), picks(d, METRICS[1])])
    return both.sort_values(["RaceDate", "RaceTime", "SD_RNo"])


def scored(d: pd.DataFrame) -> pd.DataFrame:
    """One bet per race on the highest combined percentile rank."""
    s = d.copy()
    s["score"] = rank_pct(s, METRICS[0]) + rank_pct(s, METRICS[1])
    return picks(s, "score")


def singles(d: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"fastest only": picks(d, METRICS[0]),
            "stride only": picks(d, METRICS[1])}


def variants(d: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = singles(d)
    out["agree (both)"] = agree(d)
    out["union (2u/race)"] = union(d)
    out["score (rank sum)"] = scored(d)
    return out


def stats(p: pd.DataFrame, field: pd.DataFrame) -> tuple[float, float, float]:
    """ROI, paired excess over the field average, and its t-statistic."""
    r = ret(p, "bsp")
    base = pd.Series(ret(field, "bsp"), index=field.index)
    base = base.groupby(field["SD_RNo"]).mean()
    base = base.loc[p["SD_RNo"].to_numpy()]
    diff = r - base.to_numpy()
    sd = float(np.std(diff, ddof=1)) if len(diff) > 1 else float("nan")
    t = float(np.mean(diff) / (sd / math.sqrt(len(diff)))) if sd else 0.0
    return (float(np.mean(diff) * 100.0), t,
            0.5 * math.erfc(t / math.sqrt(2.0)))


def main() -> None:
    d = like_for_like(load())
    print()
    vs = variants(d)
    print("=" * 96)
    print("MONEY AND DRAWDOWN - all at BSP, level stakes")
    print("=" * 96)
    print(f"{'system':<18}{'bets':>7}{'races':>7}{'win %':>7}{'ROI':>8}"
          f"{'final P/L':>11}{'max DD':>8}{'DD/ROI':>8}{'underwater':>11}"
          f"{'streak':>7}")
    print("-" * 96)
    curves: dict[str, np.ndarray] = {}
    for label, p in vs.items():
        r = ret(p, "bsp")
        eq = np.cumsum(r)
        peak = np.maximum.accumulate(eq)
        dd = float((peak - eq).max())
        under = (peak - eq) > 0
        longest = cur = 0
        for flag in under:
            cur = cur + 1 if flag else 0
            longest = max(longest, cur)
        streak = cur = 0
        for x in r:
            cur = cur + 1 if x < 0 else 0
            streak = max(streak, cur)
        profit = float(eq[-1])
        ratio = dd / profit if profit > 0 else float("nan")
        print(f"{label:<18}{len(r):>7,}{p['SD_RNo'].nunique():>7,}"
              f"{(r > 0).mean() * 100:>6.1f}%{r.mean() * 100:>7.2f}%"
              f"{profit:>11,.0f}{dd:>8.0f}{ratio:>8.2f}{longest:>11,}"
              f"{streak:>7}")
        curves[label.split()[0]] = eq

    print()
    print("=" * 96)
    print("THE BIAS-FREE YARDSTICKS (what the frame's missing runners cannot fake)")
    print("=" * 96)
    print(f"{'system':<18}{'picks':>7}{'win %':>8}{'BSP says':>10}{'gap':>8}"
          f"{'paired excess':>15}{'t':>7}{'p':>9}")
    print("-" * 96)
    for label, p in vs.items():
        imp = float((1.0 / p["bsp"]).mean())
        wins = float((p["WinLose"] == 1).mean())
        excess, t, pval = stats(p, d)
        print(f"{label:<18}{len(p):>7,}{wins * 100:>7.2f}%{imp * 100:>9.2f}%"
              f"{(wins - imp) * 100:>7.2f}%{excess:>14.2f}%{t:>7.2f}"
              f"{pval:>9.4f}")

    print()
    print("=" * 96)
    print("HOW OFTEN DO THE TWO RULES PICK THE SAME HORSE?")
    print("=" * 96)
    ag = agree(d)
    races = d["SD_RNo"].nunique()
    print(f"races                 : {races:,}")
    print(f"both rules agree      : {len(ag):,}  "
          f"({len(ag) / races * 100:.1f}% of races)")
    imp = float((1.0 / ag["bsp"]).mean())
    wins = float((ag["WinLose"] == 1).mean())
    print(f"agreed horse wins     : {wins * 100:.2f}% while its BSP implies "
          f"{imp * 100:.2f}%  (gap {(wins - imp) * 100:+.2f}pts)")
    print()
    longs = pd.DataFrame(
        [{"system": key, "bet_no": i + 1, "equity": v}
         for key, eq in curves.items() for i, v in enumerate(eq)])
    longs.to_csv("reports/_stride_combined.csv", index=False)
    chart_combined(curves)


def chart_combined(curves: dict[str, np.ndarray]) -> None:
    """Equity curves for every variant, x = bet number (lengths differ)."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for label, eq in curves.items():
        fig.add_trace(go.Scatter(x=np.arange(len(eq)), y=eq, name=label,
                                 mode="lines"))
    fig.update_layout(height=620, hovermode="x unified",
                      title="two stride rules fused - equity at BSP, 1u/bet",
                      xaxis_title="bet number", yaxis_title="units")
    fig.write_html("reports/stride_combined.html")
    print("chart written: reports/stride_combined.html")
    print("equity curves -> reports/_stride_combined.csv")


if __name__ == "__main__":
    main()


