"""
PRICE WATCH - measure how often the accounts actually reprice
============================================================
Takes a bookmaker snapshot every N seconds for M minutes, then reports how
much moved between each pair of snapshots.  This is the only way to measure
the real repricing rate - the daily snapshots are too far apart.

  python scripts/price_watch.py 20            # 20 minutes, every 60s
  python scripts/price_watch.py 60 --every 30
  python scripts/price_watch.py 0.5 --every 20 --quiet

Best used in the 30 minutes before a race, when books are moving hardest.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from typing import Any

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import itertools

import book_odds as bo

KEYS = ["RaceDate", "CourseClean", "RaceTime", "HorseClean", "BookmakerName"]


def compare(day, first, last):
    conn = bo.pyodbc.connect(bo.CONN)
    d = pd.read_sql("SELECT * FROM dbo.BookOdds WHERE RaceDate = ? AND "
                    "SnapshotAt >= ? AND SnapshotAt <= ?",
                    conn, params=[day, first, last])
    conn.close()
    if d.empty:
        return None
    d = d[(d["RunnerStatus"] == "entered") & (d["IsReserve"] == 0)
          & (d["PriceDecimal"] > 1)]
    ss = sorted(d["SnapshotAt"].unique())
    rows = []
    for x, y in itertools.pairwise(ss):
        A = d[d["SnapshotAt"] == x].set_index(KEYS)["PriceDecimal"]
        B = d[d["SnapshotAt"] == y].set_index(KEYS)["PriceDecimal"]
        cm = A.index.intersection(B.index)
        if not len(cm):
            continue
        A, B = A.loc[cm], B.loc[cm]
        ch = A != B
        mins = (pd.Timestamp(y) - pd.Timestamp(x)).total_seconds() / 60
        rows.append({
            "window": f"{str(x)[11:19]} -> {str(y)[11:19]}",
            "mins": round(mins, 2),
            "prices": len(cm),
            "changed": int(ch.sum()),
            "% changed": round(ch.mean() * 100, 1),
            "longer": int(((B > A) & ch).sum()),
            "shorter": int(((B < A) & ch).sum()),
            "median move %": round((((B - A).abs() / A)[ch].median() * 100)
                                   if ch.any() else 0.0, 1)})
    return pd.DataFrame(rows)


def compare_bf(day, first, last):
    """Same comparison for the Betfair exchange side (Back1 per selection)."""
    conn = bo.pyodbc.connect(bo.CONN)
    d = pd.read_sql("SELECT * FROM dbo.BetfairLive WHERE RaceDate = ? AND "
                    "SnapshotAt >= ? AND SnapshotAt <= ?",
                    conn, params=[day, first, last])
    conn.close()
    d = d[(d["RunnerStatus"] == "ACTIVE") & d["Back1"].notna()]
    if d.empty:
        return None
    ss = sorted(d["SnapshotAt"].unique())
    rows = []
    k = ["MarketID", "SelectionID"]
    for x, y in itertools.pairwise(ss):
        A = d[d["SnapshotAt"] == x].set_index(k)["Back1"]
        B = d[d["SnapshotAt"] == y].set_index(k)["Back1"]
        cm = A.index.intersection(B.index)
        if not len(cm):
            continue
        A, B = A.loc[cm], B.loc[cm]
        ch = A != B
        mins = (pd.Timestamp(y) - pd.Timestamp(x)).total_seconds() / 60
        rows.append({
            "window": f"{str(x)[11:19]} -> {str(y)[11:19]}",
            "mins": round(mins, 2),
            "prices": len(cm),
            "changed": int(ch.sum()),
            "% changed": round(ch.mean() * 100, 1),
            "longer": int(((B > A) & ch).sum()),
            "shorter": int(((B < A) & ch).sum()),
            "median move %": round((((B - A).abs() / A)[ch].median() * 100)
                                   if ch.any() else 0.0, 1)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("minutes", type=float, nargs="?", default=20)
    ap.add_argument("--every", type=float, default=60, help="seconds per pass")
    ap.add_argument("--date", default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--betfair", action="store_true",
                    help="also snapshot the Betfair exchange each pass, so the "
                         "bookmaker and exchange repricing rates can be "
                         "compared")
    a = ap.parse_args()

    day = a.date or dt.date.today().isoformat()
    bf: Any = None
    if a.betfair:
        import betfair_api
        bf = betfair_api
    if a.quiet:
        sys.stdout = open(os.devnull, "w")
    start = dt.datetime.now()
    stop = start + dt.timedelta(minutes=a.minutes)
    print(f"=== PRICE WATCH {day} === every {a.every:.0f}s for "
          f"{a.minutes:g} min, from {start:%H:%M:%S}"
          f"{'  (bookmakers + exchange)' if bf else '  (bookmakers only)'}")
    passes = 0
    first = None
    while True:
        try:
            bo.snapshot(day, delay=0.2)
            passes += 1
        except Exception as e:
            print(f"  ! bookmaker pass {passes + 1} failed: {e}")
        if bf is not None:
            try:
                bf.snapshot(day)
            except Exception as e:
                print(f"  ! betfair pass failed: {e}")
        if first is None:
            first = dt.datetime.now().replace(microsecond=0)
        left = (stop - dt.datetime.now()).total_seconds()
        if left <= 0:
            break
        print(f"  ... {passes} pass(es) done, "
              f"{int(left/60)}m{int(left % 60):02d}s left", flush=True)
        time.sleep(min(a.every, max(left, 1)))
    if a.quiet:
        sys.stdout = sys.__stdout__

    last = dt.datetime.now().replace(microsecond=0)
    lo = first - dt.timedelta(seconds=5)
    hi = last + dt.timedelta(seconds=5)
    print(f"\n{passes} snapshot rounds between {first:%H:%M:%S} and "
          f"{last:%H:%M:%S}")

    def summary(name, t):
        if t is None or t.empty:
            print(f"  {name}: not enough snapshots to compare")
            return
        print(f"\n--- {name} ---")
        print(t.to_string(index=False))
        tot_p, tot_c, tot_m = t["prices"].sum(), t["changed"].sum(), t["mins"].sum()
        if tot_m:
            rate = tot_c / tot_p / tot_m * 100
            print(f"  {tot_c:,} of {tot_p:,} price-checks changed "
                  f"({tot_c / tot_p * 100:.1f}%) over {tot_m:.1f} min "
                  f"-> {rate:.2f}% per minute")

    summary("BOOKMAKERS (12 accounts)", compare(day, lo, hi))
    if bf is not None:
        summary("BETFAIR EXCHANGE", compare_bf(day, lo, hi))
    print("\n  History: dbo.BookOdds / dbo.BetfairLive. "
          "Charts: dashboard.bat -> Movement tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
