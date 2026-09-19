"""
SAME RULE, TWO FEEDS - do SData and RaceIQ v2 agree, and what is each worth?
============================================================================
The audited Speed & Stride system was measured on Proform ``SData`` (the feed
that stopped 2026-04-30).  The live system runs on the RacingTV ``RaceIQ v2``
feed instead.  Until now the two were never compared reading-by-reading: the
audit reported +9.46%/+6.47% and the RaceIQ replication reported +0.09%/-23.16%
on different windows, so the gap could have been the data, the period, or the
method.

This script holds the method fixed and varies only the feed.

    PART A  MATCH-UP   every runner both feeds measured, same run: correlation,
                       bias, error, and whether they agree on which horse was
                       fastest / longest-strided in the race
    PART B  PRICE      the audit's own clean frame - BFSP as the complete field,
                       LEFT JOIN the lagged reading, best runner per race, level
                       stakes at Betfair BSP - run once per feed on the SAME
                       races, plus the all-runner baseline as a lie detector

Arms:  sdata-id    SData lagged by horse id   (the published audit's method)
       sdata-name  SData lagged by horse name (isolates the lag key)
       raceiq-v2   RaceIQ v2 lagged by name  (what the live system does)

Read-only: it reads PRODB and RACINGTV_2023_2026 and prints.  It publishes
nothing, writes no claims, and touches no cache.

    python scripts\\compare_sources_speed_stride.py
    python scripts\\compare_sources_speed_stride.py --from 2026-01-15 --to 2026-04-30
"""
from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd
import pyodbc

PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=300;"
       "MultipleActiveResultSets=True;")
RTV = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=RACINGTV_2023_2026;"
       "Trusted_Connection=yes;Connection Timeout=300;"
       "MultipleActiveResultSets=True;")

KEYS = ["RaceDate", "CourseClean", "HorseClean"]
COMMISSION_2, COMMISSION_5 = 0.02, 0.05
# The two feeds do not share units: Proform's SL_Finish is stride length in FEET
# (18-22 for a horse in full flight), while RaceIQ's StrideM is metres. RaceTime
# is deliberately NOT a join key: v2 stores it as text ('12:48') and PRODB as a
# real time (13:35:00), so date+course+horse is the only reliable shared key.
FEET_TO_M = 0.3048
# The live rule's plausibility bands (cloud_app/speed_stride_rule.py).  Applied
# to BOTH feeds so a junk read is junk on either side.
SPEED_BAND = (25.0, 55.0)
STRIDE_BAND = (5.0, 10.0)


def clean(value) -> str:
    """lowercase, alphanumerics only - the audit's HorseClean / CourseClean."""
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def load_sdata(lo: str, hi: str) -> pd.DataFrame:
    """Proform readings, one row per runner per run, priors computed two ways."""
    sql = f"""
        SELECT S.SD_HNo, RH.RH_DateTime, CN.C_Name AS CourseRaw,
               H.H_Name_No_Anything AS HorseRaw,
               S.[SL_Finish] AS s_stride, S.[MPH_Finish] AS s_speed
        FROM dbo.SData S
        JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.SD_RNo
        JOIN dbo.NEW_H  H  ON H.H_No  = S.SD_HNo
        LEFT JOIN dbo.NEW_C CN ON CN.C_ID = RH.RH_CNo
        WHERE RH.RH_DateTime >= '{lo}' AND RH.RH_DateTime <= '{hi}'
    """
    conn = pyodbc.connect(PRO)
    df = pd.read_sql(sql, conn)
    conn.close()
    df["when"] = pd.to_datetime(df["RH_DateTime"])
    df["RaceDate"] = df["when"].dt.date
    df["RaceTime"] = df["when"].dt.time
    df["HorseClean"] = df["HorseRaw"].map(clean)
    df["CourseClean"] = df["CourseRaw"].map(clean)
    df["s_stride"] = pd.to_numeric(df["s_stride"], errors="coerce")
    df["s_speed"] = pd.to_numeric(df["s_speed"], errors="coerce")
    df["s_stride_ft"] = df["s_stride"]              # as Proform stores it
    df["s_stride"] = df["s_stride_ft"] * FEET_TO_M   # metres, to match RaceIQ
    df.loc[~df["s_speed"].between(*SPEED_BAND), "s_speed"] = np.nan
    df.loc[~df["s_stride"].between(*STRIDE_BAND), "s_stride"] = np.nan
    df = df.sort_values(["SD_HNo", "when"])
    for metric in ("stride", "speed"):
        df[f"pid_{metric}"] = df.groupby("SD_HNo")[f"s_{metric}"].shift(1)
    df = df.sort_values(["HorseClean", "when"])
    for metric in ("stride", "speed"):
        df[f"pnm_{metric}"] = df.groupby("HorseClean")[f"s_{metric}"].shift(1)
    return df.drop_duplicates(KEYS)


