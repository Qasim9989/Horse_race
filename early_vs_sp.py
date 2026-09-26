"""EARLY vs SP VERIFICATION
=========================
Replays each logged bet against the *captured morning snapshot* and the actual
starting price, then reports both ROIs side by side.

Because the snapshot records the best price **and** the each-way terms of the
same bookmaker, the each-way numbers here cannot go wrong the way the original
ledger did (which mixed a 1/4 place fraction with a 4-place flag that no
bookmaker offered).

    python early_vs_sp.py 2026-09-19
    python early_vs_sp.py 2026-09-19 --label 1035 --snapshot-dir snapshots
    python early_vs_sp.py --out report.json

Writes <out> (default early_vs_sp_<date>.json) and prints a per-bet table plus
totals.  Rows that cannot be verified are listed separately rather than being
silently priced from stale data.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WIN_STAKE = 1.0
EW_STAKE = 2.0

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def fmt(value):
    """Signed 2dp for numbers, '-' otherwise."""
    return f"{value:+.2f}" if isinstance(value, (int, float)) else "-"


def ascii_only(value, limit=None):
    """Strip emoji/non-ASCII so console output never dies on cp1252."""
    text = "".join(ch if ord(ch) < 128 else "" for ch in str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit] if limit else text


def norm_name(value) -> str:
    """Lowercase, drop country suffix and punctuation - strict, exact keys only."""
    s = str(value or "").strip().lower()
    s = re.sub(r"\s*\([a-z]{2,4}\)\s*$", "", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_course(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def to_hhmm(value) -> str:
    s = re.sub(r"[^0-9]", "", str(value or ""))
    return s.zfill(4) if len(s) <= 4 else s[:4]


def parse_sp(value):
    """Fractional ('5/1', '11/4F') or decimal SP text -> decimal float."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s in ("-", "none", "nan"):
        return None
    if s in ("evens", "evs", "evensf", "evsf", "1/1"):
        return 2.0
    m = re.match(r"^(\d+)\s*/\s*(\d+)", s)
    if m:
        try:
            return round(1.0 + float(m.group(1)) / float(m.group(2)), 2)
        except ZeroDivisionError:
            return None
    m = re.match(r"^(\d+(?:\.\d+)?)", s)
    if m:
        try:
            val = float(m.group(1))
            return val if val > 1.0 else None
        except ValueError:
            return None
    return None


def parse_pos(value):
    """'1st' / '2' / 'NR' -> int position, or None for non-numeric."""
    s = re.sub(r"[^0-9]", "", str(value or ""))
    return int(s) if s else None


