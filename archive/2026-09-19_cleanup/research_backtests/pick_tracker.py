"""
PICK TRACKER - has the price moved on our selections?
=====================================================
For a set of selections, shows the best book price and the Betfair back price
at every snapshot we hold, and whether the edge that got them picked is there.

  python scripts/pick_tracker.py --ben            # Ben's picks today
  python scripts/pick_tracker.py --auto           # what qualifies right now
  python scripts/pick_tracker.py --logged         # whatever is in dbo.AutoPicks
  python scripts/pick_tracker.py --horse "Pike Road" --course Uttoxeter
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import sys

import pandas as pd
import pyodbc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
        r"Trusted_Connection=yes;")


def clean(s):
    return "".join(c for c in str(s or "").lower() if c.isalnum())


def ben_picks(day):
    cands = sorted(glob.glob(os.path.join(ROOT, "reports",
                                          f"bens_today_{day}.csv")))
    if not cands:
        return []
    d = pd.read_csv(cands[-1])
    d = d[d["BEN"].astype(str).str.lower().isin(("true", "1"))]
    d = d.drop_duplicates(["RaceTime", "CourseName", "HorseName"])
    return [(clean(r["CourseName"]), clean(r["HorseName"]), r["HorseName"],
             r["CourseName"], str(r["RaceTime"])) for _, r in d.iterrows()]


def logged_picks(day):
    conn = pyodbc.connect(CONN)
    try:
        d = pd.read_sql("SELECT DISTINCT CourseClean, HorseClean, HorseName, "
                        "RaceTime, CourseClean AS CourseName FROM dbo.AutoPicks "
                        "WHERE RaceDate = ?", conn, params=[day])
    except pyodbc.Error:
        d = pd.DataFrame()
    conn.close()
    if d.empty:
        return []
    return [(r["CourseClean"], r["HorseClean"], r["HorseName"],
             r["CourseName"], str(r["RaceTime"])) for _, r in d.iterrows()]


def auto_picks_now(day):
    """What beats Betfair right now (same rules as live_edge)."""
    conn = pyodbc.connect(CONN)
    book = pd.read_sql("SELECT * FROM dbo.BookOdds WHERE RaceDate = ?",
                       conn, params=[day])
    ex = pd.read_sql("SELECT * FROM dbo.BetfairLive WHERE RaceDate = ?",
                     conn, params=[day])
    conn.close()
    if book.empty or ex.empty:
        return []
    book = book[(book["RunnerStatus"] == "entered") & (book["IsReserve"] == 0)
                & (book["PriceDecimal"] > 1)]
    ex = ex[(ex["RunnerStatus"] == "ACTIVE") & ex["Back1"].notna()
            & ex["Lay1"].notna() & (ex["Lay1"] < 900) & ex["LastTraded"].notna()]
    fb = book.groupby(["CourseClean", "RaceTime"])["SnapshotAt"].transform("max")
    book = book[book["SnapshotAt"] == fb]
    best = book.sort_values("PriceDecimal", ascending=False).drop_duplicates(
        ["CourseClean", "HorseClean"])
    fe = ex.groupby(["VenueClean", "MarketID"])["SnapshotAt"].transform("max")
    ex = ex[ex["SnapshotAt"] == fe].drop_duplicates(["VenueClean", "HorseClean"])
    bf = ex.set_index(["VenueClean", "HorseClean"])["Back1"]
    best = best.assign(bf=best.set_index(["CourseClean", "HorseClean"])
                       .index.map(bf))
    best = best[best["PriceDecimal"] / best["bf"] >= 1.0]
    return [(r["CourseClean"], r["HorseClean"], r["HorseName"],
             r["CourseName"], str(r["RaceTime"])) for _, r in best.iterrows()]


def trace(day, picks, tol_min=30):
    """Price history per selection: each book snapshot paired with the nearest
    Betfair snapshot in time (the two sources are polled separately, so exact
    timestamp matches are rare)."""
    conn = pyodbc.connect(CONN)
    book = pd.read_sql("SELECT SnapshotAt, CourseClean, HorseClean, "
                       "PriceDecimal FROM dbo.BookOdds WHERE RaceDate = ?",
                       conn, params=[day])
    ex = pd.read_sql("SELECT SnapshotAt, VenueClean, HorseClean, Back1 "
                     "FROM dbo.BetfairLive WHERE RaceDate = ?",
                     conn, params=[day])
    conn.close()
    book = book[book["PriceDecimal"] > 1]
    ex = ex[ex["Back1"].notna()]
    {t: pd.Timestamp(t) for t in book["SnapshotAt"].unique()}
    fs_all = {t: pd.Timestamp(t) for t in ex["SnapshotAt"].unique()}

    def nearest_bf(t):
        if not fs_all:
            return None
        ts = pd.Timestamp(t)
        best, bestdiff = None, None
        for k, v in fs_all.items():
            d = abs((v - ts).total_seconds())
            if bestdiff is None or d < bestdiff:
                best, bestdiff = k, d
        return best if bestdiff is not None and bestdiff <= tol_min * 60 else None

    out = []
    for course, horse, hname, cname, rtime in picks:
        b = book[(book["CourseClean"] == course) & (book["HorseClean"] == horse)]
        f = ex[(ex["VenueClean"] == course) & (ex["HorseClean"] == horse)]
        bs = (b.groupby("SnapshotAt")["PriceDecimal"].max()
              if not b.empty else pd.Series(dtype=float))
        fs = (f.groupby("SnapshotAt")["Back1"].max()
              if not f.empty else pd.Series(dtype=float))
        rows = []
        for t in sorted(bs.index):
            bp = bs.get(t)
            bt = nearest_bf(t)
            fp = fs.get(bt) if bt is not None else None
            rows.append({"time": str(t)[11:19], "book": bp, "bf": fp,
                         "bf_time": str(bt)[11:19] if bt is not None else "-",
                         "edge": ((bp / fp - 1) * 100) if (bp and fp) else None})
        priced = [r for r in rows if r["edge"] is not None]
        out.append({"course": cname[:12], "time": rtime[:5], "horse": hname[:22],
                    "rows": rows, "first": priced[0] if priced else None,
                    "last": priced[-1] if priced else None})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date", nargs="?", default=None)
    ap.add_argument("--ben", action="store_true")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--logged", action="store_true")
    ap.add_argument("--horse", default=None)
    ap.add_argument("--course", default=None)
    ap.add_argument("--history", action="store_true",
                    help="print every snapshot, not just the summary line")
    a = ap.parse_args()
    day = a.date or dt.date.today().isoformat()

    if a.horse:
        picks = [(clean(a.course or ""), clean(a.horse), a.horse,
                  a.course or "", "")]
        label = "single"
    elif a.logged:
        picks, label = logged_picks(day), "logged"
    elif a.auto:
        picks, label = auto_picks_now(day), "auto"
    else:
        picks, label = ben_picks(day), "Ben"
    if not picks:
        print(f"no selections found for {day}")
        return 1

    print(f"=== PRICE TRACE {day} === {len(picks)} selections ({label})")
    print("edge = best book price / Betfair back price - 1; "
          "positive = your price is longer than the exchange\n")
    short = drifted = flat = survived = gone = 0
    for p in trace(day, picks):
        f, l = p["first"], p["last"]
        if not f:
            print(f"{p['course']:<12} {p['time']:<6} {p['horse']:<22} "
                  "not priced yet")
            continue
        if f["book"] == l["book"]:
            flat += 1
            trend = "flat"
        elif l["book"] < f["book"]:
            short += 1
            trend = "SHORTENED"
        else:
            drifted += 1
            trend = "drifted"
        if f["edge"] >= 0 and l["edge"] >= 0:
            survived += 1
        elif f["edge"] >= 0 > l["edge"]:
            gone += 1
        print(f"{p['course']:<12} {p['time']:<5} {p['horse']:<22} "
              f"book {f['book']:>7.2f} -> {l['book']:>7.2f}   "
              f"BF {f['bf']:>6.2f} -> {l['bf']:>6.2f}   "
              f"edge {f['edge']:>+6.1f}% -> {l['edge']:>+6.1f}%   {trend}")
        if a.history:
            for r in p["rows"]:
                e = f"{r['edge']:+.1f}%" if r["edge"] is not None else "-"
                print(f"        {r['time']}  book {r['book']!s:>8}  "
                      f"BF {r['bf']!s:>8}  edge {e:>7}")
    n = max(len(picks), 1)
    print(f"\n  {short} shortened, {drifted} drifted, {flat} flat "
          f"({short / n * 100:.0f}% moved in)")
    print(f"  edge still present: {survived}    edge lost since first seen: "
          f"{gone}")
    print("  'shortened' means money came for it - the exchange usually moves "
          "first, so a book still on its old price is a lag, not value.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