def load_raceiq(lo: str, hi: str) -> pd.DataFrame:
    """RaceIQ v2 readings, one row per runner per race, priors by horse name."""
    sql = f"""
        SELECT RaceDate, RaceTime, Venue AS CourseRaw, Horse AS HorseRaw,
               StrideM AS v_stride, TopSpeedMph AS v_speed
        FROM dbo.Scraped_RaceIQ_v2
        WHERE RaceDate >= '{lo}' AND RaceDate <= '{hi}'
    """
    conn = pyodbc.connect(RTV)
    df = pd.read_sql(sql, conn)
    conn.close()
    df["RaceDate"] = pd.to_datetime(df["RaceDate"]).dt.date
    df["when"] = pd.Series(
        [f"{d} {t}" for d, t in zip(df["RaceDate"], df["RaceTime"], strict=True)],
        index=df.index)
    df["HorseClean"] = df["HorseRaw"].map(clean)
    df["CourseClean"] = df["CourseRaw"].map(clean)
    df["v_stride"] = pd.to_numeric(df["v_stride"], errors="coerce")
    df["v_speed"] = pd.to_numeric(df["v_speed"], errors="coerce")
    df.loc[~df["v_speed"].between(*SPEED_BAND), "v_speed"] = np.nan
    df.loc[~df["v_stride"].between(*STRIDE_BAND), "v_stride"] = np.nan
    df = df.sort_values(["HorseClean", "when"])
    for metric in ("stride", "speed"):
        df[f"pv_{metric}"] = df.groupby("HorseClean")[f"v_{metric}"].shift(1)
    return df.drop_duplicates(KEYS)


def load_bfsp(lo: str, hi: str) -> pd.DataFrame:
    """Every priced runner - the complete field, winner included."""
    sql = f"""
        SELECT RaceDate, RaceTime, CourseClean, HorseClean, BSP_TRUE, WinLose, EventID
        FROM dbo.BFSP
        WHERE RaceDate >= '{lo}' AND RaceDate <= '{hi}' AND BSP_TRUE > 1
    """
    conn = pyodbc.connect(PRO)
    df = pd.read_sql(sql, conn)
    conn.close()
    df["RaceDate"] = pd.to_datetime(df["RaceDate"]).dt.date
    df["CourseClean"] = df["CourseClean"].map(clean)
    df["HorseClean"] = df["HorseClean"].map(clean)
    df["WinLose"] = pd.to_numeric(df["WinLose"], errors="coerce").fillna(0).astype(int)
    df["implied"] = 1.0 / df["BSP_TRUE"]
    return df


SDATA_KEEP = [*KEYS, "s_speed", "s_stride", "s_stride_ft", "pid_speed", "pid_stride",
              "pnm_speed", "pnm_stride"]
RACEIQ_KEEP = [*KEYS, "v_speed", "v_stride", "pv_speed", "pv_stride"]


def build_frame(lo: str, hi: str) -> pd.DataFrame:
    """BFSP as the field, both feeds attached on the same four keys."""
    s = load_sdata(lo, hi)
    v = load_raceiq(lo, hi)
    b = load_bfsp(lo, hi)
    print(f"SData runs pulled    : {len(s):,}   {lo} to {hi}")
    print(f"RaceIQ v2 rows pulled: {len(v):,}   "
          f"{v['RaceDate'].min() if len(v) else '-'} to {v['RaceDate'].max() if len(v) else '-'}")
    print(f"BFSP runners         : {len(b):,}   in {b['EventID'].nunique():,} races")
    f = b.merge(s[SDATA_KEEP], on=KEYS, how="left")
    f = f.merge(v[RACEIQ_KEEP], on=KEYS, how="left")
    print(f"frame rows           : {len(f):,}")
    return f


