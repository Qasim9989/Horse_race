"""DROP TIPS  (weight-drop selections - the "Ben" system, cloud edition)
=======================================================================
Flags handicap runners (>=5 declared) running off a LOWER weight than the mark
they last won off, then attaches the price captured in the morning snapshot.

Everything it needs is reachable without the laptop:
  * race type, runner count, today's weight  -> the morning snapshot (plain HTTP)
  * past runs, weights, winning marks        -> racing_form.db in the repo
  * prices and each-way terms                -> the morning snapshot

    python drop_tips.py                       # today
    python drop_tips.py 2026-09-19 --out drop_tips_today.json

Output: {"date": ..., "picks": [ ... ]} ready for the Drop Tips tab.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
BIG_DROP_LB = -8          # "genuine" drop, mirroring the Tips tab threshold
MIN_RUNNERS = 5           # handicap minimum, mirroring bens_racecard.py
LTO_MAX_POS = 4           # last time out must have finished in the first four
MIN_DAYS = 5              # not a quick turnaround
MAX_DAYS = 90             # the form still counts as current


def base_name(value: Any) -> str:
    """Lowercase, drop the country suffix and punctuation - strict keys only."""
    s = str(value or "").strip().lower()
    s = re.sub(r"\s*\([a-z]{2,4}\)\s*$", "", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_lbs(value: Any):
    """'10-0' / '10st 0lb' / 140 / '140' -> pounds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower().replace("lb", "").replace("st", "-")
    m = re.match(r"^(\d+)\s*[- ]\s*(\d+)$", s)
    if m:
        return int(m.group(1)) * 14 + int(m.group(2))
    m = re.match(r"^(\d+(?:\.\d+)?)$", s)
    if m:
        try:
            return int(float(m.group(1)))
        except ValueError:
            return None
    return None


def parse_pos(value: Any):
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("", "nr", "non-runner", "nonrunner", "nan", "none", "-"):
        return None
    digits = re.sub(r"[^0-9]", "", s)
    return int(digits) if digits else None


def load_history(db_path):
    """{base_name: [(race_date, finish_pos, lbs), ...]} newest first."""
    if not os.path.exists(db_path):
        return {}
    hist: dict[str, list] = {}
    con = sqlite3.connect(db_path)
    for horse, rdate, fpos, wgt in con.execute(
        "SELECT horse_name, race_date, finish_pos, weight_lbs FROM race_results"
    ):
        key = base_name(horse)
        if not key:
            continue
        hist.setdefault(key, []).append((str(rdate or ""), str(fpos or ""), parse_lbs(wgt)))
    con.close()
    for runs in hist.values():
        runs.sort(key=lambda x: x[0], reverse=True)
    return hist


def load_snapshot(snapshot_dir, date_str):
    """Earliest snapshot for the date wins (the closest thing to the 10:35 price)."""
    if not os.path.isdir(snapshot_dir):
        return None, []
    files = sorted(f for f in os.listdir(snapshot_dir)
                   if f.startswith(f"odds_{date_str}_") and f.endswith(".json"))
    for name in files:
        try:
            with open(os.path.join(snapshot_dir, name), encoding="utf-8") as fh:
                return json.load(fh), files
        except (OSError, ValueError):
            continue
    return None, files


