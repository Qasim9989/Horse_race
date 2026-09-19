"""BACKTEST the three AI sub-systems SEPARATELY
=============================================
1. Power Rank #1        - the rule in scripts/sync_results_ledger.py:218 (top power
                          score per race). Measured by backtest_power_rank.py.
2. Analyst Verdict Pick - the horse RacingTV's analyst names in CAPITALS in the
                          racecard verdict. No code existed for this; the rule is
                          reconstructed here from the verdict text.
3. Analyst Each-Way     - the horse named in an "each-way" sentence of the same
                          verdict ("looks a sporting each-way angle / danger").

Settlement uses the racecard's own starting_price and form_figure (finishing
position 1-9, 0 = unplaced 10th+), both served by the API for finished races.

    python backtest_ai_subsystems.py --days 90
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import re
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rtv_api


def parse_sp(value):
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
    return (int(s) if int(s) > 0 else 10) if s else None


def extract(verdict, names):
    """(verdict pick, each-way pick) from the analyst text."""
    v = str(verdict or "")
    caps = next((n for n in names if n and n.upper() in v), None)
    ew = None
    for sentence in re.split(r"(?<=[.!?])\s+", v):
        if re.search(r"each[-\s]?way", sentence, re.I):
            ew = next((n for n in names if n and n.lower() in sentence.lower()), None)
            if ew:
                break
    return caps, ew


def place_terms(field, handicap):
    if field >= 16 and handicap:
        return 0.25, 4
    if field >= 8:
        return 0.20, 3
    return 0.25, 2


def _num(value):
    """Coerce to float, mapping None/NaN/garbage to None."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def settle(rows):
    out = []
    for r in rows:
        sp, pos = _num(r.get("sp")), _num(r.get("pos"))
        if not sp or sp <= 1 or not pos:
            continue
        r = dict(r, sp=sp, pos=int(pos))
        frac, places = place_terms(int(_num(r.get("field")) or 0), bool(r.get("handicap")))
        place_odds = round(1 + (sp - 1) * frac, 2)
        won = int(pos) == 1
        r["won"] = won
        r["placed"] = int(pos) <= places
        r["win_pl"] = round(sp - 1, 2) if won else -1.0
        if won:
            r["ew_pl"] = round((sp - 1) + (place_odds - 1), 2)
        elif r["placed"]:
            r["ew_pl"] = round((place_odds - 1) - 1, 2)
        else:
            r["ew_pl"] = -2.0
        out.append(r)
    return out


def summarise(label, rows):
    if not rows:
        print(f"  {label:<24} no settled bets")
        return
    n = len(rows)
    wins = sum(1 for r in rows if r["won"])
    placed = sum(1 for r in rows if r["placed"])
    win_pl = sum(r["win_pl"] for r in rows)
    ew_pl = sum(r["ew_pl"] for r in rows)
    days = max(len({r["date"] for r in rows}), 1)
    print(f"  {label:<24} bets={n:>5,} {n / days:>5.1f}/day  win%={wins / n * 100:>5.1f} "
          f"plc%={placed / n * 100:>5.1f} avgSP={sum(r['sp'] for r in rows) / n:>6.2f}  "
          f"WIN {win_pl / n * 100:>+7.2f}%  EW {ew_pl / (n * 2) * 100:>+7.2f}%")


def collect(date_from, date_to):
    picks = []
    days = []
    d = date_from
    while d <= date_to:
        days.append(d)
        d += dt.timedelta(days=1)
    for i, day in enumerate(days, 1):
        day_str = day.isoformat()
        try:
            races = rtv_api.day_races(day_str)
        except Exception:
            continue
        for race in races:
            try:
                detail = rtv_api.race_detail(day_str, race["course_slug"], race["hhmm"])
            except Exception:
                continue
            info = detail.get("race") or {}
            verdict = info.get("analyst_verdict")
            runners = [x for x in detail.get("runners", [])
                       if not x.get("withdrawn") and not x.get("reserve")]
            if not runners:
                continue
            names = [x.get("horse_name") for x in runners]
            field = len(runners)
            handicap = "handicap" in str(info.get("title") or "").lower()
            by_name = {str(x.get("horse_name")).lower(): x for x in runners}

            def sp_of(runner):
                p = runner.get("starting_price") or {}
                return parse_sp(p.get("decimal") or p.get("fractional"))

            if verdict:
                caps, ew = extract(verdict, names)
                for rule, pick in (("Analyst Verdict Pick", caps), ("Analyst Each-Way", ew)):
                    runner = by_name.get(str(pick).lower()) if pick else None
                    if runner:
                        picks.append({
                            "date": day_str, "rule": rule,
                            "race": f"{race.get('time')} {race.get('course_name')}",
                            "horse": pick, "sp": sp_of(runner),
                            "pos": finish_pos(runner.get("form_figure")),
                            "field": field, "handicap": handicap,
                        })
            # same-day control: back the shortest-priced runner in the race
            priced = [(sp_of(x), x) for x in runners]
            priced = [(s, x) for s, x in priced if s]
            if priced:
                sp, runner = min(priced, key=lambda t: t[0])
                picks.append({
                    "date": day_str, "rule": "Control: favourite",
                    "race": f"{race.get('time')} {race.get('course_name')}",
                    "horse": runner.get("horse_name"), "sp": sp,
                    "pos": finish_pos(runner.get("form_figure")),
                    "field": field, "handicap": handicap,
                })
        if i % 10 == 0:
            print(f"    scanned {i}/{len(days)} days ({len(picks)} picks)", flush=True)
    return picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--out", default=os.path.join(HERE, "ai_subsystems_backtest.csv"))
    args = ap.parse_args()

    today = dt.date.today() - dt.timedelta(days=1)
    date_from = (dt.date.fromisoformat(args.date_from) if args.date_from
                 else today - dt.timedelta(days=args.days))
    print("=" * 116)
    print(f"  AI SUB-SYSTEMS, TESTED SEPARATELY   {date_from} -> {today}")
    print("=" * 116)
    started = time.time()
    picks = collect(date_from, today)
    print(f"  collected {len(picks)} picks in {time.time() - started:.0f}s")
    if not picks:
        print("  nothing collected")
        return 1
    df = pd.DataFrame(picks)
    df.to_csv(args.out, index=False, encoding="utf-8")
    print(f"  wrote {args.out}")
    print()
    for rule in ("Analyst Verdict Pick", "Analyst Each-Way", "Control: favourite"):
        summarise(rule, settle(df[df["rule"] == rule].to_dict("records")))
    print()
    print("  Power Rank #1 (measured separately, 2024-01 -> 2026-08, RP data):")
    print("      bets=34,461  35.7/day  win%=22.7  WIN -15.32%   EW -13.60%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