def top_horse(frame: pd.DataFrame, col: str) -> pd.Series:
    """The horse with the highest reading per race, as race -> horse."""
    has = frame[frame[col].notna()]
    if has.empty:
        return pd.Series(dtype=object)
    return has.loc[has.groupby("EventID")[col].idxmax()].set_index("EventID")["HorseClean"]


def part_a(f: pd.DataFrame) -> None:
    print()
    print("=" * 108)
    print("PART A - DO THE TWO FEEDS MEASURE THE SAME THING?  (same runner, same run)")
    print("=" * 108)
    print(f"  field coverage   sdata {f['s_speed'].notna().mean() * 100:5.1f}% of priced "
          f"runners carry a speed, {f['s_stride'].notna().mean() * 100:5.1f}% a stride")
    print(f"                   raceiq {f['v_speed'].notna().mean() * 100:5.1f}% carry a "
          f"speed, {f['v_stride'].notna().mean() * 100:5.1f}% a stride")
    for metric, label, tol in (("speed", "top speed (mph)   ", 1.0),
                               ("stride", "stride length (m) ", 0.25)):
        s_col, v_col = f"s_{metric}", f"v_{metric}"
        m = f[f[s_col].notna() & f[v_col].notna()]
        print()
        print(f"  {label} - {len(m):,} runner-runs measured by both")
        if len(m) < 30:
            print("      too few matched readings to say anything")
            continue
        s = m[s_col].to_numpy(dtype=float)
        v = m[v_col].to_numpy(dtype=float)
        d = v - s
        r = float(np.corrcoef(s, v)[0, 1])
        print(f"      sdata   mean {s.mean():7.3f}   median {np.median(s):7.3f}"
              f"   sd {s.std(ddof=1):6.3f}")
        print(f"      raceiq  mean {v.mean():7.3f}   median {np.median(v):7.3f}"
              f"   sd {v.std(ddof=1):6.3f}")
        print(f"      bias (raceiq - sdata)  mean {d.mean():+.3f}   median {np.median(d):+.3f}")
        print(f"      error                  MAE {np.abs(d).mean():.3f}"
              f"   RMS {math.sqrt((d ** 2).mean()):.3f}   pearson r {r:.3f}")
        if metric == "stride" and "s_stride_ft" in m.columns:
            raw_ft = float(m["s_stride_ft"].mean())
            print(f"      unit check             sdata raw mean {raw_ft:.2f} ft"
                  f" -> {raw_ft * FEET_TO_M:.2f} m   vs raceiq {v.mean():.2f} m")
        within = float((np.abs(d) <= tol).mean())
        print(f"      within +/-{tol:<5}       {within:.1%} of pairs")
        hs, hv = top_horse(m, s_col), top_horse(m, v_col)
        common = hs.index.intersection(hv.index)
        if len(common):
            same = float((hs.loc[common] == hv.loc[common]).mean())
            print(f"      RACE-LEVEL: same horse tops both feeds in {same:.1%}"
                  f" of {len(common):,} races")
    print()
    print("  Read this as: high r with a bias means the feeds rank runners the same")
    print("  way but disagree on the absolute figure; the race-level line is the one")
    print("  that matters, because the rule bets on the top reading in each race.")


ARMS = [
    ("sdata-id", "pid_speed", "pid_stride", "SData lagged by horse id (published)"),
    ("sdata-name", "pnm_speed", "pnm_stride", "SData lagged by horse name (control)"),
    ("raceiq-v2", "pv_speed", "pv_stride", "RaceIQ v2 lagged by name (live system)"),
]


def ret(d: pd.DataFrame, commission: float = 0.0) -> np.ndarray:
    """Level-stake return at Betfair BSP, optionally net of commission."""
    win = (d["WinLose"] == 1).to_numpy()
    gross = np.where(win, d["BSP_TRUE"].to_numpy() - 1.0, -1.0)
    if commission:
        gross = np.where(win, gross * (1.0 - commission), gross)
    return gross


def picks(d: pd.DataFrame, col: str) -> pd.DataFrame:
    """Best runner per race among those carrying a previous-run figure."""
    has = d[d[col].notna()]
    if has.empty:
        return has
    return has.loc[has.groupby("EventID")[col].idxmax()]


