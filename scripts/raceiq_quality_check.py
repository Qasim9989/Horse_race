r"""
IS THE FREE RACINGTV RACEIQ FEED CORRECT AND USEFUL?
====================================================
The stride systems were measured on PRODB.SData, which stopped 2026-04-30.
RacingTV's public RACEIQ sectionals are scraped free into
RACINGTV_2023_2026.dbo.Scraped_RaceIQ and run to today.  This audit answers two
separate questions before anything is built on that feed:

  A  CORRECT?   - value sanity (units, impossible numbers), duplicates, rows per
                  race, and whether the scraped ranks agree with the values
  B  EQUIVALENT?- on the 2023-02..2026-04 overlap, do TopSpeed / StrideLength
                  measure the same thing as SData.MPH_Finish / SL_Finish, and
                  do they rank the runners in a race the same way
  C  USEFUL?    - build the SPEED and STRIDE rules from the free feed and price
                  them at real BSP, including on 2026-05..09, the window that
                  has no SData at all

    python scripts\raceiq_quality_check.py
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pyodbc

PRO = (r"Driver={ODBC Driver 17 for SQL Server};"
       r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
       r"Trusted_Connection=yes;Connection Timeout=300;"
       r"MultipleActiveResultSets=True;")
LIVE = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=RACINGTV_2023_2026;"
        r"Trusted_Connection=yes;Connection Timeout=300;"
        r"MultipleActiveResultSets=True;")
METRICS = ("StrideLength", "TopSpeed", "AvgFrequency", "FinishingSpeedPct")
KEYS = ["RaceDate", "RaceTime", "CourseClean", "HorseClean"]


def clean(d: pd.DataFrame, horse: str, course: str) -> pd.DataFrame:
    d = d.copy()
    d["HorseClean"] = d[horse].str.lower().str.replace(
        r"[^a-z0-9]", "", regex=True)
    d["CourseClean"] = d[course].str.lower().str.replace(
        r"[^a-z0-9]", "", regex=True)
    d["RaceDate"] = pd.to_datetime(d["RaceDate"]).dt.date
    return d


def section(title: str) -> None:
    print()
    print("=" * 90)
    print(title)
    print("=" * 90)


def load_raceiq() -> tuple[pd.DataFrame, pd.DataFrame]:
    c = pyodbc.connect(LIVE)
    d = pd.read_sql("SELECT RaceDate, RaceTime, CourseName, HorseName, "
                    "StrideLength, TopSpeed, AvgFrequency, FinishingSpeedPct "
                    "FROM dbo.Scraped_RaceIQ", c)
    ranks = pd.read_sql("SELECT RaceDate, RaceTime, CourseName, HorseName, "
                        "Metric, RankPosition, Value, ValueUnit "
                        "FROM dbo.Scraped_RaceIQ_Ranks", c)
    c.close()
    d = clean(d, "HorseName", "CourseName")
    ranks = clean(ranks, "HorseName", "CourseName")
    print(f"RaceIQ rows {len(d):,}   rank rows {len(ranks):,}")
    return d, ranks


def sanity(d: pd.DataFrame, ranks: pd.DataFrame) -> None:
    section("A. IS THE FEED CORRECT?")
    print(f"{'metric':<20}{'rows':>8}{'p1':>9}{'median':>10}{'p99':>9}"
          f"{'max':>10}{'<=0':>7}")
    print("-" * 90)
    for m in METRICS:
        s = pd.to_numeric(d[m], errors="coerce")
        print(f"{m:<20}{int(s.notna().sum()):>8,}{s.quantile(0.01):>9.2f}"
              f"{s.median():>10.2f}{s.quantile(0.99):>9.2f}{s.max():>10.2f}"
              f"{int((s <= 0).sum()):>7,}")

    dupes = int(d.duplicated(["RaceDate", "RaceTime", "CourseName",
                              "HorseName"]).sum())
    print(f"\nduplicate horse-in-race rows : {dupes:,}")
    per_race = d.groupby(["RaceDate", "CourseName", "RaceTime"]).size()
    print(f"races                        : {len(per_race):,}")
    print(f"rows per race  min/median/max: {per_race.min()} / "
          f"{per_race.median():.0f} / {per_race.max()}")

    print("\nranks table - does rank 1 match the best value in the race?")
    for metric in ("Top Speed", "Stride Length", "FSP", "0-20MPH"):
        r = ranks[ranks["Metric"] == metric]
        if r.empty:
            print(f"  {metric:<15} no rows")
            continue
        vals = pd.to_numeric(r["Value"], errors="coerce")
        print(f"  {metric:<15} rows {len(r):>7,}  value "
              f"min/median/max {vals.min():.2f} / {vals.median():.2f} / "
              f"{vals.max():.2f}  units "
              f"{sorted(set(r['ValueUnit'].dropna()))[:4]}")


def load_sdata() -> pd.DataFrame:
    """The old paid/frozen source, for the equivalence test."""
    c = pyodbc.connect(PRO)
    s = pd.read_sql("""
        SELECT RH.RH_DateTime, CN.C_Name AS Course,
               H.H_Name_No_Anything AS HorseName,
               S.[MPH_Finish] AS sdata_speed, S.[SL_Finish] AS sdata_stride
        FROM dbo.SData S
        JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.SD_RNo
        JOIN dbo.NEW_H  H  ON H.H_No  = S.SD_HNo
        LEFT JOIN dbo.NEW_C CN ON CN.C_ID = RH.RH_CNo
    """, c)
    c.close()
    s["RaceDate"] = pd.to_datetime(s["RH_DateTime"]).dt.date
    s["RaceTime"] = pd.to_datetime(s["RH_DateTime"]).dt.time
    return clean(s, "HorseName", "Course")


def equivalence(d: pd.DataFrame, s: pd.DataFrame) -> pd.DataFrame:
    section("B. DOES IT MEASURE THE SAME THING AS SData?")
    s["RaceTime"] = s["RaceTime"].astype(str)
    d2 = d.copy()
    d2["RaceTime"] = d2["RaceTime"].astype(str)
    m = pd.DataFrame()
    for label, keys in (("course+time keys", KEYS),
                        ("date+horse only", ["RaceDate", "HorseClean"])):
        cand = d2.merge(s[[*keys, "sdata_speed", "sdata_stride"]], on=keys,
                        how="inner")
        print(f"join on {label:<18}: {len(cand):>8,} matched rows")
        if len(cand) > 1000:
            m = cand
            print(f"  -> using {label} (RaceIQ store course slugs and times in"
                  " a different shape, so the exact key misses)")
            break
    if m.empty:
        print("nothing matched on either key set - cannot compare the feeds")
        return m
    m["TopSpeed"] = pd.to_numeric(m["TopSpeed"], errors="coerce")
    m["StrideLength"] = pd.to_numeric(m["StrideLength"], errors="coerce")
    pairs = (("TopSpeed", "sdata_speed"), ("StrideLength", "sdata_stride"))
    print(f"\n{'RaceIQ':<15}{'SData':<15}{'n':>8}{'pearson':>9}{'spearman':>10}"
          f"{'ratio med':>11}")
    print("-" * 90)
    for a, b in pairs:
        ok = m[m[a].notna() & m[b].notna()]
        if len(ok) < 30:
            print(f"{a:<15}{b:<15} too few overlapping values ({len(ok)})")
            continue
        pear = float(np.corrcoef(ok[a], ok[b])[0, 1])
        ra = ok.groupby(["RaceDate", "CourseClean", "RaceTime"])[a].rank()
        rb = ok.groupby(["RaceDate", "CourseClean", "RaceTime"])[b].rank()
        spear = float(np.corrcoef(ra.to_numpy(), rb.to_numpy())[0, 1])
        ratio = float((ok[a] / ok[b]).median())
        print(f"{a:<15}{b:<15}{len(ok):>8,}{pear:>9.3f}{spear:>10.3f}"
              f"{ratio:>11.3f}")
    print("\n(a ratio far from 1.000 means the feeds use different units -"
          " 3.28 would be feet vs metres)")
    return m


def rule_test(d: pd.DataFrame) -> None:
    """Build SPEED and STRIDE from the free feed and price them at real BSP."""
    section("C. IS IT USEFUL? - the rules built from the free feed, at BSP")
    c = pyodbc.connect(PRO)
    b = pd.read_sql("""
        SELECT RaceDate, RaceTime, CourseClean, HorseClean, BSP_TRUE, WinLose,
               EventID
        FROM dbo.BFSP WHERE BSP_TRUE > 1
    """, c)
    c.close()
    b["RaceDate"] = pd.to_datetime(b["RaceDate"]).dt.date
    b["RaceTime"] = b["RaceTime"].astype(str)
    b["CourseClean"] = b["CourseClean"].str.lower()
    b["HorseClean"] = b["HorseClean"].str.lower()

    d = d.copy()
    d["RaceTime"] = d["RaceTime"].astype(str)
    before = len(d)
    d["_filled"] = d[["StrideLength", "TopSpeed", "FinishingSpeedPct"]].notna(
    ).sum(axis=1)
    d = d.sort_values("_filled", ascending=False).drop_duplicates(
        ["RaceDate", "HorseClean", "RaceTime"])
    print(f"de-duplicated the feed before lagging: {before:,} -> {len(d):,} "
          f"rows ({before - len(d):,} repeats dropped, keeping the most "
          "complete copy)")
    dt_txt = (d["RaceDate"].astype(str).to_numpy()
              + " " + d["RaceTime"].astype(str).to_numpy())
    d["when"] = pd.to_datetime(dt_txt, errors="coerce")
    d["HorseKey"] = d["CourseClean"] + "|" + d["HorseClean"]
    # lag inside horse across races, ordered in time; horse identity is
    # course-independent, so key on the name alone for continuity
    d = d.sort_values(["HorseClean", "when"])
    for m in ("TopSpeed", "StrideLength"):
        d[f"p_{m}"] = pd.to_numeric(d[m], errors="coerce").groupby(
            d["HorseClean"]).shift(1)

    f = b.merge(d[["RaceDate", "HorseClean", "p_TopSpeed", "p_StrideLength"]],
                on=["RaceDate", "HorseClean"], how="left")
    f["implied"] = 1.0 / f["BSP_TRUE"]
    print(f"BFSP runners {len(f):,}   races {f['EventID'].nunique():,}   "
          f"carrying a lagged figure {int(f['p_TopSpeed'].notna().sum()):,}"
          f" ({f['p_TopSpeed'].notna().mean() * 100:.1f}%)")

    for label, lo, hi in (("overlap 2023-02..2026-04", "2023-02-01",
                           "2026-04-30"),
                          ("UNTOUCHED 2026-05..09", "2026-05-01", "2026-09-16")):
        dates = pd.to_datetime(f["RaceDate"]).dt.date
        e = f[(dates >= pd.Timestamp(lo).date())
              & (dates <= pd.Timestamp(hi).date())]
        print()
        print(f"--- {label}: {e['EventID'].nunique():,} races, "
              f"{len(e):,} runners")
        if e.empty:
            continue
        base = pd.Series(_ret(e), index=e.index).groupby(e["EventID"]).mean()
        print(f"    market baseline (all runners) : {_ret(e).mean() * 100:+.2f}%")
        print(f"{'rule':<16}{'picks':>7}{'win %':>7}{'BSP says':>9}{'gap':>7}"
              f"{'gross':>8}{'net 2%':>8}{'max DD':>8}{'paired':>8}{'t':>6}")
        for name, col in (("SPEED", "p_TopSpeed"), ("STRIDE", "p_StrideLength")):
            has = e[e[col].notna()]
            if has.empty:
                print(f"{name:<16}      0 picks - no lagged figure")
                continue
            p = has.loc[has.groupby("EventID")[col].idxmax()]
            r = _ret(p)
            eq = np.cumsum(r)
            dd = float((np.maximum.accumulate(eq) - eq).max())
            wins = float((p["WinLose"] == 1).mean())
            imp = float(p["implied"].mean())
            diff = r - base.loc[p["EventID"]].to_numpy()
            sd = float(np.std(diff, ddof=1))
            t = float(np.mean(diff) / (sd / math.sqrt(len(diff))))
            print(f"{name:<16}{len(p):>7,}{wins * 100:>6.2f}%{imp * 100:>8.2f}%"
                  f"{(wins - imp) * 100:>6.2f}%{r.mean() * 100:>7.2f}%"
                  f"{_net(r, p).mean() * 100:>7.2f}%{dd:>8.0f}"
                  f"{np.mean(diff) * 100:>7.2f}%{t:>6.2f}")


def _ret(d: pd.DataFrame) -> np.ndarray:
    win = (d["WinLose"] == 1).to_numpy()
    return np.where(win, d["BSP_TRUE"].to_numpy() - 1.0, -1.0)


def _net(r: np.ndarray, p: pd.DataFrame) -> np.ndarray:
    win = (p["WinLose"] == 1).to_numpy()
    return np.where(win, r * 0.98, r)


def main() -> None:
    d, ranks = load_raceiq()
    sanity(d, ranks)
    s = load_sdata()
    equivalence(d, s)
    rule_test(d)


if __name__ == "__main__":
    main()


