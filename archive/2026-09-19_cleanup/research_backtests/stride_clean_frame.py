r"""
CLEAN FRAME - A FIELD THAT IS ACTUALLY COMPLETE
===============================================
Every earlier stride result ran on a frame built as SData JOIN BFSP and then
filtered to runners with a previous run.  That frame loses the winner in 1,519
races and ~7-10% of every field (field implied probability 0.899-0.928 instead
of ~1.00), which inflates every level ROI - including the +12.89% headline.

This rebuilds it the other way round:

    BFSP (every priced runner)  LEFT JOIN  lagged SData metrics

so

  * the FIELD is complete - every runner the market priced, winner included,
    which makes the market baseline the honest one (~-2% at BSP, not +4%)
  * the PICKS are still restricted to runners carrying a previous-run stride
    figure, because that is the only thing a bettor could have known

and then prices the picks properly: gross, and net of Betfair commission.

    python scripts\stride_clean_frame.py
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pyodbc

PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=300;"
       "MultipleActiveResultSets=True;")
FROM, TO = "2021-01-01", "2026-09-15"
KEYS = ["RaceDate", "RaceTime", "CourseClean", "HorseClean"]
RULES = {"fastest prev run": "p_MPH_Finish",
         "longest prev stride": "p_SL_Finish"}
ERAS = {"2021-2023": ("2021-01-01", "2023-12-31"),
        "2024-2026.04": ("2024-01-01", "2026-04-30"),
        "2026.05-09 UNTOUCHED": ("2026-05-01", "2026-09-15"),
        "pooled": ("2021-01-01", "2026-09-15")}
COMMISSION = 0.02

SDATA_SQL = """
SELECT S.SD_RNo, S.SD_HNo, RH.RH_DateTime, CN.C_Name AS Course,
       H.H_Name_No_Anything AS HorseClean,
       S.[SL_Finish] AS cur_SL_Finish, S.[MPH_Finish] AS cur_MPH_Finish
FROM dbo.SData S
JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.SD_RNo
JOIN dbo.NEW_H  H  ON H.H_No  = S.SD_HNo
LEFT JOIN dbo.NEW_C CN ON CN.C_ID = RH.RH_CNo
"""

BFSP_SQL = f"""
SELECT RaceDate, RaceTime, CourseClean, HorseClean, BSP_TRUE, WinLose,
       MorningWAP, PPWAP, EventID
