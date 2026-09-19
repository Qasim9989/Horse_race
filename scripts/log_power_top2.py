"""
LOG THE TOP-2 POWER PICKS (early prices) INTO THE LEDGER
=======================================================
Forward test of the "back the first two cards" idea, with the two things the
historical tests showed matter:

  * the EARLY bookmaker price, not SP (early prices ran 1.22x SP at odds 1-3 and
    1.20x at 3-6 in the snapshot data, but 0.72x at 51+), and
  * only TWO picks, because card #3 added volume without adding return.

Power_Score is the app's own formula (app.py:498):

    0.35*Best_TS + 0.35*Avg_TS3 + 0.30*Best_RPR - 0.15*Weight_lbs

Ratings are taken from the cloud DB (a copy of the Racing Post data) but ONLY
from plausible rows - Topspeed >= 40 and RPR >= 40.  Without that guard the
score mixes scales: 708 rows since 2026-09-01 carry impossible figures (e.g.
Topspeed 14, RPR 39 for a horse rated 58), and a mixed-scale score is one
plausible reason the old Power Rank #1 rule measured so badly.

Rows land in results_ledger.csv as system "AI Top-2 (early)", one per card, with
the bookmaker price, bookmaker and place terms, and are settled by
settle_daily_results.py like every other system.  The Daily Breakdown tab shows
them per day.

    python scripts\\log_power_top2.py --date 2026-09-19
    python scripts\\log_power_top2.py --settle
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sqlite3
import subprocess
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)
CLOUD_DIR = os.path.join(PROJECT_DIR, "cloud_app")
DB_PATH = os.path.join(CLOUD_DIR, "racing_form.db")
LEDGER_CSV = os.path.join(CLOUD_DIR, "results_ledger.csv")

sys.path.insert(0, CLOUD_DIR)
import rtv_api

SYSTEM_NAME = "AI Top-2 (early)"
MIN_RATING = 40.0          # RP Topspeed/RPR are on a ~40-130 scale
COLUMNS = ["race_date", "system_name", "sub_system", "course", "race_time", "horse_name",
           "early_odds", "best_bookmaker", "sp_odds", "sp_text", "finish_pos", "won",
           "placed", "places_paid", "early_win_pl", "sp_win_pl", "early_ew_pl",
           "sp_ew_pl", "bf_odds", "early_place_odds", "bf_place_odds"]
PENDING = "⏳ Running Today"


def weight_lbs(text: str) -> float | None:
    """"9-4" (st-lb) -> 130 lb."""
    match = re.match(r"^\s*(\d+)\s*-\s*(\d+)", str(text or ""))
    return int(match.group(1)) * 14 + int(match.group(2)) if match else None


def claim_lbs(text: str) -> int:
    match = re.search(r"\((\d+)\)", str(text or ""))
    return int(match.group(1)) if match else 0


def query_name(horse_name: str) -> str:
    """Racecards add a country suffix ("Dallas Star (IRE)"); the DB does not."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(horse_name or "")).strip()


def prior_ratings(cur, horse_name: str) -> tuple[float | None, float | None, float | None]:
    """(best TS, mean of last 3 TS, best RPR) from plausible prior rows only."""
    bare = query_name(horse_name)
    cur.execute(
        """
        SELECT topspeed, rpr FROM race_results
        WHERE horse_name = ? OR horse_name = ? OR horse_name LIKE ?
        ORDER BY race_date DESC LIMIT 12
        """,
        (bare, horse_name, f"{bare} (%"),
    )
    ts_values, rpr_values = [], []
    for topspeed, rpr in cur.fetchall():
        ts = pd.to_numeric(topspeed, errors="coerce")
        rp = pd.to_numeric(rpr, errors="coerce")
        if pd.notna(ts) and ts >= MIN_RATING:
            ts_values.append(float(ts))
        if pd.notna(rp) and rp >= MIN_RATING:
            rpr_values.append(float(rp))
    best_ts = max(ts_values) if ts_values else None
    avg_ts3 = sum(ts_values[:3]) / len(ts_values[:3]) if ts_values else None
    best_rpr = max(rpr_values) if rpr_values else None
    return best_ts, avg_ts3, best_rpr


def power_score(cur, horse_name: str, weight: float | None):
    """The app's Power_Score, or None when the inputs are not there."""
    best_ts, avg_ts3, best_rpr = prior_ratings(cur, horse_name)
    if best_ts is None or avg_ts3 is None or best_rpr is None or weight is None:
        return None
    return round(0.35 * best_ts + 0.35 * avg_ts3 + 0.30 * best_rpr - 0.15 * weight, 1)


