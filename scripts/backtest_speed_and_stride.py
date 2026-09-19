"""
BACKTEST the Speed & Stride rule, category by category
======================================================
The rule itself lives in cloud_app/speed_stride_rule.py - the same code the
RacingTV tab, the cloud cache and the settlement ledger use.

For every day in the window this rebuilds the day's picks from the RaceIQ
telemetry held BEFORE that day (the most recent reading per metric, exactly as
the app looks it up) and settles each pick at the racecard's starting price, so
the three categories - and a favourite control - share one measure.

    python backtest_speed_and_stride.py --from 2026-08-20
    python backtest_speed_and_stride.py --days 30

Read the sample size before believing any figure: the RaceIQ v2 scraper only
holds clean top speeds from 2026-08-01, so a short window means few picks.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sqlite3
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PROJECT_DIR, "cloud_app"))
import rtv_api
import speed_stride_rule as ss_rule

DB_PATH = os.path.join(PROJECT_DIR, "cloud_app", "racing_form.db")

# Clean top speeds only exist from the v2 scraper's window, so a speed "prior"
# is only trusted from this date on.  Strides go much further back (the v1
# scraper read those correctly - its stride mean matches v2's to 0.02 m).
V2_SPEED_FROM = "2026-08-01"


def parse_sp(value):
    """Starting price -> decimal (same parser as backtest_ai_subsystems.py)."""
    s = str(value or "").strip().lower()
    if not s:
        return None
    if s in ("evens", "evs", "1/1"):
        return 2.0
    m = re.match(r"^(\d+)\s*/\s*(\d+)", s)
    if m:
        try:
            return round(1 + float(m.group(1)) / float(m.group(2)), 2)
        except ZeroDivisionError:
            return None
    try:
        v = float(s)
        return v if v > 1 else None
    except ValueError:
        return None


def finish_pos(fig):
    """form_figure -> finishing position (0 means unplaced, 10th or worse)."""
    s = re.sub(r"[^0-9]", "", str(fig or ""))
    if not s:
        return None
    return int(s) if int(s) > 0 else 10


def load_telemetry():
    """RaceIQ readings from the cloud DB, parser junk already discounted."""
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT race_date, LOWER(horse_name) AS h_clean, stride_length, top_speed "
        "FROM raceiq_telemetry",
        con,
    )
    con.close()
    df["h_clean"] = df["h_clean"].apply(ss_rule.norm_horse)
    df["speed"] = df["top_speed"].apply(ss_rule.usable_speed)
    df["stride"] = df["stride_length"].apply(ss_rule.usable_stride)
    return df.sort_values("race_date")

def prior_readings(telemetry, day_str):
    """{horse: (speed, stride)} from the latest reading strictly before the day."""
    prior = telemetry[telemetry["race_date"] < day_str]
    out: dict = {}
    for index, metric in enumerate(("speed", "stride")):
        rows = prior
        if metric == "speed":
            rows = prior[prior["race_date"] >= V2_SPEED_FROM]
        part = rows.dropna(subset=[metric]).groupby("h_clean")[metric].last()
        for horse, value in part.items():
            out.setdefault(horse, [None, None])[index] = value
    return {horse: tuple(values) for horse, values in out.items()}


def collect(date_from, date_to):
    telemetry = load_telemetry()
    picks = []
    days = []
    day = date_from
    while day <= date_to:
        days.append(day)
        day += dt.timedelta(days=1)

    for i, day in enumerate(days, 1):
        day_str = day.isoformat()
        try:
            races = rtv_api.day_races(day_str)
        except Exception:
            continue
        readings = prior_readings(telemetry, day_str)

        for race in races:
            try:
                detail = rtv_api.race_detail(day_str, race["course_slug"], race["hhmm"])
            except Exception:
                continue
            runners = [r for r in detail.get("runners", [])
                       if not r.get("withdrawn") and not r.get("reserve")]
            if not runners:
                continue

            def sp_of(runner):
                price = runner.get("starting_price") or {}
                return parse_sp(price.get("decimal") or price.get("fractional"))

            by_name = {ss_rule.norm_horse(r.get("horse_name")): r for r in runners}
            entries = []
            for runner in runners:
                key = ss_rule.norm_horse(runner.get("horse_name"))
                speed, stride = readings.get(key, (None, None))
                entries.append((runner.get("horse_name"), speed, stride))

            race_label = f"{race.get('time')} {race.get('course_name')}"
            for horse, category in ss_rule.evaluate(entries):
                runner = by_name.get(ss_rule.norm_horse(horse))
                if runner is None:
                    continue
                picks.append({
                    "date": day_str, "rule": category, "race": race_label,
                    "horse": runner.get("horse_name"), "sp": sp_of(runner),
                    "pos": finish_pos(runner.get("form_figure")),
                })

            priced = [(sp_of(r), r) for r in runners]
            priced = [(sp, r) for sp, r in priced if sp]
            if priced:
                sp, runner = min(priced, key=lambda pair: pair[0])
                picks.append({
                    "date": day_str, "rule": "Control: favourite", "race": race_label,
                    "horse": runner.get("horse_name"), "sp": sp,
                    "pos": finish_pos(runner.get("form_figure")),
                })

        if i % 5 == 0:
            print(f"    scanned {i}/{len(days)} days ({len(picks)} picks)", flush=True)
    return picks


def _num(value):
    """Coerce to float; None for missing/garbage (pandas turns None into NaN)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def settle(rows):
    """Win-only settlement at starting price -> (settled rows, unpriced count)."""
    out = []
    skipped = 0
    for row in rows:
        sp, pos = _num(row.get("sp")), _num(row.get("pos"))
        if not sp or sp <= 1 or not pos:
            skipped += 1          # no starting price or no finishing position yet
            continue
        row = dict(row, sp=sp, pos=int(pos), won=(int(pos) == 1))
        row["win_pl"] = round(sp - 1, 2) if row["won"] else -1.0
        out.append(row)
    return out, skipped


