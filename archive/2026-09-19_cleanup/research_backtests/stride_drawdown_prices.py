r"""
DRAWDOWN: BSP vs MORNING vs EVENING PRICE
=========================================
The same stride picks, on the same races, priced four ways:

  BSP       BFSP.BSP_TRUE                  the exchange's starting price
  morning   NEW_HIR.HIR_MorningPrice       early price, morning of the race
  evening   NEW_HIR.HIR_EveningPrice       early price, the evening before
  pre-off   BFSP.PPWAP                     weighted average near the off

Level stakes, one unit per pick, in chronological order.  For each series:
ROI, final P/L, deepest drawdown, how long it lasted, worst losing streak, and
the three worst episodes with their dates.

Everything is measured on the subset of picks where *all four* prices exist and
are sane (1.0 < price <= 100), so the curves are like-for-like: any difference
is the price itself, not a different set of bets.

    python scripts\stride_drawdown_prices.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pyodbc

PICKLE = "reports/_sdata_prev.pkl"
PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=300;"
       "MultipleActiveResultSets=True;")
RULES = {"fastest prev run": "p_MPH_Finish",
         "longest prev stride": "p_SL_Finish"}
SERIES = [("BSP", "bsp"), ("morning", "morning"),
          ("evening", "evening"), ("pre-off", "ppwap")]
CAP = 100.0


def load() -> pd.DataFrame:
    """Stride frame + the morning and evening prices from NEW_HIR."""
    d = pd.read_pickle(PICKLE)
    d = d[d["BSP_TRUE"].notna() & d["p_SL_Finish"].notna()].copy()
    c = pyodbc.connect(PRO)
    h = pd.read_sql("""
        SELECT HIR_RNo AS SD_RNo, HIR_HNo AS SD_HNo,
               HIR_EveningPrice AS evening,
               HIR_MorningPrice AS morning
        FROM dbo.NEW_HIR
        WHERE HIR_EveningPrice > 0 AND HIR_MorningPrice > 0
    """, c)
    c.close()
    print(f"NEW_HIR priced rows: {len(h):,}")
    d = d.merge(h, on=["SD_RNo", "SD_HNo"], how="left")
    d["bsp"] = d["BSP_TRUE"]
    d["ppwap"] = d["PPWAP"]
    d = d.sort_values(["RaceDate", "RaceTime", "SD_RNo", "SD_HNo"])
    return d


def like_for_like(d: pd.DataFrame) -> pd.DataFrame:
    """Rows where every price exists and is sane - the fair comparison."""
    cols = [col for _, col in SERIES]
    vals = d[cols]
    ok = vals.notna().all(axis=1) & (vals > 1.0).all(axis=1)
    ok &= (vals <= CAP).all(axis=1)
    print(f"rules sample {len(d):,} runners -> like-for-like {int(ok.sum()):,}"
          f" ({(ok.sum() / len(d)) * 100:.1f}%)")
    return d[ok]


def picks(d: pd.DataFrame, col: str) -> pd.DataFrame:
    return d.loc[d.groupby("SD_RNo")[col].idxmax()]


def ret(d: pd.DataFrame, price: str) -> np.ndarray:
    win = (d["WinLose"] == 1).to_numpy()
    return np.where(win, d[price].to_numpy() - 1.0, -1.0)


def episodes(eq: np.ndarray, top: int = 3) -> list[tuple[float, int, int, int]]:
    """Deepest drawdown episodes: (depth, peak_i, trough_i, recovery_i)."""
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    out: list[tuple[float, int, int, int]] = []
    i, n = 0, len(eq)
    while i < n:
        if dd[i] <= 0:
            i += 1
            continue
        start = i
        while i < n and dd[i] > 0:
            i += 1
        end = i - 1
        trough = start + int(np.argmax(dd[start:end + 1]))
        rec = -1
        j = end + 1
        while j < n:
            if eq[j] >= peak[trough]:
                rec = j
                break
            j += 1
        out.append((float(dd[trough]), start - 1, trough, rec))
    out.sort(key=lambda e: -e[0])
    return out[:top]


def worst_streak(r: np.ndarray) -> int:
    worst = cur = 0
    for x in r:
        cur = cur + 1 if x < 0 else 0
        worst = max(worst, cur)
    return worst


def chart(name: str, dates: np.ndarray, curves: dict[str, np.ndarray]) -> None:
    """Write an interactive equity + drawdown chart for one rule."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, subplot_titles=(
        f"{name} - equity, 1 unit per pick",
        "distance below the peak (units)"))
    for label, eq in curves.items():
        peak = np.maximum.accumulate(eq)
        fig.add_trace(go.Scatter(x=dates, y=eq, name=label, mode="lines"),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=dates, y=eq - peak, name=label + " DD",
                                 mode="lines", showlegend=False),
                      row=2, col=1)
    fig.update_layout(height=820, hovermode="x unified",
                      title=f"BSP vs morning vs evening vs pre-off - {name}")
    out = f"reports/stride_drawdown_{name.split(maxsplit=1)[0]}.html"
    fig.write_html(out)
    print(f"  chart written: {out}")


def main() -> None:
    d = like_for_like(load())
    print()
    for name, col in RULES.items():
        p = picks(d, col)
        dates = pd.to_datetime(p["RaceDate"]).dt.date.to_numpy()
        print("=" * 88)
        print(f"{name.upper()}   {len(p):,} picks, "
              f"{p['SD_RNo'].nunique():,} races, "
              f"{dates[0]} to {dates[-1]}")
        print("=" * 88)
        print(f"{'price':<9}{'wins':>6}{'win %':>7}{'ROI':>9}{'final P/L':>11}"
              f"{'max DD':>9}{'DD/ROI':>8}{'longest DD':>12}{'streak':>8}")
        print("-" * 88)
        curves: dict[str, np.ndarray] = {}
        for label, price in SERIES:
            r = ret(p, price)
            eq = np.cumsum(r)
            curves[label] = eq
            peak = np.maximum.accumulate(eq)
            dd = float((peak - eq).max())
            in_dd = (peak - eq) > 0
            longest = cur = 0
            for flag in in_dd:
                cur = cur + 1 if flag else 0
                longest = max(longest, cur)
            profit = float(eq[-1])
            roi = float(r.mean() * 100.0)
            ratio = dd / profit if profit > 0 else float("nan")
            print(f"{label:<9}{int((r > 0).sum()):>6}"
                  f"{(r > 0).mean() * 100:>6.1f}%{roi:>8.2f}%{profit:>11,.0f}"
                  f"{dd:>9.0f}{ratio:>8.2f}{longest:>12,}"
                  f"{worst_streak(r):>8}")
        print()
        print(f"  worst episodes for '{name}' (depth in units, dates):")
        for label, _ in SERIES:
            eq = curves[label]
            print(f"    {label:<9}", end="")
            for depth, pi, ti, rec in episodes(eq, 3):
                pi_i = max(pi, 0)
                when = (f"{dates[pi_i]} -> {dates[ti]}, "
                        f"recovered {dates[rec] if rec > 0 else 'not yet'}")
                print(f"  -{depth:.0f}u {when}", end="")
            print()
        print()
        pd.DataFrame(curves).to_csv(
            f"reports/_stride_dd_{col.replace('p_', '')}.csv", index=False)
        chart(name, dates, curves)
    print("equity curves saved to reports/_stride_dd_*.csv")


if __name__ == "__main__":
    main()

