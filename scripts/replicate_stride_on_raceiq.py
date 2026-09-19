"""
REPLICATE the audited Speed & Stride test on the RacingTV RaceIQ feed
=====================================================================
The audit (STRIDE_SYSTEM.md §3, reports/_stride_cleanframe.txt) measured, on
``PRODB.dbo.BFSP LEFT JOIN lagged PRODB.dbo.SData``:

    SPEED  (highest previous-run top speed)   +9.46% net of 2% at Betfair BSP
    STRIDE (longest previous-run stride)      +6.47% net of 2% at Betfair BSP
    pooled 38,420 picks, frame validated by an all-runner baseline of -2.213%

That feed stops on 2026-04-30, so the system cannot be produced from it at all.
This script asks the *same question of the live feed*, with the same method:

  * frame      - BFSP (every priced runner) LEFT JOIN lagged RaceIQ metrics, so
                 the field is complete and the all-runner baseline is honest
  * picks      - highest previous-run TopSpeedMph (SPEED) / StrideM (STRIDE),
                 only among runners carrying a previous-run figure
  * prices     - real Betfair BSP, 1u level stakes, net of 2% and 5% commission
  * controls   - every runner at BSP, and the covered-runner pool
  * per era    - picks, strike, BSP-implied, gap, gross, net 2%, net 5%, max DD,
                 worst losing run, and the paired t versus the same race's
                 average runner (the audit's confidence measure)

It also prints coverage - how much of each field carries a figure - because the
audit's own caveat is that the system only fires where the feed exists.

    python scripts\\replicate_stride_on_raceiq.py --from 2026-08-01 --source v2
    python scripts\\replicate_stride_on_raceiq.py --from 2023-01-01 --source v1

Note on which feed to believe: the v1 RaceIQ parser mis-read its own column
labels (35% of TopSpeed reads are the number 20), so v1 results are a weak
shadow of the live feed.  raceiq_scrape_v2.py is the one to judge.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import sys

import pandas as pd
import pyodbc

RTV_CONN = (r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;"
            r"Database=RACINGTV_2023_2026;Trusted_Connection=yes;"
            r"MultipleActiveResultSets=True;")
PRO_CONN = (r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;"
            r"Database=PRODB;Trusted_Connection=yes;Connection Timeout=300;"
            r"MultipleActiveResultSets=True;")

COMMISSION_2, COMMISSION_5 = 0.02, 0.05
# Same plausibility bands the live system uses (cloud_app/speed_stride_rule.py).
SPEED_BAND = (25.0, 55.0)
STRIDE_BAND = (5.0, 10.0)


def clean_name(value) -> str:
    """lowercase, alphanumerics only - the audit's HorseClean / CourseClean."""
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def load_bfsp(date_from: str, date_to: str) -> pd.DataFrame:
    """Every priced runner with its real Betfair SP and result."""
    sql = f"""
        SELECT RaceDate, RaceTime, CourseClean, HorseClean, BSP_TRUE, WinLose
        FROM dbo.BFSP
        WHERE RaceDate >= '{date_from}' AND RaceDate <= '{date_to}' AND BSP_TRUE > 1
    """
    conn = pyodbc.connect(PRO_CONN)
    df = pd.read_sql(sql, conn)
    conn.close()
    df["RaceDate"] = pd.to_datetime(df["RaceDate"]).dt.date
    df["CourseClean"] = df["CourseClean"].map(clean_name)
    df["HorseClean"] = df["HorseClean"].map(clean_name)
    df["Won"] = pd.to_numeric(df["WinLose"], errors="coerce").fillna(0).astype(int)
    df["RaceKey"] = pd.Series(
        [f"{d}|{t}|{c}" for d, t, c in
         zip(df["RaceDate"], df["RaceTime"], df["CourseClean"], strict=True)],
        index=df.index)
    df["date_str"] = df["RaceDate"].astype(str)
    return df


def raceiq_sql(source: str, date_from: str, date_to: str) -> str:
    if source == "v2":
        return f"""
            SELECT RaceDate, RaceTime, Venue AS CourseRaw, Horse AS HorseRaw,
                   StrideM AS stride, TopSpeedMph AS speed
            FROM dbo.Scraped_RaceIQ_v2
            WHERE RaceDate >= '{date_from}' AND RaceDate <= '{date_to}'
        """
    return f"""
        SELECT RaceDate, RaceTime, CourseName AS CourseRaw, HorseName AS HorseRaw,
               TRY_CAST(StrideLength AS float) AS stride,
               TRY_CAST(TopSpeed AS float) AS speed
        FROM dbo.Scraped_RaceIQ
        WHERE RaceDate >= '{date_from}' AND RaceDate <= '{date_to}'
    """