FROM dbo.BFSP
WHERE RaceDate >= '{FROM}' AND RaceDate <= '{TO}' AND BSP_TRUE > 1
"""


def keys(d: pd.DataFrame, horse: str) -> pd.DataFrame:
    d = d.copy()
    d["HorseClean"] = d[horse].str.lower().str.replace(
        r"[^a-z0-9]", "", regex=True)
    return d


def build() -> pd.DataFrame:
    """BFSP as the base - the field - with lagged stride metrics attached."""
    c = pyodbc.connect(PRO)
    s = pd.read_sql(SDATA_SQL, c)
    b = pd.read_sql(BFSP_SQL, c)
    c.close()
    print(f"SData rows pulled : {len(s):,}")
    print(f"BFSP runners      : {len(b):,}")

    s["HorseClean"] = s["HorseClean"].str.lower().str.replace(
        r"[^a-z0-9]", "", regex=True)
    s["CourseClean"] = s["Course"].str.lower().str.replace(
        r"[^a-z0-9]", "", regex=True)
    s["RaceDate"] = pd.to_datetime(s["RH_DateTime"]).dt.date
    s["RaceTime"] = pd.to_datetime(s["RH_DateTime"]).dt.time
    s["when"] = pd.to_datetime(s["RH_DateTime"])
    s = s.sort_values(["SD_HNo", "when"])
    for col in ("cur_SL_Finish", "cur_MPH_Finish"):
        short = col.replace("cur_", "p_")
        s[short] = s.groupby("SD_HNo")[col].shift(1)
    s["days_since"] = (s["when"] - s.groupby("SD_HNo")["when"].shift(1)
                       ).dt.days
    # a second stride row for the same horse and race is a data duplicate
    s = s.drop_duplicates(["RaceDate", "RaceTime", "CourseClean", "HorseClean"])
    keep = [*KEYS, "p_SL_Finish", "p_MPH_Finish", "days_since"]

    b = keys(b, "HorseClean")
    b["RaceDate"] = pd.to_datetime(b["RaceDate"]).dt.date
    f = b.merge(s[keep], on=KEYS, how="left")
    f["implied"] = 1.0 / f["BSP_TRUE"]
    print(f"clean frame rows  : {len(f):,}   races {f['EventID'].nunique():,}")
    return f


def ret(d: pd.DataFrame, commission: float = 0.0) -> np.ndarray:
    """Level-stake return at BSP, optionally net of commission on winnings."""
    win = (d["WinLose"] == 1).to_numpy()
    gross = np.where(win, d["BSP_TRUE"].to_numpy() - 1.0, -1.0)
    if commission:
        gross = np.where(win, gross * (1.0 - commission), gross)
    return gross


def picks(d: pd.DataFrame, col: str) -> pd.DataFrame:
    """Best runner per race among those that carry a previous-run figure."""
    has = d[d[col].notna()]
    return has.loc[has.groupby("EventID")[col].idxmax()]


def era(d: pd.DataFrame, lo: str, hi: str) -> pd.DataFrame:
    dates = pd.to_datetime(d["RaceDate"]).dt.date
    return d[(dates >= pd.Timestamp(lo).date())
             & (dates <= pd.Timestamp(hi).date())]


def integrity(f: pd.DataFrame) -> None:
    print()
    print("=" * 92)
    print("1. IS THE FIELD COMPLETE NOW?")
    print("=" * 92)
    per_race = f.groupby("EventID")["implied"].sum()
    has = f[f["p_MPH_Finish"].notna()]
    per_race_has = has.groupby("EventID")["implied"].sum()
    winners = f.groupby("EventID")["WinLose"].sum()
    print(f"races                          : {len(per_race):,}")
    print(f"runners                        : {len(f):,}")
    print(f"races without a winner         : {int((winners != 1).sum()):,}")
    print(f"field implied prob per race    : {per_race.mean():.4f}"
          "   (complete and efficient is ~1.00)")
    dates = pd.to_datetime(f["RaceDate"]).dt.date
    hd = pd.to_datetime(has["RaceDate"]).dt.date
    print(f"race dates in the frame        : {dates.min()} to {dates.max()}")
    print(f"race dates carrying a stride   : {hd.min()} to {hd.max()}"
          "   <- the feed's real edge")
    print(f"runners carrying a stride fig  : {len(has):,}"
          f"  ({len(has) / len(f) * 100:.1f}% of the field)")
    print(f"implied prob of just those     : {per_race_has.mean():.4f}"
          "   <- this is what the old frame measured")
    print()
    print("market baseline, backing every runner at BSP:")
    print(f"  all runners                  : {ret(f).mean() * 100:+.3f}%"
          f"   ({len(f):,} bets)")
    print(f"  only stride-figured runners  : {ret(has).mean() * 100:+.3f}%"
          f"   ({len(has):,} bets)")


def analyse(f: pd.DataFrame, label: str) -> None:
    print()
    print("=" * 92)
    print(f"2. THE PICKS AT BSP - {label}")
    print("=" * 92)
    print(f"{'rule':<21}{'picks':>7}{'win %':>7}{'BSP says':>9}{'gap':>7}"
          f"{'ROI gross':>11}{'net 2%':>8}{'net 5%':>8}{'max DD':>8}"
          f"{'streak':>7}{'paired':>8}{'t':>6}")
    print("-" * 92)
    field_ret = pd.Series(ret(f), index=f.index)
    base = field_ret.groupby(f["EventID"]).mean()
    if len(f) == 0:
        print("  (no runners in this window - nothing to price)")
        return
    for name, col in RULES.items():
        p = picks(f, col)
        if len(p) == 0:
            print(f"{name:<21}      0 picks - the stride feed has no "
                  "coverage in this window")
            continue
        gross = ret(p)
        net = ret(p, COMMISSION)
        eq = np.cumsum(net)
        dd = float((np.maximum.accumulate(eq) - eq).max())
        streak = cur = 0
        for x in net:
            cur = cur + 1 if x < 0 else 0
            streak = max(streak, cur)
        wins = float((p["WinLose"] == 1).mean())
        imp = float(p["implied"].mean())
        diff = gross - base.loc[p["EventID"]].to_numpy()
        sd = float(np.std(diff, ddof=1))
        t = float(np.mean(diff) / (sd / math.sqrt(len(diff))))
        print(f"{name:<21}{len(p):>7,}{wins * 100:>6.2f}%{imp * 100:>8.2f}%"
              f"{(wins - imp) * 100:>6.2f}%{gross.mean() * 100:>10.2f}%"
              f"{net.mean() * 100:>7.2f}%{ret(p, 0.05).mean() * 100:>7.2f}%"
              f"{dd:>8.0f}{streak:>7}{np.mean(diff) * 100:>7.2f}%{t:>6.2f}")
    print()
    print("(net 2% / 5% = Betfair commission taken off winning bets; the"
          " commission rate depends on your turnover)")
    print(f"(paired = pick return minus the same race's all-runner average, "
          f"{len(base):,} races)")


def main() -> None:
    f = build()
    integrity(f)
    for label, (lo, hi) in ERAS.items():
        analyse(era(f, lo, hi), label)
    print()
    print("=" * 92)
    print("3. READ THIS WAY")
    print("=" * 92)
    print("The baseline line in section 1 is the lie detector: if backing every")
    print("runner comes out near -2% the frame is honest, and then the pick ROI")
    print("above is the first absolute number in this project that can be trusted")
    print("at all. Gross positive but net-of-2%-commission negative means the")
    print("signal exists and is eaten by costs - which is a system you cannot")
    print("bet, and worth knowing before staking anything.")


if __name__ == "__main__":
    main()

