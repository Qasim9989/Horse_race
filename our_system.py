"""OUR SYSTEM  -  the five-condition handicap system, rebuilt on live racing data
================================================================================
This is Ben's system from scripts/bens_racecard.py, re-pointed at the data that
actually updates (RacingTV racecards + the racing results DB) instead of the
Proform database, whose cards stop on 2026-05-22.

A horse qualifies when ALL FIVE hold:
    1  MarkChange < 0        today's handicap mark is lower than last time out
    2  BelowLastWinMark      today's mark is below the mark it last won off
    3  BelowCareerMax        today's mark is below its career-best mark
    4  ProvenAtTrip          has finished in the top 3 over this trip before
    5  RanTop4LTO            finished in the top four last time out

A softer flag is also reported: dropped mark + below career max + proven at trip.

  * today's mark and weight  -> RacingTV racecard (official_rating / weight)
  * past runs, marks, trip   -> racing_form.db (race_results)
  * prices and EW terms      -> the captured morning snapshot, when present

    python our_system.py [YYYY-MM-DD]
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rtv_api

MIN_RUNNERS = 5          # handicap minimum, as in the original
TRIP_TOLERANCE_YDS = 110  # half a furlong - "proven at this trip"


def base_name(value: Any) -> str:
    """Lowercase, drop the country suffix and punctuation - strict keys only."""
    s = str(value or "").strip().lower()
    s = re.sub(r"\s*\([a-z]{2,4}\)\s*$", "", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_pos(value: Any):
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("", "nr", "non-runner", "nonrunner", "nan", "none", "-"):
        return None
    digits = re.sub(r"[^0-9]", "", s)
    return int(digits) if digits else None


def parse_mark(value: Any):
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return int(digits) if digits else None


def distance_yards(text: Any):
    """'7f 36y' / '1m2f' / '2m' / '1m 1f 100y' -> yards."""
    s = str(text or "").lower().replace(" ", "")
    if not s:
        return None
    yards = 0.0
    found = False
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)([mfy])", s):
        found = True
        v = float(value)
        yards += {"m": 1760.0, "f": 220.0, "y": 1.0}[unit] * v
    return round(yards) if found else None


def load_history(db_path):
    """{base_name: [ {date, pos, mark, yds}, ... ]} newest first."""
    if not os.path.exists(db_path):
        return {}
    hist: dict[str, list] = {}
    con = sqlite3.connect(db_path)
    for horse, rdate, fpos, mark, dist in con.execute(
        "SELECT horse_name, race_date, finish_pos, official_rating, distance FROM race_results"
    ):
        key = base_name(horse)
        if not key:
            continue
        hist.setdefault(key, []).append({
            "date": str(rdate or ""),
            "pos": parse_pos(fpos),
            "mark": parse_mark(mark),
            "yds": distance_yards(dist),
        })
    con.close()
    for runs in hist.values():
        runs.sort(key=lambda x: x["date"], reverse=True)
    return hist


def load_snapshot_prices(snapshot_dir, date_str):
    """{(course, hhmm, horse): runner entry} from the captured morning snapshot."""
    entries: dict[tuple, dict] = {}
    if not snapshot_dir or not os.path.isdir(snapshot_dir):
        return entries
    files = sorted(f for f in os.listdir(snapshot_dir)
                   if f.startswith(f"odds_{date_str}_") and f.endswith(".json"))
    for name in files:
        try:
            with open(os.path.join(snapshot_dir, name), encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            continue
        for race in payload.get("races", []):
            course = re.sub(r"[^a-z0-9]+", "", str(race.get("course") or "").lower())
            hhmm = re.sub(r"[^0-9]", "", str(race.get("hhmm") or race.get("time") or ""))
            for run in race.get("runners", []):
                entries.setdefault((course, hhmm, base_name(run.get("horse"))), run)
    return entries


def build(date_str=None, db_path=None, snapshot_dir=None):
    """Scan the day's handicaps and return (picks, stats)."""
    date_str = date_str or dt.date.today().isoformat()
    db_path = db_path or os.path.join(HERE, "racing_form.db")
    snapshot_dir = snapshot_dir or os.path.join(HERE, "snapshots")
    hist = load_history(db_path)
    prices = load_snapshot_prices(snapshot_dir, date_str)

    races = [r for r in rtv_api.day_races(date_str)
             if "handicap" in str(r.get("title") or "").lower()]
    picks, scanned, races_used, errors = [], 0, 0, 0
    for race in races:
        try:
            detail = rtv_api.race_detail(date_str, race["course_slug"], race["hhmm"])
        except Exception:
            errors += 1
            continue
        runners = [x for x in rtv_api.runners_of(detail) if rtv_api.is_live(x)]
        if len(runners) < MIN_RUNNERS:
            continue
        races_used += 1
        info = detail.get("race") or {}
        today_yds = distance_yards(info.get("distance_formatted") or info.get("distance"))
        course_key = re.sub(r"[^a-z0-9]+", "", str(race.get("course_name") or race.get("course_slug") or "").lower())
        hhmm = re.sub(r"[^0-9]", "", str(race.get("hhmm") or race.get("time") or ""))

        for run in runners:
            scanned += 1
            mark = parse_mark(run.get("official_rating"))
            if not mark:
                continue
            runs = [x for x in hist.get(base_name(run["horse_name"]), [])
                    if x["date"] < date_str and x["mark"]]
            if not runs:
                continue
            lto = runs[0]
            wins = [x for x in runs if x["pos"] == 1]
            last_win_mark = wins[0]["mark"] if wins else None
            career_max = max(x["mark"] for x in runs)
            trip_places = 0
            if today_yds:
                trip_places = sum(1 for x in runs if x["pos"] in (1, 2, 3) and x["yds"]
                                  and abs(x["yds"] - today_yds) <= TRIP_TOLERANCE_YDS)
            change = mark - lto["mark"]
            below_win = last_win_mark is not None and mark < last_win_mark
            below_max = mark < career_max
            proven = trip_places > 0
            lto_top4 = lto["pos"] in (1, 2, 3, 4)
            strict = change < 0 and below_win and below_max and proven and lto_top4
            soft = change < 0 and below_max and proven
            if not (strict or soft):
                continue

            fired = []
            if change < 0:
                fired.append(f"mark {change:+d}")
            if below_win:
                fired.append("below last winning mark")
            if below_max:
                fired.append("below career best")
            if proven:
                fired.append(f"{trip_places}x placed at trip")
            if lto_top4:
                fired.append(f"LTO {lto['pos']}")
            price = prices.get((course_key, hhmm, base_name(run["horse_name"])), {})
            picks.append({
                "Race": f"{race.get('time')} {race.get('course_name')}",
                "Horse": run.get("horse_name"),
                "System": "OUR SYSTEM" if strict else "soft",
                "Mark": mark,
                "LTO_Mark": lto["mark"],
                "Change": change,
                "LastWinMark": last_win_mark,
                "CareerMax": career_max,
                "TripPlaces": trip_places,
                "LTO": lto["pos"],
                "Weight": run.get("weight"),
                "Odds": f"{price['price']:.2f}" if price.get("price") else "-",
                "BF_Odds": f"{price['bf_win']:.2f}" if price.get("bf_win") else "-",
                "BF_Place": (f"{price['bf_place']:.2f} ({price.get('bf_terms', '?')})"
                             if price.get("bf_place") else "-"),
                "Bookmaker": price.get("bookmaker") or "-",
                "Extra_Places": f"{price.get('max_places')} Pl" if price.get("max_places") else "-",
                "Conditions": " | ".join(fired),
                "course_slug": race.get("course_slug"),
                "hhmm": hhmm,
                "raw_odds": price.get("price") or 999.0,
            })

    picks.sort(key=lambda p: (0 if p["System"] == "OUR SYSTEM" else 1, p["hhmm"], p["Race"]))
    stats = {
        "date": date_str,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "handicap_races": races_used,
        "runners_considered": scanned,
        "strict": sum(1 for p in picks if p["System"] == "OUR SYSTEM"),
        "soft": sum(1 for p in picks if p["System"] == "soft"),
        "errors": errors,
    }
    return picks, stats


