r"""
INTRADAY ODDS CAPTURE - morning, hourly, then the run-in to the off
===================================================================
One command, run often. It works out what is due *now* and captures only that,
so a timer every minute costs a handful of requests instead of the whole card.

Capture points, measured from each race's own off time:

    morning    the existing 09:35 UTC workflow (whole card)
    hourly     every hour on the hour, for races still to come
    T-15 T-10 T-5 T-4 T-3 T-2 T-1     per race, inside the last 15 minutes
    BSP        once the race is off, the Betfair starting price

Off times come from RacingTV's own start_iso, which carries a UTC offset
('2026-09-20T14:00:00+01:00'), so the arithmetic is absolute and behaves the
same on a UK laptop and on a UTC GitHub runner.

Idempotent: a point already on disk for those runners is skipped, so a 1-minute
timer can run beside a 5-minute one without duplicating captures.

    python intraday_capture.py                    # capture whatever is due now
    python intraday_capture.py --dry-run          # say what would be captured
    python intraday_capture.py --now 14:45        # test the logic at a set time
    python intraday_capture.py --points 15,10,5   # narrow the run-in
    python intraday_capture.py --bsp              # only missing starting prices
    python intraday_capture.py --hourly           # only the hourly point

Output: <out>/odds_<date>_<HHMM>_<POINT>.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rtv_api
import morning_capture as mc

DEFAULT_POINTS = (15, 10, 5, 4, 3, 2, 1)


def race_key(race):
    return f"{race.get('course_slug')}|{race.get('hhmm')}"


def race_tag(race):
    return f"{race.get('time')} {race.get('course_name')}"


def minutes_to_off(race, now):
    """Minutes until the off, or None if the race has no usable start time."""
    off = mc.parse_iso(race.get("start_iso"))
    if off is None:
        return None
    return (off - now).total_seconds() / 60.0


def due_now(races, now, points, bsp_after=1.5):
    """{point: [races]} - what each race is owed at this instant.

    A point is due when the race is within half a minute of it, so a timer that
    fires at 13:44:40 still serves the 14:00 race's T-15 (15.3m -> 15).
    """
    work: dict[str, list] = {}
    for race in races:
        mins = minutes_to_off(race, now)
        if mins is None:
            continue
        if mins >= 0:
            nearest = int(mins + 0.5)
            if nearest in points and abs(mins - nearest) <= 0.5:
                work.setdefault(f"T{nearest}", []).append(race)
        elif mins >= -bsp_after:
            work.setdefault("BSP", []).append(race)
    return work


def hourly_due(races, now):
    """Races still to come, when we are standing on the hour."""
    if now.minute != 0:
        return []
    return [r for r in races if (minutes_to_off(r, now) or -1) > 0]


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def already_done(out_dir, date_str, point, keys):
    """Has this point already been captured for every one of these races?"""
    if not keys:
        return True
    prefix = f"odds_{date_str}_"
    for name in sorted(os.listdir(out_dir)):
        if not (name.startswith(prefix) and name.endswith(".json")):
            continue
        payload = _load(os.path.join(out_dir, name))
        if not payload or payload.get("capture_point") != point:
            continue
        have = {f"{r.get('course_slug')}|{r.get('hhmm')}" for r in payload.get("races", [])}
        if keys.issubset(have):
            return True
    return False


def bsp_done(out_dir, date_str, wanted):
    """Already stored the starting price for these markets?"""
    want = {(m.get("event", {}).get("venue"), m.get("marketStartTime")) for m in wanted}
    prefix = f"odds_{date_str}_"
    for name in sorted(os.listdir(out_dir)):
        if not (name.startswith(prefix) and name.endswith(".json")):
            continue
        payload = _load(os.path.join(out_dir, name))
        if not payload or payload.get("scope") != "bsp":
            continue
        have = {(m.get("venue"), m.get("off_utc")) for m in payload.get("markets", [])}
        if want.issubset(have):
            return True
    return False


def write_snapshot(out_dir, date_str, label, races, point, now, window_races=None):
    """Capture these races (bookmakers + Betfair) and save one snapshot file.

    `window_races` are the scheduler's own race rows (they carry course_name and
    start_iso), needed to pick out the matching Betfair markets; `races` are the
    converted rows that go into the file.
    """
    bf_map = mc.capture_betfair(date_str, races=window_races or races)
    matched = mc.attach_betfair(races, bf_map)
    priced = sum(r["runners_priced"] for r in races)
    total = sum(r["runners_total"] for r in races)
    payload = {
        "captured_at": now.isoformat(timespec="seconds"),
        "date": date_str,
        "label": label,
        "scope": "intraday",
        "capture_point": point,
        "source": "api.racingtv.com",
        "betfair_included": bool(bf_map),
        "betfair_matched": matched,
        "race_count": len(races),
        "runner_count": total,
        "runner_count_priced": priced,
        "races": races,
    }
    path = os.path.join(out_dir, f"odds_{date_str}_{label}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  {point:<4} races={len(races)} runners={total} priced={priced}"
          f" bf_matched={matched} -> {os.path.basename(path)}")
    return 1 if priced else 0


def capture_bsp(date_str, wanted, out_dir, now):
    """Betfair starting prices for markets that have just gone off."""
    if mc.bf is None or not mc.bf.is_configured():
        print("  [WARN] no Betfair credentials - cannot capture BSP")
        return 0
    try:
        token = mc.bf.login()
        books = mc.bf.fetch_market_books([m["marketId"] for m in wanted],
                                        token=token, sp=True)
    except Exception as exc:
        print(f"  [WARN] BSP fetch failed: {str(exc)[:100]}")
        return 0
    by_id = {b["marketId"]: b for b in books}
    out = []
    for market in wanted:
        book = by_id.get(market["marketId"])
        if not book:
            continue
        names = {str(r["selectionId"]): mc.bf.normalize_name(r["runnerName"])
                 for r in market.get("runners", [])}
        rows = [{"key": names.get(str(run.get("selectionId"))),
                 "bsp": run.get("bsp"),
                 "last_traded": run.get("lastPriceTraded")}
                for run in book.get("runners", []) if run.get("status") == "ACTIVE"]
        if not any(r["bsp"] for r in rows):
            continue                      # SP not published for this race yet
        out.append({
            "market_type": market.get("description", {}).get("marketType"),
            "market_id": market.get("marketId"),
            "venue": market.get("event", {}).get("venue"),
            "off_utc": market.get("marketStartTime"),
            "places": book.get("numberOfWinners"),
            "runners": rows,
        })
    if not out:
        print("  [WARN] no starting price published yet for this window")
        return 0
    label = now.astimezone().strftime("%H%M") + "_BSP"
    payload = {
        "captured_at": now.isoformat(timespec="seconds"),
        "date": date_str,
        "label": label,
        "scope": "bsp",
        "capture_point": "BSP",
        "markets": out,
    }
    path = os.path.join(out_dir, f"odds_{date_str}_{label}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  BSP  markets={len(out)} -> {os.path.basename(path)}")
    return 1


def _point_order(point):
    if point == "HR":
        return -1
    if point == "BSP":
        return 99
    return int(point[1:])


def main():
    ap = argparse.ArgumentParser(description="Capture whatever is due right now.")
    ap.add_argument("date", nargs="?", default=dt.date.today().isoformat())
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "snapshots"))
    ap.add_argument("--points", default=",".join(str(p) for p in DEFAULT_POINTS))
    ap.add_argument("--bsp-window", type=float, default=1.5,
                    help="minutes after the off to still collect BSP (use 6 on a 5-min timer)")
    ap.add_argument("--dry-run", action="store_true", help="say what is due, capture nothing")
    ap.add_argument("--now", default=None, help="HH:MM to pretend it is (testing)")
    ap.add_argument("--hourly", action="store_true", help="only the hourly point")
    ap.add_argument("--bsp", action="store_true", help="only missing starting prices")
    ap.add_argument("--prune-days", type=int, default=90)
    args = ap.parse_args()

    points = tuple(int(p) for p in str(args.points).replace(" ", "").split(",") if p)
    out_dir = args.out if os.path.isabs(args.out) else os.path.join(os.getcwd(), args.out)
    os.makedirs(out_dir, exist_ok=True)

    now = dt.datetime.now().astimezone()
    if args.now:
        hh, _, mm = args.now.partition(":")
        now = now.replace(hour=int(hh), minute=int(mm or 0), second=0, microsecond=0)

    print("=" * 74)
    print(f"  INTRADAY CAPTURE - {args.date}   now {now.strftime('%H:%M:%S')}"
          f"   points {','.join('T' + str(p) for p in points)}")
    print("=" * 74)

    races = rtv_api.day_races(args.date)
    if not races:
        print("  no races listed for this date")
        return 0

    if args.bsp:
        plan = [("BSP", [r for r in races if (minutes_to_off(r, now) or 99) <= 0])]
    elif args.hourly:
        plan = [("HR", hourly_due(races, now))]
    else:
        work = due_now(races, now, points, bsp_after=args.bsp_window)
        hourly = hourly_due(races, now)          # on the hour, everything still to come
        if hourly:
            work["HR"] = hourly
        plan = [(p, work[p]) for p in sorted(work, key=_point_order)]
    if not plan:
        print("  nothing due at this minute - no capture")
        return 0

    catalogue = []
    if any(p == "BSP" for p, _ in plan) and mc.bf is not None and mc.bf.is_configured():
        try:
            catalogue = mc.bf.fetch_today_catalogue(args.date, mc.bf.login())
        except Exception as exc:
            print(f"  [WARN] Betfair catalogue failed: {str(exc)[:100]}")

    made = 0
    for point, group in plan:
        if not group:
            print(f"  {point}: nothing in the window")
            continue
        if point == "BSP":
            wanted = [m for m in catalogue if mc.market_in_window(m, group, tolerance_s=120)]
            if not wanted:
                print("  BSP: no Betfair market matched")
                continue
            if bsp_done(out_dir, args.date, wanted):
                print("  BSP: already captured for these markets")
                continue
            if args.dry_run:
                print(f"  would capture BSP for {len(wanted)} market(s): "
                      f"{', '.join(race_tag(r) for r in group)}")
                continue
            made += capture_bsp(args.date, wanted, out_dir, now)
            continue

        keys = {race_key(r) for r in group}
        if already_done(out_dir, args.date, point, keys):
            print(f"  {point}: already captured for those runners")
            continue
        if args.dry_run:
            for r in group:
                print(f"  would capture {point:<3} {race_tag(r)}"
                      f"   ({minutes_to_off(r, now):+.1f} min to the off)")
            continue
        rows = mc.capture_races(args.date, only_keys=keys)
        if not rows:
            print(f"  {point}: no race data returned")
            continue
        label = now.astimezone().strftime("%H%M") + "_" + point
        made += write_snapshot(out_dir, args.date, label, rows, point, now,
                               window_races=group)

    if not args.dry_run:
        removed = mc.prune_snapshots(out_dir, args.prune_days)
        if removed:
            print(f"  pruned {len(removed)} old snapshot(s)")
    print(f"  wrote {made} snapshot(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