def collect(race_date: str) -> list:
    """One row per card (#1 and #2 by Power_Score) for every race that day."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    rows = []
    races = rtv_api.day_races(race_date)
    print(f"  {len(races)} races to rank")
    for race in races:
        try:
            detail = rtv_api.race_detail(race_date, race["course_slug"], race["hhmm"])
        except Exception:
            continue
        runners = [r for r in rtv_api.runners_of(detail) if rtv_api.is_live(r)]
        if len(runners) < 2:
            continue
        try:
            odds_res, _ = rtv_api.runner_odds([r["runner_id"] for r in runners])
        except Exception:
            odds_res = {}

        scored = []
        for runner in runners:
            weight = weight_lbs(runner.get("weight"))
            if weight is not None:
                weight -= claim_lbs(runner.get("jockey"))
            power = power_score(cur, str(runner.get("horse_name", "")).strip(), weight)
            if power is None:
                continue
            scored.append((power, runner))
        if len(scored) < 2:
            continue
        scored.sort(key=lambda pair: pair[0], reverse=True)

        for rank, (_power, runner) in enumerate(scored[:2], start=1):
            quotes = [q for q in (odds_res.get(runner["runner_id"]) or [])
                      if q.get("decimal") and q["decimal"] > 1.0]
            if not quotes:
                continue
            best = max(quotes, key=lambda q: q["decimal"])
            best_price = float(best["decimal"])
            # Take the biggest PLACE offer while staying within 5% of the best win
            # price: 4 places @1/5 pays the same per place as 3 places @1/5, so the
            # only thing that matters is how many horses get paid.  Measured on
            # PRODB.dbo.BookOdds, a 4th-place finish is paid by some book in half
            # the cases - worth ~+0.09u per EW bet.
            generous = [q for q in quotes if int(q.get("places") or 0) >= 4
                        and float(q["decimal"]) >= best_price * 0.95]
            chosen = max(generous, key=lambda q: float(q["decimal"])) if generous else best
            reported = [int(q["places"]) for q in quotes if q.get("places")]
            places = max(3, int(chosen.get("places") or 0) or (max(reported) if reported else 3))
            rows.append({
                "race_date": race_date,
                "system_name": SYSTEM_NAME,
                "sub_system": f"{'🥇' if rank == 1 else '🥈'} Power #{rank} (early price)",
                "course": race.get("course_name", ""),
                "race_time": race.get("time", ""),
                "horse_name": runner.get("horse_name", ""),
                "early_odds": round(float(chosen["decimal"]), 2),
                "best_bookmaker": str(chosen.get("bookmaker_name", "")),
                "sp_odds": None, "sp_text": "-", "finish_pos": PENDING,
                "won": 0, "placed": 0, "places_paid": places,
                "early_win_pl": None, "sp_win_pl": None,
                "early_ew_pl": None, "sp_ew_pl": None,
                "bf_odds": None, "early_place_odds": None, "bf_place_odds": None,
            })
    conn.close()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--settle", action="store_true")
    args = ap.parse_args()

    print(f"Ranking {args.date} by Power_Score (top two per race, early book price)")
    new_rows = collect(args.date)
    if not new_rows:
        print("  nothing logged - no racecards or no rated runners yet")
        return 1
    print(f"  {len(new_rows)} card picks ({len(new_rows) / 2:.0f} races)")

    ledger = pd.read_csv(LEDGER_CSV)
    drop = (ledger["race_date"].astype(str) == args.date) & (ledger["system_name"] == SYSTEM_NAME)
    ledger = ledger[~drop]
    ledger = pd.concat([ledger, pd.DataFrame(new_rows, columns=COLUMNS)], ignore_index=True)[COLUMNS]
    ledger.to_csv(LEDGER_CSV, index=False)

    today = ledger[ledger["race_date"].astype(str) == args.date]
    print(f"  ledger now {len(ledger)} rows; {args.date}: "
          f"{int((today['system_name'] == SYSTEM_NAME).sum())} top-2 picks "
          f"({int(drop.sum())} replaced)")
    for system_name, count in today.groupby("system_name").size().items():
        print(f"    {system_name:<22} {count}")

    if args.settle:
        print("\n--- settling ---")
        subprocess.run([sys.executable, "-u",
                        os.path.join(HERE, "settle_daily_results.py"), "--date", args.date],
                       cwd=PROJECT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())