try:  # optional - present in the app, absent in a bare CLI run
    import streamlit as _st

    _build_cached = _st.cache_data(ttl=600, show_spinner=False)(build)
except Exception:  # pragma: no cover
    _build_cached = build


def render(st, date_str):
    """Draw the OUR SYSTEM mini tab (called from the Tips view)."""
    import pandas as pd

    picks, stats = _build_cached(date_str)
    st.caption(
        "Our five conditions — dropped mark, below last winning mark, below career best, "
        "proven at the trip, top-four last time out — computed from the RacingTV cards and "
        "the racing results database (no Proform)."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🎯 OUR SYSTEM", stats["strict"])
    c2.metric("Soft (3 of 5)", stats["soft"])
    c3.metric("Handicaps scanned", stats["handicap_races"])
    c4.metric("Runners checked", stats["runners_considered"])

    if not picks:
        st.info("No qualifying handicappers found for this date.")
        return

    choice = st.radio(
        "Show", ["🎯 OUR SYSTEM only", "Soft picks too"],
        horizontal=True, key="our_system_filter",
    )
    rows = ([p for p in picks if p["System"] == "OUR SYSTEM"]
            if choice.startswith("🎯") else picks)
    if not rows:
        st.info("No full five-condition picks today — switch to 'Soft picks too' to see near misses.")
        return

    df = pd.DataFrame(rows)
    cols = [c for c in ["Race", "Horse", "Odds", "BF_Odds", "BF_Place", "Bookmaker",
                        "Extra_Places", "Weight", "Mark", "LTO_Mark", "Change",
                        "LastWinMark", "CareerMax", "TripPlaces", "LTO", "Conditions"]
            if c in df.columns]
    st.dataframe(df[cols], use_container_width=True, hide_index=True)
    st.caption(f"Scanned {stats['generated_at']} · {stats['handicap_races']} handicaps · "
               f"{stats['runners_considered']} runners"
               + (f" · {stats['errors']} card(s) failed" if stats.get("errors") else ""))


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    print("=" * 78)
    print(f"  OUR SYSTEM (five conditions, live data) - {date_str}")
    print("=" * 78)
    picks, stats = build(date_str)
    print(f"  handicaps scanned : {stats['handicap_races']}")
    print(f"  runners checked   : {stats['runners_considered']}")
    print(f"  OUR SYSTEM picks  : {stats['strict']}")
    print(f"  soft picks        : {stats['soft']}")
    if stats.get("errors"):
        print(f"  card errors       : {stats['errors']}")
    print("-" * 78)
    for p in picks:
        cond = "".join(ch if ord(ch) < 128 else "" for ch in p["Conditions"])
        print(f"  [{p['System']:<9}] {p['Race']:<26.26s} {str(p['Horse'])[:20]:<20s} "
              f"mark {p['Mark']:>3} (LTO {p['LTO_Mark']:>3}, {p['Change']:+d}) "
              f"win {p['LastWinMark']!s:>4s} max {p['CareerMax']:>3} trip {p['TripPlaces']} "
              f"@{p['Odds']:>7s}  {cond}")
    if not picks:
        print("  (nothing qualified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