def load_raceiq(source: str, date_from: str, date_to: str) -> pd.DataFrame:
    """RaceIQ readings, one row per runner per race, junk values dropped."""
    conn = pyodbc.connect(RTV_CONN)
    df = pd.read_sql(raceiq_sql(source, date_from, date_to), conn)
    conn.close()
    df["RaceDate"] = pd.to_datetime(df["RaceDate"]).dt.date
    df["date_str"] = df["RaceDate"].astype(str)
    df["HorseClean"] = df["HorseRaw"].map(clean_name)
    df["CourseClean"] = df["CourseRaw"].map(clean_name)
    df["stride"] = pd.to_numeric(df["stride"], errors="coerce")
    df["speed"] = pd.to_numeric(df["speed"], errors="coerce")
    df.loc[~df["stride"].between(*STRIDE_BAND), "stride"] = None
    df.loc[~df["speed"].between(*SPEED_BAND), "speed"] = None
    return df.dropna(subset=["stride", "speed"], how="all")



def lag_metrics(raceiq: pd.DataFrame) -> pd.DataFrame:
    """Each horse's PREVIOUS-run figures, with the date they came from."""
    df = raceiq.dropna(subset=["HorseClean"]).copy()
    df = df.groupby(["HorseClean", "date_str"], as_index=False).agg(
        stride=("stride", "max"), speed=("speed", "max"))
    df = df.sort_values(["HorseClean", "date_str"])
    grouped = df.groupby("HorseClean", sort=False)
    out = df.copy()
    for metric in ("stride", "speed"):
        out[f"p_{metric}"] = grouped[metric].shift(1)
        out[f"psrc_{metric}"] = grouped["date_str"].shift(1)
    return out[["HorseClean", "date_str", "p_stride", "p_speed",
                "psrc_stride", "psrc_speed"]]


def build_frame(bfsp: pd.DataFrame, lagged: pd.DataFrame) -> pd.DataFrame:
    """The field (BFSP) with lagged RaceIQ figures attached."""
    frame = bfsp.copy()
    frame = frame.merge(lagged, on=["date_str", "HorseClean"], how="left")
    # A "previous" figure that actually came from today is not a previous run.
    for metric in ("stride", "speed"):
        same_day = frame[f"psrc_{metric}"] == frame["date_str"]
        frame.loc[same_day, [f"p_{metric}", f"psrc_{metric}"]] = None
    return frame


def price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Implied probability and each runner's 1u BSP return, gross and net."""
    frame = frame.copy()
    overround = frame.groupby("RaceKey")["BSP_TRUE"].transform(lambda s: (1.0 / s).sum())
    frame["implied"] = (1.0 / frame["BSP_TRUE"]) / overround
    won = frame["Won"] == 1
    for name, commission in (("gross", 0.0), ("net2", COMMISSION_2),
                             ("net5", COMMISSION_5)):
        frame[f"ret_{name}"] = -1.0
        frame.loc[won, f"ret_{name}"] = (frame.loc[won, "BSP_TRUE"] - 1.0) * (1 - commission)
    frame["race_avg_net2"] = frame.groupby("RaceKey")["ret_net2"].transform("mean")
    return frame


def max_drawdown(returns: pd.Series) -> float:
    """Deepest peak-to-trough fall of the level-stakes curve, in stake units."""
    curve = returns.cumsum()
    return float((curve.cummax() - curve).max()) if len(curve) else 0.0


def worst_losing_run(picks: pd.DataFrame) -> int:
    best = run = 0
    for won in picks.sort_values(["RaceDate", "RaceTime"])["Won"]:
        run = 0 if won else run + 1
        best = max(best, run)
    return best


def paired_t(picks: pd.DataFrame) -> float:
    """t of (pick return - same race's average runner return), one per race."""
    diff = (picks["ret_net2"] - picks["race_avg_net2"]).dropna()
    if len(diff) < 2 or diff.std(ddof=1) == 0:
        return float("nan")
    return float(diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff))))


def picks_for(frame: pd.DataFrame, metric: str) -> pd.DataFrame:
    """One pick per race: the highest previous-run figure that exists."""
    column = f"p_{metric}"
    eligible = frame[frame[column].notna()]
    if eligible.empty:
        return eligible
    ranked = eligible.sort_values(column, ascending=False)
    chosen = ranked.groupby("RaceKey", as_index=False).head(1)
    return chosen.sort_values(["RaceDate", "RaceTime"])


