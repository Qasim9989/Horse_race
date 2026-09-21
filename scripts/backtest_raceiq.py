"""
RACEIQ BACKTEST - the Speed & Stride rule settled on the telemetry WE scrape
===========================================================================
No Proform SData anywhere in this file.  Everything comes from:

  RACINGTV_2023_2026.dbo.Scraped_RaceIQ_v2   the scraped RaceIQ readings
                                             (TopSpeedMph, StrideM)
  RACINGTV_2023_2026.dbo.Scraped_Results     the field, finishing positions, SP
  PRODB.dbo.BFSP                             Betfair BSP

The rule (cloud_app/speed_stride_rule.py):

  SPEED   the horse with the fastest prior top speed, if >= 35.0 mph
  STRIDE  the horse with the longest prior stride,    if >= 6.80 m
  AGREE   one horse tops both, and that pick replaces the two

"Prior" means the horse's most recent RaceIQ reading from an EARLIER date - a horse's
own reading in the race being priced is never used, so there is no look-ahead.

Settlement: 1u win-only at SP and at Betfair BSP, net of 2% commission on winners.
A control row (the favourite of each race) is printed so the yardstick is visible.

    python scripts\\backtest_raceiq.py
    python scripts\\backtest_raceiq.py --from 2026-08-01
"""
from __future__ import annotations

import argparse
import re

import numpy as np
import pandas as pd
import pyodbc

SPEED_MIN_MPH = 35.0
STRIDE_MIN_M = 6.80
COMMISSION = 0.02
RTV = (r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;"
       r"Database=RACINGTV_2023_2026;Trusted_Connection=yes;")
PRO = (r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;"
       r"Database=PRODB;Trusted_Connection=yes;")
SUFFIX = re.compile(r"\((?:ire|gb|fr|usa|can|ger|ity|spa|aus|nz|jpn|hk|swe|den|nor|bel|hol)\)\s*$")


def norm(value) -> str:
    text = SUFFIX.sub("", str(value or "").strip())
    return re.sub(r"[^a-z0-9]", "", text.lower())


def parse_sp(value) -> float | None:
    text = str(value or "").strip().lower().replace("f", "").replace("j", "")
    if not text:
        return None
    if text in ("evs", "evens", "1/1"):
        return 2.0
    m = re.match(r"^(\d+)\s*/\s*(\d+)", text)
    if m:
        return round(1 + float(m.group(1)) / float(m.group(2)), 2)
    try:
        v = float(text)
        return v if v > 1 else None
    except ValueError:
        return None