def window(f: pd.DataFrame, lo: str, hi: str) -> pd.DataFrame:
    dates = pd.to_datetime(f["RaceDate"]).dt.date
    return f[(dates >= pd.to_datetime(lo).date()) & (dates <= pd.to_datetime(hi).date())]


def part_b(f: pd.DataFrame, label: str, lo: str, hi: str) -> None:
    w = window(f, lo, hi)
    print()
    print("=" * 108)
    print(f"PART B - WHAT IS EACH FEED WORTH?   {label}   {lo} to {hi}")
    print("=" * 108)
    if w.empty:
        print("  no races in this window")
        return
    field = ret(w)
    n_races = w["EventID"].nunique()
    print(f"  field: {len(w):,} priced runners in {n_races:,} races")
    print(f"  LIE DETECTOR - backing every runner at BSP: {field.mean() * 100:+.3f}%")
    print(f"                 field implied probability  : "
          f"{w.groupby('EventID')['implied'].sum().mean():.4f}   (honest is ~1.00)")
    print()
    print(f"  {'arm / rule':<34}{'picks':>7}{'cov':>7}{'win%':>7}{'BSP says':>9}"
          f"{'gap':>7}{'gross':>8}{'net 2%':>8}{'net 5%':>8}{'maxDD':>7}{'run':>6}")
    print("  " + "-" * 106)
    for arm, sp_col, st_col, _blurb in ARMS:
        for rule, col in (("fastest prev run", sp_col), ("longest prev stride", st_col)):
            p = picks(w, col)
            if p.empty:
                print(f"  {arm + ' / ' + rule:<34}      0 picks - no coverage here")
                continue
            gross = ret(p)
            net = ret(p, COMMISSION_2)
            eq = np.cumsum(net)
            dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
            streak = cur = 0
            for x in net:
                cur = cur + 1 if x < 0 else 0
                streak = max(streak, cur)
            wins = float((p["WinLose"] == 1).mean())
            imp = float(p["implied"].mean())
            cov = p["EventID"].nunique() / n_races
            print(f"  {arm + ' / ' + rule:<34}{len(p):>7,}{cov:>6.0%}{wins * 100:>6.2f}%"
                  f"{imp * 100:>8.2f}%{(wins - imp) * 100:>6.2f}%{gross.mean() * 100:>7.2f}%"
                  f"{net.mean() * 100:>7.2f}%{ret(p, COMMISSION_5).mean() * 100:>7.2f}%"
                  f"{dd:>7.0f}{streak:>6}")
    print()
    print("  cov = share of the window's races the feed can price a pick in.")
    print("  Same frame, same rule, same races - only the feed changes.")
    print("  Note: both feeds are banded to the live rule (25-55 mph, 5-10 m) here,")
    print("  so the sdata-id row is the audit's method plus that band, not the")
    print("  published figure reproduced exactly.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--load-from", default="2024-12-01",
                    help="pull readings from here so lagged priors have warmed up")
    ap.add_argument("--load-to", default="2026-09-18")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    args = ap.parse_args()

    f = build_frame(args.load_from, args.load_to)
    if args.date_from:
        f = window(f, args.date_from, args.date_to or args.load_to)
        print(f"restricted to        : {args.date_from} to {args.date_to or args.load_to} "
              f"({len(f):,} runners)")
    if f.empty:
        print("nothing in the frame - check the window")
        return 1

    part_a(f)
    overlap_2026 = ("overlap 2026 (SData's last months)",
                    "2026-01-15", "2026-04-30")
    overlap_2025 = ("overlap 2025 (both feeds)", "2025-01-01", "2025-06-05")
    v2_only = ("RaceIQ v2 only - after SData stopped", "2026-05-01", "2026-09-18")
    for label, lo, hi in (overlap_2026, overlap_2025, v2_only):
        if not window(f, lo, hi).empty:
            part_b(f, label, lo, hi)
    print()
    print("=" * 108)
    print("If RaceIQ v2 tops the market baseline and SData does not (or vice versa) on")
    print("the same races, the feed is the difference.  If both are alike there, the")
    print("gap was the period - and the period is the thing the backfill is fixing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