def summarise(label: str, picks: pd.DataFrame, pool: pd.DataFrame) -> dict:
    if len(picks) == 0:
        print(f"  {label:<22} no picks - the feed has no coverage in this window")
        return {}
    stats = {
        "picks": len(picks),
        "strike_pct": picks["Won"].mean() * 100,
        "bsp_implied_pct": picks["implied"].mean() * 100,
        "gross_pct": picks["ret_gross"].mean() * 100,
        "net2_pct": picks["ret_net2"].mean() * 100,
        "net5_pct": picks["ret_net5"].mean() * 100,
        "coverage_pct_of_field": pool["has_figure"].mean() * 100,
        "pool_net2_pct": pool.loc[pool["has_figure"], "ret_net2"].mean() * 100,
        "max_dd_units": max_drawdown(picks["ret_net2"]),
        "worst_run": worst_losing_run(picks),
        "paired_t": paired_t(picks),
    }
    stats["gap_pts"] = stats["strike_pct"] - stats["bsp_implied_pct"]
    print(f"  {label:<22} picks {stats['picks']:>7,}  strike {stats['strike_pct']:>5.2f}%  "
          f"BSP says {stats['bsp_implied_pct']:>5.2f}%  gap {stats['gap_pts']:>+5.2f}  "
          f"gross {stats['gross_pct']:>+6.2f}%  net2 {stats['net2_pct']:>+6.2f}%  "
          f"net5 {stats['net5_pct']:>+6.2f}%  DD {stats['max_dd_units']:>5.0f}u  "
          f"run {stats['worst_run']:>3}  t {stats['paired_t']:>5.2f}")
    return stats


def coverage_report(label: str, frame: pd.DataFrame) -> None:
    races = frame["RaceKey"].nunique()
    print(f"\n  {label}: {len(frame):,} priced runners in {races:,} races")
    for metric in ("speed", "stride"):
        have = frame[f"p_{metric}"].notna()
        print(f"    previous-run {metric:<6}: {have.sum():>7,} runners "
              f"({have.mean() * 100:>5.1f}% of the field, "
              f"{have.sum() / max(races, 1):.1f} per race)")
    print("    audit (SData) for comparison : 43.6% of the field, ~4.1 per race")



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2026-08-01")
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--source", choices=("v1", "v2"), default="v2",
                    help="v2 = raceiq_scrape_v2.py (the live feed); v1 = the old parser")
    args = ap.parse_args()

    date_to = args.date_to or dt.date.today().isoformat()
    print("=" * 122)
    print(f"  RACEIQ REPLICATION OF THE SPEED & STRIDE AUDIT   "
          f"{args.date_from} -> {date_to}   source={args.source}")
    print("=" * 122)

    bfsp = load_bfsp(args.date_from, date_to)
    raceiq = load_raceiq(args.source, args.date_from, date_to)
    print(f"  BFSP priced runners : {len(bfsp):,}  in {bfsp['RaceKey'].nunique():,} races")
    print(f"  RaceIQ rows pulled  : {len(raceiq):,}")
    if raceiq.empty:
        print("  nothing to test - backfill the feed for this window first")
        return 1

    frame = price_frame(build_frame(bfsp, lag_metrics(raceiq)))
    coverage_report(f"{args.source} coverage", frame)

    print("\n  CONTROLS (the audit's lie detector)")
    controls = (("every runner at BSP", pd.Series(True, index=frame.index)),
                ("covered pool (stride)", frame["p_stride"].notna()),
                ("covered pool (speed)", frame["p_speed"].notna()))
    for label, mask in controls:
        subset = frame[mask]
        if subset.empty:
            continue
        print(f"    {label:<24} bets {len(subset):>7,}  net2 "
              f"{subset['ret_net2'].mean() * 100:>+6.2f}%")

    years = sorted({day.year for day in frame["RaceDate"]})
    eras = [(str(y), f"{y}-01-01", f"{y}-12-31") for y in years]
    eras.append(("pooled", args.date_from, date_to))

    for metric in ("speed", "stride"):
        print(f"\n  === {metric.upper()} RULE - highest previous-run {metric} ===")
        for label, start, end in eras:
            era = frame[(frame["RaceDate"] <= dt.date.fromisoformat(end))
                        & (frame["RaceDate"] >= dt.date.fromisoformat(start))].copy()
            if era.empty:
                continue
            era["has_figure"] = era[f"p_{metric}"].notna()
            summarise(label, picks_for(era, metric), era)

    print("\n  AUDIT FOR REFERENCE (Proform SData, 2021-01-01..2026-04-30, BSP, net 2%)")
    print("    SPEED  picks 38,420  strike 19.41%  BSP says 17.71%  gap +1.70  "
          "gross +11.30%  net2 +9.46%  DD 286u  run 45  t 5.84")
    print("    STRIDE picks 38,420  strike 17.42%  BSP says 16.09%  gap +1.33  "
          "gross +8.28%  net2 +6.47%  DD 452u  run 53  t 4.18")
    return 0


if __name__ == "__main__":
    sys.exit(main())