def summarise(label, rows):
    """Print the category summary and return it as a stats dict (or None)."""
    if not rows:
        print(f"  {label:<24} no settled bets")
        return None
    n = len(rows)
    wins = sum(1 for r in rows if r["won"])
    win_pl = sum(r["win_pl"] for r in rows)
    days = max(len({r["date"] for r in rows}), 1)
    stats = {
        "bets": n,
        "bets_per_day": round(n / days, 1),
        "win_pct": round(wins / n * 100, 1),
        "avg_sp": round(sum(r["sp"] for r in rows) / n, 2),
        "roi_pct": round(win_pl / n * 100, 2),
    }
    print(f"  {label:<24} bets={n:>5,} {n / days:>5.1f}/day  win%={stats['win_pct']:>5.1f}  "
          f"avgSP={stats['avg_sp']:>6.2f}  WIN {stats['roi_pct']:>+7.2f}%")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--out", default=os.path.join(HERE, "speed_stride_backtest.csv"))
    args = ap.parse_args()

    date_to = (dt.date.fromisoformat(args.date_to) if args.date_to
               else dt.date.today() - dt.timedelta(days=1))
    date_from = (dt.date.fromisoformat(args.date_from) if args.date_from
                 else date_to - dt.timedelta(days=args.days))

    print("=" * 116)
    print(f"  SPEED & STRIDE RULE, BY CATEGORY   {date_from} -> {date_to}")
    print(f"  rule: {ss_rule.SPEED_MIN_MPH:.1f}+ mph, {ss_rule.STRIDE_MIN_M:.2f}+ m stride")
    print("=" * 116)
    started = time.time()
    picks = collect(date_from, date_to)
    print(f"  collected {len(picks)} picks in {time.time() - started:.0f}s")
    if not picks:
        print("  nothing collected")
        return 1

    df = pd.DataFrame(picks)
    df.to_csv(args.out, index=False, encoding="utf-8")
    print(f"  wrote {args.out}")
    print()
    results = {}
    for rule in (*ss_rule.CATEGORIES, "Control: favourite"):
        rows, unpriced = settle(df[df["rule"] == rule].to_dict("records"))
        stats = summarise(rule, rows)
        if stats:
            stats["unpriced"] = unpriced
            results[rule] = stats
        if unpriced:
            print(f"    ^ {unpriced} {rule} pick(s) had no starting price / "
                  f"finishing position and were excluded")

    # Publish the measured figures so the app and the cloud cache label the
    # categories with numbers this run actually produced.
    claims = {
        "generated": dt.date.today().isoformat(),
        "window": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "measure": "win-only, settled at the racecard starting price",
        "speed_prior_from": V2_SPEED_FROM,
        "rule": {"speed_min_mph": ss_rule.SPEED_MIN_MPH,
                 "stride_min_m": ss_rule.STRIDE_MIN_M},
        "categories": {k: v for k, v in results.items() if k != "Control: favourite"},
        "control_favourite": results.get("Control: favourite"),
    }
    with open(ss_rule.CLAIMS_FILE, "w", encoding="utf-8") as handle:
        json.dump(claims, handle, indent=2)
    print(f"  wrote {ss_rule.CLAIMS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
