"""
LIVE EDGE - your bookmaker price vs Betfair RIGHT NOW
=====================================================
Joins the newest bookmaker snapshot (PRODB.dbo.BookOdds) with the newest
Betfair exchange snapshot (PRODB.dbo.BetfairLive) and reports, per runner:

    ratio = best bookmaker price / Betfair back price

A ratio above 1.00 means the book is offering a longer price than you can get
on the exchange - the only kind of bet that can carry an edge.  Commission on
exchange winnings only makes the book side look better, never worse.

  python scripts/live_edge.py [YYYY-MM-DD] [--min-ratio 1.0] [--top 30]
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import warnings

import pandas as pd
import pyodbc

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
        r"Trusted_Connection=yes;")


def load(day):
    conn = pyodbc.connect(CONN)
    book = pd.read_sql(
        "SELECT * FROM dbo.BookOdds WHERE RaceDate = ?", conn, params=[day])
    ex = pd.read_sql(
        "SELECT * FROM dbo.BetfairLive WHERE RaceDate = ?", conn, params=[day])
    conn.close()
    return book, ex


def newest_per_race(df, keys):
    if df.empty:
        return df
    fresh = df.groupby(keys)["SnapshotAt"].transform("max")
    return df[df["SnapshotAt"] == fresh].copy()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date", nargs="?", default=None)
    ap.add_argument("--min-ratio", type=float, default=0.0)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--no-traded-filter", dest="require_traded",
                    action="store_false",
                    help="include markets where nothing has traded yet")
    ap.set_defaults(require_traded=True)
    a = ap.parse_args()
    day = a.date or dt.date.today().isoformat()

    book, ex = load(day)
    if book.empty:
        print(f"No bookmaker prices for {day}. Run: book_odds.py snapshot")
        return 1
    if ex.empty:
        print(f"No Betfair prices for {day}. Run: betfair_api.py snapshot")
        return 1

    book = book[(book["RunnerStatus"] == "entered") & (book["IsReserve"] == 0)
                & (book["PriceDecimal"] > 1)]
    book = newest_per_race(book, ["CourseClean", "RaceTime"])
    ex = newest_per_race(ex, ["VenueClean", "MarketID"])

    # A Betfair price is only meaningful when the market is real: an active
    # runner, a two-sided ladder (a lay of 1000.0 is Betfair's cap and just
    # means nobody is laying), and something actually traded.  Without this the
    # join invents 200%+ "edges" on thin evening markets.
    raw_ex = len(ex)
    ex = ex[(ex["RunnerStatus"] == "ACTIVE")
            & (ex["MarketStatus"].isin(["OPEN", "ACTIVE"]))
            & ex["Back1"].notna() & ex["Lay1"].notna()
            & (ex["Lay1"] < 900)]
    if a.require_traded:
        # LastTraded is populated once the runner has actually been traded.
        # (TradedVolume holds the market-level totalMatched, which is 0.0 before
        # the off, so it cannot be used for this.)
        ex = ex[ex["LastTraded"].notna()]
    print(f"betfair rows {raw_ex} -> usable (two-sided"
          f"{', traded' if a.require_traded else ''}) {len(ex)}")

    keys_b = ["RaceDate", "CourseClean", "RaceTime", "HorseClean"]
    book["rank"] = book.groupby(keys_b)["PriceDecimal"].rank(method="first",
                                                            ascending=False)
    best = book[book["rank"] == 1].copy()
    best["NBooks"] = book.groupby(keys_b)["BookmakerName"].transform("size")

    m = best.merge(
        ex[["VenueClean", "HorseClean", "Back1", "Lay1", "Back1Size",
            "Lay1Size", "MarketStatus", "BSP", "TradedVolume", "SnapshotAt"]],
        left_on=["CourseClean", "HorseClean"],
        right_on=["VenueClean", "HorseClean"], how="inner",
        suffixes=("", "_bf"))
    if m.empty:
        print("No runners matched between the two sources - check the "
              "snapshots are the same day.")
        return 1

    m["Ratio"] = m["PriceDecimal"] / m["Back1"]
    m["LayRatio"] = m["PriceDecimal"] / m["Lay1"]
    m["BookTime"] = pd.to_datetime(m["SnapshotAt"]).max()
    m["BFTime"] = pd.to_datetime(m["SnapshotAt_bf"]).max()

    print(f"=== BOOK vs BETFAIR (live) ===  {day}")
    print(f"book snapshot  {m['BookTime'].iloc[0]}   "
          f"betfair snapshot {m['BFTime'].iloc[0]}")
    print(f"runners matched: {len(m)}   markets: "
          f"{m.groupby(['CourseClean', 'RaceTime']).ngroups}\n")

    print(f"  median book/Betfair(back) : {m['Ratio'].median():.3f}")
    print(f"  books at or above Betfair : {(m['Ratio'] >= 1).mean() * 100:.1f}%")
    print(f"  books 5%+ above Betfair   : {(m['Ratio'] >= 1.05).mean() * 100:.1f}%")
    print(f"  books 10%+ above          : {(m['Ratio'] >= 1.10).mean() * 100:.1f}%")
    print(f"  median book/Betfair(lay)  : {m['LayRatio'].median():.3f}\n")

    show = m[m["Ratio"] >= a.min_ratio].sort_values("Ratio", ascending=False)
    print("--- biggest bookmaker edges ---")
    print("%-22s %-12s %-6s %8s %-12s %8s %8s"
          % ("HORSE", "COURSE", "TIME", "BOOK", "WHICH BOOK", "BETFAIR",
             "RATIO"))
    for _, r in show.head(a.top).iterrows():
        print("%-22s %-12s %-6s %8.2f %-12s %8.2f %7.1f%%"
              % (str(r["HorseName"])[:22], str(r["CourseName"])[:12],
                 str(r["RaceTime"])[:5], r["PriceDecimal"],
                 str(r["BookmakerName"])[:12], r["Back1"],
                 (r["Ratio"] - 1) * 100))

    worst = m.sort_values("Ratio").head(5)
    print("\n--- worst (book price much shorter than Betfair) ---")
    for _, r in worst.iterrows():
        print("  %-22s %-12s book %8.2f vs betfair %8.2f  (%.0f%%)"
              % (str(r["HorseName"])[:22], str(r["CourseName"])[:12],
                 r["PriceDecimal"], r["Back1"], (r["Ratio"] - 1) * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