def build_picks(snapshot, history, date_str):
    """Every handicap runner (>=5 declared) below the mark it last won off."""
    picks = []
    handicap_races = considered = 0
    for race in snapshot.get("races", []):
        title = str(race.get("title") or "")
        if "handicap" not in title.lower():
            continue
        runners = race.get("runners") or []
        if len(runners) < MIN_RUNNERS:
            continue
        handicap_races += 1
        for run in runners:
            considered += 1
            horse = run.get("horse") or ""
            lbs = parse_lbs(run.get("weight"))
            if not lbs:
                continue
            runs = [r for r in history.get(base_name(horse), [])
                    if r[0] < date_str and r[2]]
            if not runs:
                continue
            last_win = next((r for r in runs if parse_pos(r[1]) == 1), None)
            if last_win is None:
                continue
            delta = lbs - last_win[2]
            if delta > 0:
                continue
            # Form discipline - without this the rule fires on ~40% of the field
            # (most handicappers run below their winning mark at some point).
            lto_pos = parse_pos(runs[0][1])
            if lto_pos is None or lto_pos > LTO_MAX_POS:
                continue
            dslr = run.get("dslr")
            try:
                dslr_val = float(dslr) if dslr not in (None, "") else None
            except (TypeError, ValueError):
                dslr_val = None
            if dslr_val is not None and not (MIN_DAYS <= dslr_val <= MAX_DAYS):
                continue
            if delta <= BIG_DROP_LB:
                category, angle = "big_drop", "Big Weight Drop"
            elif delta < 0:
                category, angle = "below_mark", "Below Win Mark"
            else:
                category, angle = "exact_mark", "Exact Last Win Mark"
            price = run.get("price")
            picks.append({
                "Race": f"{race.get('time')} {race.get('course')}",
                "Horse": horse,
                "Category": category,
                "Angle": f"{angle} ({delta:+d}lb vs last winning mark)",
                "Weight": run.get("weight"),
                "Weight_lbs": lbs,
                "Last_Win_Weight_lbs": last_win[2],
                "Last_Win_Date": last_win[0],
                "Last_Run_Weight_lbs": runs[0][2],
                "Last_Run_Date": runs[0][0],
                "Delta_lb": delta,
                "LTO_Pos": lto_pos,
                "LTO_Date": runs[0][0],
                "DSLR": int(dslr_val) if dslr_val is not None else None,
                "Form": run.get("form"),
                "Rating": run.get("rating"),
                "Runs_On_Record": len(runs),
                "Races_Ago": None,
                "Odds": f"{price:.2f}" if price else "-",
                "BF_Odds": f"{run['bf_win']:.2f}" if run.get("bf_win") else "-",
                "BF_Place": (f"{run['bf_place']:.2f} ({run.get('bf_terms', '?')})"
                             if run.get("bf_place") else "-"),
                "Bookmaker": run.get("bookmaker") or "-",
                "Extra_Places": f"{run.get('max_places')} Pl" if run.get("max_places") else "-",
                "Places": run.get("places"),
                "Denominator": run.get("denominator"),
                "Place_Price": run.get("place_price"),
                "course_slug": race.get("course_slug"),
                "hhmm": race.get("hhmm"),
                "raw_odds": price or 999.0,
            })
    return picks, handicap_races, considered


def main():
    ap = argparse.ArgumentParser(description="Build the Drop Tips cache from a captured snapshot.")
    ap.add_argument("date", nargs="?", default=dt.date.today().isoformat())
    ap.add_argument("--snapshot-dir", default=os.path.join(HERE, "snapshots"))
    ap.add_argument("--db", default=os.path.join(HERE, "racing_form.db"))
    ap.add_argument("--out", default=os.path.join(HERE, "drop_tips_today.json"))
    args = ap.parse_args()

    print("=" * 74)
    print(f"  DROP TIPS - {args.date}")
    print("=" * 74)
    snapshot, files = load_snapshot(args.snapshot_dir, args.date)
    if snapshot is None:
        print(f"[WARN] no snapshot for {args.date} in {args.snapshot_dir}")
        print("[WARN] run morning_capture.py first, then re-run this.")
        return 1
    print(f"  snapshot: {files[0] if files else '?'}")

    history = load_history(args.db)
    print(f"  history : {len(history)} horses on record from {os.path.basename(args.db)}")

    picks, handicap_races, considered = build_picks(snapshot, history, args.date)
    picks.sort(key=lambda p: (p["hhmm"], p["Race"], p["Horse"]))

    counts = {}
    for p in picks:
        counts[p["Category"]] = counts.get(p["Category"], 0) + 1
    payload = {
        "date": args.date,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": f"{files[0] if files else '?'} + {os.path.basename(args.db)}",
        "handicap_races": handicap_races,
        "runners_considered": considered,
        "picks": picks,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
    print("-" * 74)
    print(f"  handicap races (>= {MIN_RUNNERS} declared): {handicap_races}")
    print(f"  runners considered: {considered}")
    print(f"  selections: {len(picks)}  big_drop={counts.get('big_drop', 0)} "
          f"below_mark={counts.get('below_mark', 0)} exact_mark={counts.get('exact_mark', 0)}")
    for p in picks[:25]:
        angle = "".join(ch if ord(ch) < 128 else "" for ch in p["Angle"])
        print(f"    {p['Race']:<26.26s} {p['Horse']:<22.22s} {p['Weight']:>6s} "
              f"last-win {p['Last_Win_Weight_lbs']:>3d}  {p['Delta_lb']:+3d}lb  "
              f"@{p['Odds']:>7s}  {angle}")
    if len(picks) > 25:
        print(f"    ... and {len(picks) - 25} more")
    print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
