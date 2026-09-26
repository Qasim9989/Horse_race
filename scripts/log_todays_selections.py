"""
LOG TODAY'S PICKS INTO THE LEDGER
=================================
`results_ledger.csv` is the file `settle_daily_results.py` settles (it writes
system_results_ledger in the cloud DB).  Nothing was writing it automatically,
so the picks shown on the tabs were never logged or settled - the Tips tab
showed 168 picks while the ledger held 3.

This closes that gap: it copies today's picks out of the cloud caches
(tips_today.json, speed_stride_today.json) into the ledger with the columns the
settlement engine expects.  Settlement is left to
`settle_daily_results.py --date <date>` (or pass --settle to chain it).

  * Tips            - UPSERT: rows already logged are kept (some arrive from
                      other sources), only missing horses are added.
  * Speed & Stride  - REPLACE for the day: the ledger previously held the old
                      pre-fix rule (one pick per race, no thresholds) while the
                      tab now shows the shared rule's picks.

Usage
    python scripts\\log_todays_selections.py                 # today's caches
    python scripts\\log_todays_selections.py --date 2026-09-19
    python scripts\\log_todays_selections.py --settle        # log, then settle
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOUD_DIR = os.path.join(PROJECT_DIR, "cloud_app")
LEDGER_CSV = os.path.join(CLOUD_DIR, "results_ledger.csv")

COLUMNS = [
    # NOTE: these must match results_ledger.csv exactly.  They used to be the old
    # names (date/off/horse/sp/pos), which made this script die with
    # KeyError: 'date' and meant today's picks were NEVER logged - so the Settle
    # button had nothing to settle and appeared to do nothing.
    "race_date", "system_name", "sub_system", "course", "race_time", "horse_name",
    "early_odds", "best_bookmaker", "sp_odds", "sp_text", "finish_pos", "won",
    "placed", "places_paid", "early_win_pl", "sp_win_pl", "early_ew_pl",
    "sp_ew_pl", "bf_odds", "early_place_odds", "bf_place_odds", "price_flag",
]

PENDING = "⏳ Running Today"

# Ledger sub-system labels for the Speed & Stride rule (as already stored).
SS_LABELS = {
    "AGREE (Speed + Stride)": "🎯 Dual Agree (Speed + Stride)",
    "SPEED System Pick": "🚀 Top Speed #1",
    "STRIDE System Pick": "📏 Top Stride #1",
}


def split_race(race_text: str) -> tuple[str, str]:
    """"13:02 Newbury" -> ("13:02", "Newbury")."""
    match = re.match(r"^\s*(\d{1,2}:\d{2})\s+(.*)$", str(race_text or ""))
    return (match.group(1), match.group(2).strip()) if match else ("", str(race_text or ""))


def money(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 1.0 else default


def place_price(text):
    """"1.89 (3 Pl)" -> 1.89"""
    match = re.match(r"^\s*([\d.]+)", str(text or ""))
    return money(match.group(1)) if match else None


def row_base(date, system_name, sub_system, race_text, horse, odds, bookie,
             bf_odds=None, bf_place=None, places_paid=3):
    off, course = split_race(race_text)
    return {
        "race_date": date,
        "system_name": system_name,
        "sub_system": sub_system,
        "course": course,
        "race_time": off,
        "horse_name": horse,
        "early_odds": money(odds),
        "best_bookmaker": bookie or "",
        "sp_odds": None, "sp_text": "-", "finish_pos": PENDING,
        "won": 0, "placed": 0, "places_paid": places_paid,
        "early_win_pl": None, "sp_win_pl": None,
        "early_ew_pl": None, "sp_ew_pl": None,
        "bf_odds": money(bf_odds),
        "early_place_odds": place_price(bf_place),
        "bf_place_odds": place_price(bf_place),
        "price_flag": "",
    }


def load_cache(name: str) -> tuple[str, list]:
    path = os.path.join(CLOUD_DIR, name)
    if not os.path.exists(path):
        return "", []
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return str(payload.get("date") or ""), (payload.get("rows") or payload.get("picks") or [])


def tips_rows(date: str) -> list:
    cache_date, picks = load_cache("tips_today.json")
    if cache_date != date:
        print(f"[WARN] tips_today.json is dated {cache_date}, not {date} - skipped")
        return []
    return [row_base(date, "Tips", pick.get("Category", "Tips"), pick.get("Race", ""),
                     pick.get("Horse", ""), pick.get("Decimal_Odds"), pick.get("Bookmaker"),
                     bf_odds=pick.get("BF_Odds"), bf_place=pick.get("BF_Place"),
                     places_paid=4 if str(pick.get("Extra_Places", "")).startswith("4") else 3)
            for pick in picks]


def speed_stride_rows(date: str) -> list:
    cache_date, picks = load_cache("speed_stride_today.json")
    if cache_date != date:
        print(f"[WARN] speed_stride_today.json is dated {cache_date}, not {date} - skipped")
        return []
    return [row_base(date, "Speed & Stride",
                     SS_LABELS.get(pick.get("Category", ""), pick.get("Category", "Speed & Stride")),
                     pick.get("Race", ""), pick.get("Horse", ""), pick.get("Odds"),
                     pick.get("Bookmaker"), bf_odds=pick.get("BF_Odds"), bf_place=pick.get("BF_Place"),
                     places_paid=4 if str(pick.get("Extra_Places", "")).startswith("4") else 3)
            for pick in picks]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--settle", action="store_true", help="run settlement afterwards")
    args = ap.parse_args()
    date = args.date

    if not os.path.exists(LEDGER_CSV):
        print(f"Error: {LEDGER_CSV} not found.")
        return 1

    ledger = pd.read_csv(LEDGER_CSV)
    day = ledger["race_date"].astype(str) == date
    print(f"Ledger before: {len(ledger)} rows ({int(day.sum())} for {date})")

    ss_new = speed_stride_rows(date)
    if ss_new:
        drop = day & (ledger["system_name"] == "Speed & Stride")
        ledger = ledger[~drop]
        print(f"Speed & Stride: replaced {int(drop.sum())} old rows with {len(ss_new)} from the cache")

    tips_all = tips_rows(date)
    tips_new = []
    if tips_all:
        existing = {(str(r.race_date), str(r.horse_name).strip().lower())
                    for r in ledger[ledger["system_name"] == "Tips"].itertuples()}
        tips_new = [r for r in tips_all
                    if (date, str(r["horse_name"]).strip().lower()) not in existing]
        print(f"Tips: adding {len(tips_new)} picks from the cache "
              f"({len(tips_all) - len(tips_new)} already logged)")

    ledger = pd.concat([ledger, pd.DataFrame(ss_new + tips_new, columns=COLUMNS)],
                       ignore_index=True)[COLUMNS]
    ledger.to_csv(LEDGER_CSV, index=False)

    day = ledger["race_date"].astype(str) == date
    print(f"Ledger after : {len(ledger)} rows ({int(day.sum())} for {date})")
    for system_name, count in ledger[day].groupby("system_name").size().items():
        print(f"    {system_name:<18} {count}")

    if args.settle:
        print("\n--- settling ---")
        subprocess.run([sys.executable, "-u",
                        os.path.join(PROJECT_DIR, "scripts", "settle_daily_results.py"),
                        "--date", date], cwd=PROJECT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())