def load_raceiq(date_from: str) -> pd.DataFrame:
    conn = pyodbc.connect(RTV)
    df = pd.read_sql(f"""
        SELECT CAST(RaceDate AS date) race_date, Venue, Horse, TopSpeedMph, StrideM
        FROM dbo.Scraped_RaceIQ_v2
        WHERE RaceDate >= '{date_from}'
    """, conn)
    conn.close()
    df["race_date"] = pd.to_datetime(df["race_date"])
    df["horse"] = df["Horse"].map(norm)
    df["venue"] = df["Venue"].map(norm)
    for c in ("TopSpeedMph", "StrideM"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[(df["horse"] != "") & df["TopSpeedMph"].between(25, 55) & df["StrideM"].between(5, 10)]
    return df.dropna(subset=["TopSpeedMph", "StrideM"])


def load_runners(date_from: str) -> pd.DataFrame:
    conn = pyodbc.connect(RTV)
    df = pd.read_sql(f"""
        SELECT CAST(RaceDate AS date) race_date, RaceTime, CourseName, HorseName, PosNo, SP
        FROM dbo.Scraped_Results
        WHERE RaceDate >= '{date_from}'
    """, conn)
    conn.close()
    df["race_date"] = pd.to_datetime(df["race_date"])
    df["horse"] = df["HorseName"].map(norm)
    df["venue"] = df["CourseName"].map(norm)
    df["time"] = df["RaceTime"].astype(str)
    df["pos"] = pd.to_numeric(df["PosNo"].astype(str).str.extract(r"^(\d+)")[0], errors="coerce")
    df["sp"] = df["SP"].map(parse_sp)
    return df[(df["horse"] != "") & df["venue"] != ""]


def attach_prior_reading(runners: pd.DataFrame, telemetry: pd.DataFrame) -> pd.DataFrame:
    """Each runner's most recent RaceIQ reading from an earlier date (no look-ahead)."""
    tele = telemetry.sort_values("race_date")[["horse", "race_date", "TopSpeedMph", "StrideM"]]
    tele = tele.rename(columns={"race_date": "reading_date"})
    out = runners.sort_values("race_date").copy()
    out = pd.merge_asof(out, tele, left_on="race_date", right_on="reading_date",
                        by="horse", direction="backward", allow_exact_matches=False)
    return out


def pick_runners(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the rule per race and label the one or two picks."""
    frame = frame[frame["TopSpeedMph"].notna() & frame["StrideM"].notna()].copy()
    keys = ["race_date", "venue", "time"]
    best_speed = frame.groupby(keys)["TopSpeedMph"].transform("max")
    best_stride = frame.groupby(keys)["StrideM"].transform("max")
    speed_horse = frame["TopSpeedMph"].eq(best_speed) & (best_speed >= SPEED_MIN_MPH)
    stride_horse = frame["StrideM"].eq(best_stride) & (best_stride >= STRIDE_MIN_M)

    frame["pick"] = ""
    frame.loc[stride_horse, "pick"] = "STRIDE"
    frame.loc[speed_horse, "pick"] = "SPEED"
    both = speed_horse & stride_horse
    frame.loc[both, "pick"] = "AGREE"

    # ties: keep the shortest price of those sharing the top reading
    frame = frame[frame["pick"] != ""]
    frame["_rank"] = frame.groupby(keys + ["pick"])["sp"].rank(method="first", na_option="bottom")
    return frame[frame["_rank"] == 1].drop(columns="_rank")


def report(label: str, g: pd.DataFrame, price_col: str) -> None:
    priced = g[g[price_col].between(1.01, 1000)]
    if len(priced) < 10:
        print(f"  {label:<22} {len(priced)} priced - too few")
        return
    won = (priced["pos"] == 1).astype(int)
    pl = np.where(won == 1, (priced[price_col] - 1) * (1 - COMMISSION), -1.0)
    roi = pl.sum() / len(priced) * 100
    implied = (1 / priced[price_col]).mean() * 100
    print(f"  {label:<22} bets {len(priced):>5}  win {won.mean() * 100:>5.1f}%  "
          f"implied {implied:>5.1f}%  avg {priced[price_col].mean():>6.2f}  "
          f"ROI {roi:>+7.2f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2023-03-01",
                    help="first date of the scrape to use (v2 telemetry starts 2023-03-01)")
    args = ap.parse_args()

    tele = load_raceiq(args.date_from)
    runners = load_runners(args.date_from)
    print(f"RaceIQ readings: {len(tele):,} rows, "
          f"{tele['race_date'].min():%Y-%m-%d} to {tele['race_date'].max():%Y-%m-%d}")
    print(f"result runners : {len(runners):,} rows, "
          f"{runners['race_date'].min():%Y-%m-%d} to {runners['race_date'].max():%Y-%m-%d}")

    merged = attach_prior_reading(runners, tele)
    with_prior = merged[merged["TopSpeedMph"].notna() & merged["StrideM"].notna()]
    print(f"runners with an earlier RaceIQ reading: {len(with_prior):,} "
          f"({len(with_prior) / max(len(merged), 1) * 100:.1f}%)")

    bfsp = pd.read_sql(f"""SELECT CAST(RaceDate AS date) race_date, CourseClean, HorseClean, BSP_TRUE
                           FROM dbo.BFSP WHERE RaceDate >= '{args.date_from}'""",
                       pyodbc.connect(PRO))
    bfsp["race_date"] = pd.to_datetime(bfsp["race_date"])
    bfsp["horse"] = bfsp["HorseClean"].map(norm)
    bfsp["venue"] = bfsp["CourseClean"].map(norm)
    bfsp["bsp"] = pd.to_numeric(bfsp["BSP_TRUE"], errors="coerce")
    bfsp = bfsp.drop_duplicates(["race_date", "venue", "horse"])

    picks = pick_runners(with_prior).merge(
        bfsp[["race_date", "venue", "horse", "bsp"]], on=["race_date", "venue", "horse"], how="left")

    print(f"\npicks produced: {len(picks):,}")
    print(picks["pick"].value_counts().to_string())

    print("\n=== RACEIQ BACKTEST - win-only, 1u, net 2% ===")
    for col, name in (("sp", "at SP"), ("bsp", "at Betfair BSP")):
        print(f"\n{name}:")
        report("every pick", picks, col)
        for cat in ("SPEED", "STRIDE", "AGREE"):
            report(cat, picks[picks["pick"] == cat], col)
        fav = with_prior.copy()
        fav["sp"] = fav["sp"]
        fav = fav.sort_values("sp").groupby(["race_date", "venue", "time"]).head(1)
        fav = fav.merge(bfsp[["race_date", "venue", "horse", "bsp"]],
                        on=["race_date", "venue", "horse"], how="left")
        report("control: favourite", fav, col)

    print("\nSPEED picks by month (at BSP):")
    sp = picks[picks["pick"] == "SPEED"]
    for month, g in sp.groupby(sp["race_date"].dt.to_period("M")):
        report(str(month), g, "bsp")


if __name__ == "__main__":
    main()