def load_snapshot(snapshot_dir, date_str, label=None):
    """Flatten every matching snapshot: {(course, hhmm, horse): entry}."""
    if not os.path.isdir(snapshot_dir):
        return {}, []
    files = sorted(f for f in os.listdir(snapshot_dir)
                   if f.startswith(f"odds_{date_str}_") and f.endswith(".json"))
    if label:
        files = [f for f in files if f[5:-5].endswith(label)] or files
    entries: dict[tuple, dict] = {}
    loaded = []
    for name in files:
        try:
            with open(os.path.join(snapshot_dir, name), encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            continue
        loaded.append(name)
        for race in payload.get("races", []):
            course = norm_course(race.get("course") or race.get("course_slug"))
            hhmm = to_hhmm(race.get("hhmm") or race.get("time"))
            for run in race.get("runners", []):
                key = (course, hhmm, norm_name(run.get("horse")))
                # earliest capture wins
                entries.setdefault(key, dict(run, race_title=race.get("title"),
                                             captured_at=payload.get("captured_at")))
    return entries, loaded


def load_ledger(path, date_str):
    with open(path, encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("race_date") == date_str]
    return rows


def load_results(db_path, date_str):
    """{(horse): {'pos': int|None, 'sp': float|None, 'raw_pos': str}} from SQLite."""
    if not os.path.exists(db_path):
        return {}
    out = {}
    try:
        con = sqlite3.connect(db_path)
        cur = con.execute(
            "SELECT horse_name, finish_pos, sp_odds FROM race_results WHERE race_date = ?",
            (date_str,),
        )
        for horse, pos, sp in cur.fetchall():
            key = norm_name(horse)
            if key and key not in out:
                out[key] = {"pos": parse_pos(pos), "raw_pos": str(pos),
                            "sp": parse_sp(sp), "sp_text": str(sp)}
        con.close()
    except sqlite3.Error:
        return {}
    return out


def place_odds_from_terms(win_price, denominator):
    """Place price implied by a bookmaker's own terms."""
    try:
        win_price, denominator = float(win_price), float(denominator)
    except (TypeError, ValueError):
        return None
    if win_price <= 1.0 or denominator <= 1.0:
        return None
    return round(1.0 + (win_price - 1.0) / denominator, 2)


def pnl_win(price, won):
    if price is None:
        return None
    return round(price - 1.0, 2) if won else -1.0


def pnl_ew(price, place_price, won, placed):
    if price is None or place_price is None:
        return None
    if won:
        return round((price - 1.0) + (place_price - 1.0), 2)
    if placed:
        return round((place_price - 1.0) - 1.0, 2)
    return -2.0


def evaluate(ledger_rows, snapshots, results):
    """One record per logged bet, priced from the snapshot and settled from results."""
    out = []
    for r in ledger_rows:
        key = (norm_course(r.get("course")), to_hhmm(r.get("race_time")),
               norm_name(r.get("horse_name")))
        snap = snapshots.get(key)
        res = results.get(norm_name(r.get("horse_name")))
        rec = {
            "date": r.get("race_date"),
            "system": r.get("system_name"),
            "sub_system": r.get("sub_system"),
            "course": r.get("course"),
            "time": r.get("race_time"),
            "horse": r.get("horse_name"),
            "ledger_early": r.get("early_odds"),
            "ledger_sp": r.get("sp_odds"),
            "ledger_pos": r.get("finish_pos"),
            "ledger_places_paid": r.get("places_paid"),
            "ledger_early_ew_pl": r.get("early_ew_pl"),
            "ledger_sp_ew_pl": r.get("sp_ew_pl"),
        }
        flags = []
        if snap is None:
            flags.append("no_snapshot")
        if res is None:
            flags.append("no_result")
        if snap:
            rec.update({
                "early_price": snap.get("price"),
                "early_bookmaker": snap.get("bookmaker"),
                "places": snap.get("places"),
                "denominator": snap.get("denominator"),
                "early_place_price": snap.get("place_price"),
                "bf_win": snap.get("bf_win"),
                "bf_place": snap.get("bf_place"),
                "captured_at": snap.get("captured_at"),
            })
        pos = res["pos"] if res else parse_pos(r.get("finish_pos"))
        sp = (res.get("sp") if res else None) or parse_sp(r.get("sp_odds"))
        rec["finish_pos"] = pos
        rec["sp"] = sp
        rec["void"] = pos is None
        places = rec.get("places")
        won = pos == 1
        placed = bool(pos and places and pos <= int(places))
        rec["won"] = won
        rec["placed"] = placed
        if not rec["void"]:
            rec["early_win_pl"] = pnl_win(rec.get("early_price"), won)
            rec["early_ew_pl"] = pnl_ew(rec.get("early_price"), rec.get("early_place_price"),
                                        won, placed)
            rec["sp_win_pl"] = pnl_win(sp, won)
            rec["sp_place_price"] = place_odds_from_terms(sp, rec.get("denominator"))
            rec["sp_ew_pl"] = pnl_ew(sp, rec.get("sp_place_price"), won, placed)
        rec["status"] = ",".join(flags) if flags else "verified"
        out.append(rec)
    return out


def _sum(rows, field):
    vals = [r[field] for r in rows if r.get(field) is not None]
    return round(sum(vals), 2) if vals else None


def _roi(pnl, n, stake):
    return round(pnl / (n * stake) * 100, 2) if (pnl is not None and n) else None


def summarise(rows):
    verified = [r for r in rows if r["status"] == "verified" and not r["void"]]
    n = len(verified)
    voids = sum(1 for r in rows if r["void"])
    unverifiable = sum(1 for r in rows if r["status"] != "verified" and not r["void"])
    e_win, s_win = _sum(verified, "early_win_pl"), _sum(verified, "sp_win_pl")
    e_ew, s_ew = _sum(verified, "early_ew_pl"), _sum(verified, "sp_ew_pl")
    return {
        "bets_logged": len(rows),
        "bets_verified": n,
        "unverifiable": unverifiable,
        "voids": voids,
        "winners": sum(1 for r in verified if r["won"]),
        "placed": sum(1 for r in verified if r["placed"]),
        "win": {"staked": round(n * WIN_STAKE, 2), "early_pnl": e_win, "sp_pnl": s_win,
                "early_roi_pct": _roi(e_win, n, WIN_STAKE),
                "sp_roi_pct": _roi(s_win, n, WIN_STAKE)},
        "ew": {"staked": round(n * EW_STAKE, 2), "early_pnl": e_ew, "sp_pnl": s_ew,
               "early_roi_pct": _roi(e_ew, n, EW_STAKE),
               "sp_roi_pct": _roi(s_ew, n, EW_STAKE)},
    }


def by_system(rows):
    groups: dict[str, list] = {}
    for r in rows:
        if r["status"] != "verified" or r["void"]:
            continue
        groups.setdefault(r["system"], []).append(r)
    out = {}
    for name, g in sorted(groups.items()):
        n = len(g)
        e_ew, s_ew = _sum(g, "early_ew_pl"), _sum(g, "sp_ew_pl")
        out[name] = {"bets": n, "early_ew_pnl": e_ew, "sp_ew_pnl": s_ew,
                     "early_roi_pct": _roi(e_ew, n, EW_STAKE),
                     "sp_roi_pct": _roi(s_ew, n, EW_STAKE)}
    return out


def main():
    ap = argparse.ArgumentParser(description="Verify logged bets against the morning snapshot and SP.")
    ap.add_argument("date", nargs="?", default=dt.date.today().isoformat())
    ap.add_argument("--snapshot-dir", default=os.path.join(HERE, "snapshots"))
    ap.add_argument("--label", default=None, help="prefer a snapshot with this label, e.g. 1035")
    ap.add_argument("--ledger", default=os.path.join(HERE, "results_ledger.csv"))
    ap.add_argument("--db", default=os.path.join(HERE, "racing_form.db"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    date_str = args.date
    snapshots, files = load_snapshot(args.snapshot_dir, date_str, args.label)
    ledger_rows = load_ledger(args.ledger, date_str)
    results = load_results(args.db, date_str)

    print("=" * 98)
    print(f"  EARLY vs SP VERIFICATION - {date_str}")
    print("=" * 98)
    print(f"  snapshots used : {', '.join(files) if files else 'NONE FOUND'}")
    print(f"  snapshot runner entries : {len(snapshots)}")
    print(f"  ledger bets for the date: {len(ledger_rows)}")
    print(f"  results loaded          : {len(results)}")

    rows = evaluate(ledger_rows, snapshots, results)
    summary = summarise(rows)
    systems = by_system(rows)

    print("-" * 98)
    print(f"  {'horse':19s} {'system':15s} {'pos':>4s} {'early':>7s} {'book':>13s} "
          f"{'pl':>4s} {'SP':>7s}  {'eWIN':>7s} {'eEW':>7s} {'sEW':>7s}  status")
    for r in rows:
        pos = "-" if r["void"] else str(r.get("finish_pos"))
        early = f"{r['early_price']:.2f}" if r.get("early_price") else "-"
        sp = f"{r['sp']:.2f}" if r.get("sp") else "-"
        pl = str(r.get("places")) if r.get("places") else "-"
        print(f"  {ascii_only(r['horse'], 19):19s} {ascii_only(r['system'], 15):15s} {pos:>4s} "
              f"{early:>7s} {ascii_only(r.get('early_bookmaker'), 13):>13s} {pl:>4s} {sp:>7s}  "
              f"{fmt(r.get('early_win_pl')):>7s} {fmt(r.get('early_ew_pl')):>7s} "
              f"{fmt(r.get('sp_ew_pl')):>7s}  {r['status']}")

    print("-" * 98)
    w, e = summary["win"], summary["ew"]
    print(f"  VERIFIED BETS: {summary['bets_verified']} of {summary['bets_logged']} logged "
          f"({summary['unverifiable']} unverifiable, {summary['voids']} void) | "
          f"winners {summary['winners']}, placed {summary['placed']}")
    print(f"  Win-only (GBP1/bet, {w['staked']:.0f} staked)")
    print(f"     early price : P&L {w['early_pnl']:+.2f}   ROI {w['early_roi_pct']:+.2f}%")
    print(f"     SP          : P&L {w['sp_pnl']:+.2f}   ROI {w['sp_roi_pct']:+.2f}%")
    print(f"  Each-Way (GBP2/bet, {e['staked']:.0f} staked)")
    print(f"     early price : P&L {e['early_pnl']:+.2f}   ROI {e['early_roi_pct']:+.2f}%")
    print(f"     SP          : P&L {e['sp_pnl']:+.2f}   ROI {e['sp_roi_pct']:+.2f}%")

    if systems:
        print("-" * 98)
        print(f"  BY SYSTEM (each-way)   {'bets':>4s} {'early P&L':>10s} {'early ROI':>10s} {'SP P&L':>9s} {'SP ROI':>8s}")
        for name, s in systems.items():
            print(f"    {ascii_only(name, 20):20s} {s['bets']:>4d} {s['early_ew_pnl']:>10.2f} "
                  f"{s['early_roi_pct']:>9.2f}% {s['sp_ew_pnl']:>9.2f} {s['sp_roi_pct']:>7.2f}%")

    unver = [r for r in rows if r["status"] != "verified"]
    if unver:
        print("-" * 98)
        print(f"  UNVERIFIABLE ROWS ({len(unver)}) - excluded from the ROI above")
        for r in unver:
            print(f"    {ascii_only(r['horse'], 20):20s} {ascii_only(r['course'], 14):14s} {r['time']} "
                  f"{ascii_only(r['sub_system'], 34):34s} ledger_pos={r['ledger_pos']} [{r['status']}]")

    ledger_ew = sum(float(r["early_ew_pl"]) for r in ledger_rows
                    if r.get("early_ew_pl") not in (None, "", "nan"))
    print("-" * 98)
    print("  LEDGER COMPARISON (each-way, early price, excludes void rows)")
    print(f"     as stored in results_ledger.csv : {ledger_ew:+.2f}")
    print(f"     verified from the snapshot      : {e['early_pnl']:+.2f}")
    print(f"     difference                      : {e['early_pnl'] - ledger_ew:+.2f}")

    payload = {"date": date_str, "snapshots": files, "summary": summary,
               "by_system": systems, "rows": rows}
    out_path = args.out or os.path.join(HERE, f"early_vs_sp_{date_str}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
    print("-" * 98)
    print(f"  wrote {out_path}")
    return 0 if summary["bets_verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
