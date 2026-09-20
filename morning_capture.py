"""MORNING ODDS CAPTURE  (laptop-independent snapshot)
=====================================================
Saves a timestamped snapshot of every runner's best bookmaker price - together
with THAT SAME bookmaker's each-way terms - plus Betfair win and place prices.

Why this exists
---------------
The ledger used to record whatever price the live scan happened to return,
which produced "early" prices no bookmaker was offering (e.g. Snooze Lane 23.0
when the market best was 13.0) and each-way place odds derived twice with
different fractions.  One timestamped snapshot, taken once, fixes both: every
settlement can be replayed against a price that genuinely existed at capture
time.

Designed to run with no laptop: pure standard library HTTPS (no browser, no API
key, no SQL Server).  Suitable for a GitHub Actions cron job.

    python morning_capture.py                      # today, label = HHMM now
    python morning_capture.py 2026-09-19
    python morning_capture.py 2026-09-19 --label 1035 --out snapshots
    python morning_capture.py --no-betfair --limit 3   # smoke test

Output: <out>/odds_<date>_<label>.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from types import ModuleType

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rtv_api

bf: ModuleType | None
try:
    import betfair_ew_service as _bf_service
    bf = _bf_service
except Exception as exc:  # pragma: no cover - optional dependency
    bf = None
    print(f"[WARN] Betfair service unavailable: {exc}")


def place_odds_from_terms(win_price, denominator):
    """Place price implied by a bookmaker's own terms: 1 + (win-1)/denominator."""
    try:
        win_price = float(win_price)
        denominator = float(denominator)
    except (TypeError, ValueError):
        return None
    if win_price <= 1.0 or denominator <= 1.0:
        return None
    return round(1.0 + (win_price - 1.0) / denominator, 2)


def capture_betfair(date_str):
    """{normalised horse: {win, place, terms}} from Betfair WIN + PLACE markets."""
    if bf is None or not bf.is_configured():
        print("[WARN] Betfair credentials not configured - skipping exchange prices.")
        return {}
    try:
        token = bf.login()
        catalogue = bf.fetch_today_catalogue(date_str, token)
    except Exception as exc:
        print(f"[WARN] Betfair login/catalogue failed: {str(exc)[:120]}")
        return {}

    win_mkts = [m for m in catalogue if m.get("description", {}).get("marketType") == "WIN"]
    place_mkts = [m for m in catalogue if m.get("description", {}).get("marketType") == "PLACE"]
    ids = [m["marketId"] for m in win_mkts] + [m["marketId"] for m in place_mkts]
    if not ids:
        print("[WARN] Betfair returned no WIN/PLACE markets for this date.")
        return {}
    print(f"  Betfair: fetching {len(ids)} market books (WIN + PLACE)...")
    try:
        books = bf.fetch_market_books(ids, token=token)
    except Exception as exc:
        print(f"[WARN] Betfair market books failed: {str(exc)[:120]}")
        return {}
    book_by_id = {b["marketId"]: b for b in books}

    out: dict[str, dict] = {}
    for m in win_mkts:
        book = book_by_id.get(m["marketId"])
        if not book:
            continue
        names = {str(r["selectionId"]): bf.normalize_name(r["runnerName"])
                 for r in m.get("runners", [])}
        for r in book.get("runners", []):
            name = names.get(str(r.get("selectionId")))
            if not name:
                continue
            ex = r.get("ex", {})
            lays, backs = ex.get("availableToLay", []), ex.get("availableToBack", [])
            rec = out.setdefault(name, {})
            if lays:
                rec["win_lay"] = round(float(lays[0]["price"]), 2)
            if backs:
                rec["win_back"] = round(float(backs[0]["price"]), 2)
            price = lays[0]["price"] if lays else (backs[0]["price"] if backs else None)
            if price:
                # kept for the settlement replay: lay preferred, back as fallback
                rec["win"] = round(float(price), 2)

    for m in place_mkts:
        book = book_by_id.get(m["marketId"])
        if not book:
            continue
        winners = book.get("numberOfWinners", 3)
        names = {str(r["selectionId"]): bf.normalize_name(r["runnerName"])
                 for r in m.get("runners", [])}
        for r in book.get("runners", []):
            name = names.get(str(r.get("selectionId")))
            if not name:
                continue
            ex = r.get("ex", {})
            backs, lays = ex.get("availableToBack", []), ex.get("availableToLay", [])
            rec = out.setdefault(name, {})
            if backs:
                rec["place_back"] = round(float(backs[0]["price"]), 2)
            if lays:
                rec["place_lay"] = round(float(lays[0]["price"]), 2)
            price = backs[0]["price"] if backs else (lays[0]["price"] if lays else None)
            if price:
                rec["place"] = round(float(price), 2)
                rec["terms"] = f"{winners} Pl"
    print(f"  Betfair: {len(out)} runners priced (win and/or place).")
    return out


