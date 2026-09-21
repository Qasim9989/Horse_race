"""
BEN'S MORNING EXTRA-PLACE SYSTEM (SHEET 1 REVERSE-ENGINEERED)
============================================================
Reverse-engineered from Ben's actual Google Sheet 1 ("BensBets - Morning Public"):
88 bets in September 2026, +105.84 pts profit, +50.46% ROI, Average Odds 17.02.

Strategy Mechanics:
  1. Race Type: UK & Irish Handicaps with 8+ runners where bookmakers offer Extra Places.
     - 8 to 15 runners: 4 Places @ 1/5 Odds (Standard only pays 3 places).
     - 16+ runners: 5 Places @ 1/5 Odds (Standard only pays 4 places).
  2. Handicap Mark Drop: Current mark <= LTO mark (drop of -1lb to -7lb, or level).
     (Drops > 7lb excluded as cross-code Jumps/Flat anomalies).
  3. Disregarded Prep Run: Horse finished 4th or worse last time out (or quiet prep).
     This uninspiring finish pushes the market odds into double digits.
  4. Odds Sweet Spot: Early morning price between 6.0 (5/1) and 34.0 (33/1).
     At 1/5 odds, a 4th-place finish on a 14/1 shot pays 2.8/1 clear profit on the place part.
  5. Selectivity: Ranks qualifying candidates by score (mark drop + peak rating competence)
     and returns strictly 3 to 5 selective Each-Way bets per day.
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

MIN_RUNNERS = 8  # Extra place handicaps require at least 8 runners


def base_name(value: Any) -> str:
    """Lowercase, strip country suffix and punctuation."""
    s = str(value or "").strip().lower()
    s = re.sub(r"\s*\([a-z]{2,4}\)\s*$", "", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_pos(value: Any) -> int | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("", "nr", "non-runner", "nonrunner", "nan", "none", "-"):
        return None
    digits = re.sub(r"[^0-9]", "", s)
    return int(digits) if digits else None


def parse_mark(value: Any) -> int | None:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return int(digits) if digits else None


def parse_decimal_odds(odds_val: Any) -> float:
    if not odds_val:
        return 0.0
    s = str(odds_val).strip()
    if "/" in s:
        parts = s.split("/")
        try:
            return round((float(parts[0]) / float(parts[1])) + 1.0, 2)
        except Exception:
            return 0.0
    try:
        return round(float(s), 2)
    except Exception:
        return 0.0


def distance_yards(text: Any) -> int | None:
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


def load_history(db_path: str) -> dict[str, list[dict[str, Any]]]:
    """Load horse career runs from racing_form.db in descending date order."""
    if not os.path.exists(db_path):
        return {}
    hist: dict[str, list[dict[str, Any]]] = {}
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "SELECT horse_name, race_date, finish_pos, official_rating, distance, topspeed, rpr FROM race_results"
        )
        for horse, rdate, fpos, mark, dist, ts, rpr in cur:
            key = base_name(horse)
            if not key:
                continue
            ts_int = int(ts) if ts and str(ts).isdigit() else 0
            rpr_int = int(rpr) if rpr and str(rpr).isdigit() else 0
            hist.setdefault(key, []).append({
                "date": str(rdate or ""),
                "pos": parse_pos(fpos),
                "mark": parse_mark(mark),
                "yds": distance_yards(dist),
                "ts": ts_int,
                "rpr": rpr_int,
            })
    finally:
        con.close()
    for runs in hist.values():
        runs.sort(key=lambda x: str(x["date"]), reverse=True)
    return hist


def load_snapshot_prices(snapshot_dir: str, date_str: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    """{(course_key, hhmm, horse_key): runner_data} from morning odds snapshot."""
    entries: dict[tuple[str, str, str], dict[str, Any]] = {}
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


def scan_extra_place_bets(
    date_str: str | None = None,
    db_path: str | None = None,
    snapshot_dir: str | None = None,
    max_picks: int = 5,
) -> list[dict[str, Any]]:
    """Scan today's races and return Ben's top 3 to 5 selective extra-place bets."""
    date_str = date_str or dt.date.today().isoformat()
    db_path = db_path or os.path.join(HERE, "racing_form.db")
    snapshot_dir = snapshot_dir or os.path.join(HERE, "snapshots")

    hist = load_history(db_path)
    prices = load_snapshot_prices(snapshot_dir, date_str)

    # Filter for handicaps only
    races = [r for r in rtv_api.day_races(date_str)
             if "handicap" in str(r.get("title") or "").lower()]

    candidates: list[dict[str, Any]] = []

    for race in races:
        try:
            detail = rtv_api.race_detail(date_str, race["course_slug"], race["hhmm"])
        except Exception:
            continue

        # Fetch live bookmaker odds for this race (keyed by integer runner_id)
        live_odds_map: dict[int, list[dict[str, Any]]] = {}
        try:
            r_ids = [x["runner_id"] for x in rtv_api.runners_of(detail) if "runner_id" in x]
            live_odds_map, _ = rtv_api.runner_odds(r_ids)
        except Exception:
            pass

        runners = [x for x in rtv_api.runners_of(detail) if rtv_api.is_live(x)]
        field_size = len(runners)
        if field_size < MIN_RUNNERS:
            continue

        # Extra Place Terms
        if field_size >= 16:
            extra_places = 5
            place_terms_str = "5 Places @ 1/5 Odds (Extra Place)"
        else:
            extra_places = 4
            place_terms_str = "4 Places @ 1/5 Odds (Extra Place)"

        course_name = str(race.get("course_name") or race.get("course_slug") or "")
        course_key = re.sub(r"[^a-z0-9]+", "", course_name.lower())
        hhmm = re.sub(r"[^0-9]", "", str(race.get("hhmm") or race.get("time") or ""))
        time_str = str(race.get("time") or "")

        for run in runners:
            horse_name = str(run.get("horse_name") or "").strip()
            h_key = base_name(horse_name)
            if not h_key:
                continue

            day_mark = parse_mark(run.get("official_rating"))
            if not day_mark:
                continue

            # Prior runs strictly before today
            runs = [x for x in hist.get(h_key, []) if str(x["date"]) < date_str and x["mark"]]
            if not runs:
                continue

            lto = runs[0]
            lto_mark = lto["mark"]
            if not lto_mark:
                continue

            mark_change = day_mark - lto_mark
            # Rule 1: Mark must be falling or level (0 to -7lb drop; >7lb is cross-code noise)
            if mark_change > 0 or mark_change < -7:
                continue

            # Rule 2: Unplaced LTO (4th or worse, or quiet prep run)
            # In Ben's Sheet 1, 73% finished 5th or worse
            lto_pos = lto["pos"]
            is_unplaced_lto = lto_pos is None or lto_pos >= 4

            # Check previous wins
            prev_wins = [x for x in runs if x["pos"] == 1]
            last_win_mark = prev_wins[0]["mark"] if prev_wins else None
            is_below_win = (last_win_mark is None) or (day_mark <= last_win_mark)

            # Rule 3: Odds sweet spot (6.0 to 34.0)
            # Try snapshot first, fall back to live RTV bookmaker feed
            price_entry = prices.get((course_key, hhmm, h_key), {})
            early_odds_str = str(price_entry.get("odds") or run.get("odds") or "")
            early_odds = parse_decimal_odds(early_odds_str)
            best_bookmaker = str(price_entry.get("bookmaker") or "")

            # If no snapshot odds, fetch live from RTV runner_odds
            if early_odds == 0.0:
                rid = run.get("runner_id")  # integer key
                live_quotes = live_odds_map.get(rid, [])
                v_quotes = [q for q in live_quotes if q.get("decimal") and float(q["decimal"]) > 1.0]
                if v_quotes:
                    best_q = max(v_quotes, key=lambda q: float(q["decimal"]))
                    early_odds = round(float(best_q["decimal"]), 2)
                    best_bookmaker = str(best_q.get("bookmaker_name") or "")

            # If odds are available and outside value band, filter
            if early_odds > 0 and (early_odds < 6.0 or early_odds > 40.0):
                continue

            # Peak rating ability
            peak_ts = max([int(x["ts"]) for x in runs if x.get("ts")] or [0])
            peak_rpr = max([int(x["rpr"]) for x in runs if x.get("rpr")] or [0])

            # Ben's Selection Score:
            # - Reward bigger mark drops (-2 to -7lb)
            # - Reward unplaced LTO (value odds)
            # - Reward proven class (peak RPR/TS)
            score = abs(mark_change) * 2.5 + (peak_rpr / 10.0)
            if is_unplaced_lto:
                score += 3.0
            if is_below_win:
                score += 2.0
            if early_odds >= 8.0:
                score += 2.0

            # Place return at 1/5 EW terms
            place_return = round(((early_odds - 1.0) / 5.0) + 1.0, 2) if early_odds > 1.0 else 0.0

            candidates.append({
                "race_time": time_str,
                "course": course_name,
                "horse": horse_name,
                "mark": day_mark,
                "lto_mark": lto_mark,
                "drop": mark_change,
                "lto_pos": lto_pos,
                "last_win_mark": last_win_mark if last_win_mark else "Maiden",
                "odds": early_odds if early_odds > 0 else 0.0,
                "odds_display": f"{early_odds:.1f}" if early_odds > 0 else "Not Available",
                "bookmaker": best_bookmaker if best_bookmaker else "Best Available",
                "place_return": place_return,
                "field_size": field_size,
                "extra_places": extra_places,
                "place_terms": place_terms_str,
                "peak_rpr": peak_rpr,
                "peak_ts": peak_ts,
                "score": round(score, 2),
                "bet_type": "Each-Way",
                "suggested_stake": "1.0 pt EW (2.0 pts total)",
            })

    # Sort descending by score and pick strictly top 3 to 5
    candidates.sort(key=lambda x: float(x["score"]), reverse=True)
    return candidates[:max_picks]


if __name__ == "__main__":
    target_date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    print(f"Scanning Ben's Extra-Place System for {target_date}...")
    picks = scan_extra_place_bets(target_date)
    print(f"Found {len(picks)} Top Extra-Place Selections:\n")
    for i, p in enumerate(picks, 1):
        print(f"{i}. {p['horse']} - {p['course']} {p['race_time']}")
        print(f"   Odds: {p['odds']} | Bet: {p['bet_type']} | {p['place_terms']}")
        print(f"   Mark: {p['mark']} (LTO: {p['lto_mark']}, Drop: {p['drop']:+d}lb) | LTO Pos: {p['lto_pos']} | Last Win OR: {p['last_win_mark']}")
        print(f"   Peak RPR: {p['peak_rpr']} | TS: {p['peak_ts']} | Score: {p['score']}\n")