def capture_races(date_str, limit=None):
    """All races for the date, with each runner's best price and matching EW terms."""
    races = rtv_api.day_races(date_str)
    if limit:
        races = races[:limit]
    print(f"  RacingTV: {len(races)} races to capture")
    out = []
    for idx, r in enumerate(races, 1):
        slug, hhmm = r.get("course_slug"), r.get("hhmm")
        tag = f"{r.get('time')} {r.get('course_name')}"
        try:
            detail = rtv_api.race_detail(date_str, slug, hhmm)
        except Exception as exc:
            print(f"    [{idx}] {tag}: race_detail failed ({str(exc)[:60]})")
            continue
        live = [x for x in rtv_api.runners_of(detail) if rtv_api.is_live(x)]
        if not live:
            print(f"    [{idx}] {tag}: no live runners")
            continue
        odds_map = {}
        try:
            odds_map, _books = rtv_api.runner_odds([x["runner_id"] for x in live])
        except Exception as exc:
            print(f"    [{idx}] {tag}: odds failed ({str(exc)[:60]})")
        rows = []
        priced = 0
        for run in live:
            quotes = [q for q in (odds_map.get(run["runner_id"]) or [])
                      if (q.get("decimal") or 0) > 1.0]
            entry = {
                "horse": run.get("horse_name"),
                "cloth": run.get("cloth_number"),
                "weight": run.get("weight"),
                "form": run.get("form"),
                "dslr": run.get("days_since_run"),
                "rating": run.get("timeform_rating"),
                "quotes": len(quotes),
            }
            if quotes:
                top = max(quotes, key=lambda q: q["decimal"])
                entry["price"] = round(float(top["decimal"]), 2)
                entry["bookmaker"] = top.get("bookmaker_name")
                entry["places"] = top.get("places")
                entry["denominator"] = top.get("denominator")
                entry["place_price"] = place_odds_from_terms(top["decimal"], top.get("denominator"))
                places = [q.get("places") for q in quotes if q.get("places")]
                entry["max_places"] = max(places) if places else None
                priced += 1
            rows.append(entry)
        out.append({
            "course": r.get("course_name"),
            "course_slug": slug,
            "hhmm": hhmm,
            "time": r.get("time"),
            "country": r.get("country"),
            "title": r.get("title"),
            "runners_total": len(rows),
            "runners_priced": priced,
            "runners": rows,
        })
        print(f"    [{idx}/{len(races)}] {tag}: {len(rows)} runners, {priced} priced")
    return out


def attach_betfair(races, bf_map):
    """Merge Betfair win/place prices into each runner by normalised name."""
    if not bf_map or bf is None:
        return 0
    matched = 0
    for race in races:
        for entry in race["runners"]:
            key = bf.normalize_name(entry.get("horse") or "")
            rec = bf_map.get(key)
            if not rec:
                continue
            if rec.get("win"):
                entry["bf_win"] = rec["win"]
            if rec.get("place"):
                entry["bf_place"] = rec["place"]
                entry["bf_terms"] = rec.get("terms")
            # both sides of each book, so the each-way edges can be rebuilt
            # offline (the scanner needs the LAY price, not the back price)
            for src, dst in (("win_lay", "bf_win_lay"), ("win_back", "bf_win_back"),
                             ("place_lay", "bf_place_lay"), ("place_back", "bf_place_back")):
                if rec.get(src):
                    entry[dst] = rec[src]
            matched += 1
    return matched


def prune_snapshots(out_dir, keep_days):
    """Delete odds_*.json older than keep_days so the repo cannot balloon."""
    if not keep_days or keep_days <= 0:
        return []
    cutoff = dt.date.today() - dt.timedelta(days=keep_days)
    removed = []
    for name in sorted(os.listdir(out_dir)):
        if not (name.startswith("odds_") and name.endswith(".json")):
            continue
        try:
            file_date = dt.date.fromisoformat(name[5:15])
        except ValueError:
            continue
        if file_date < cutoff:
            os.remove(os.path.join(out_dir, name))
            removed.append(name)
    return removed


def main():
    ap = argparse.ArgumentParser(description="Capture a timestamped odds snapshot.")
    ap.add_argument("date", nargs="?", default=dt.date.today().isoformat(),
                    help="YYYY-MM-DD (default: today)")
    ap.add_argument("--label", default=None, help="snapshot label (default: HHMM now)")
    ap.add_argument("--out", default="snapshots", help="output directory")
    ap.add_argument("--no-betfair", action="store_true", help="skip exchange prices")
    ap.add_argument("--limit", type=int, default=None, help="only the first N races")
    ap.add_argument("--prune-days", type=int, default=None,
                    help="delete snapshots older than N days")
    args = ap.parse_args()

    date_str = args.date
    label = args.label or dt.datetime.now().strftime("%H%M")
    out_dir = args.out if os.path.isabs(args.out) else os.path.join(os.getcwd(), args.out)
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 74)
    print(f"  MORNING ODDS CAPTURE - {date_str} (label {label})")
    print(f"  started {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} -> {out_dir}")
    print("=" * 74)

    started = time.time()
    races = capture_races(date_str, limit=args.limit)
    bf_map = {} if args.no_betfair else capture_betfair(date_str)
    matched = attach_betfair(races, bf_map)

    priced = sum(r["runners_priced"] for r in races)
    total = sum(r["runners_total"] for r in races)
    payload = {
        "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
        "date": date_str,
        "label": label,
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
    size_kb = os.path.getsize(path) / 1024
    print("-" * 74)
    print(f"  races={len(races)} runners={total} priced={priced} betfair_matched={matched}")
    print(f"  wrote {path} ({size_kb:.1f} KB) in {time.time() - started:.0f}s")
    removed = prune_snapshots(out_dir, args.prune_days)
    if removed:
        print(f"  pruned {len(removed)} old snapshot(s)")
    if priced == 0:
        print("[WARN] No prices captured - treat this snapshot as failed.")
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
