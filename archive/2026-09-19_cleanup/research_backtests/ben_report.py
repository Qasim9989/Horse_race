"""
BEN'S SYSTEM - console report
=============================
  1. Is the edge in the picks, or only in the prices?  The forward ledger
     (reports/bens_forward_ledger.csv) settles every selection twice: at the
     odds Ben recorded, and at the real Betfair SP.
  2. Today's card, priced against your accounts and the live exchange.

  python scripts/ben_report.py [YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import os

import pandas as pd
import pyodbc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "reports", "bens_forward_ledger.csv")
CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
        r"Trusted_Connection=yes;")


def clean(s):
    return "".join(c for c in str(s or "").lower() if c.isalnum())


def ledger_report():
    led = pd.read_csv(LEDGER)
    for c in ("Odds", "BSP_TRUE", "PL_taken", "PL_bsp", "move_pct", "won",
              "Stake"):
        if c in led:
            led[c] = pd.to_numeric(led[c], errors="coerce")
    stake = float(led["Stake"].fillna(1).sum()) or len(led)
    print(f"=== BEN LEDGER === {len(led):,} settled bets   "
          f"{led['Date'].min()} -> {led['Date'].max()}")
    print(f"  strike rate          {led['won'].mean() * 100:5.1f}%")
    print(f"  at his recorded odds {led['PL_taken'].sum():+9.2f}  "
          f"ROI {led['PL_taken'].sum() / stake * 100:+6.2f}%")
    print(f"  at the real BSP      {led['PL_bsp'].sum():+9.2f}  "
          f"ROI {led['PL_bsp'].sum() / stake * 100:+6.2f}%")
    print(f"  median odds {led['Odds'].median():.2f} vs median BSP "
          f"{led['BSP_TRUE'].median():.2f}   "
          f"(BSP/odds {led['BSP_TRUE'].div(led['Odds']).median():.3f})")

    def table(title, col, bins=None, labels=None):
        d = led.copy()
        d["g"] = pd.cut(d[col], bins, labels=labels) if bins else d[col]
        g = d.groupby("g", observed=True).agg(
            Bets=("PL_taken", "size"), His=("PL_taken", "mean"),
            BSP=("PL_bsp", "mean"))
        g["His"] = (g["His"] * 100).round(1)
        g["BSP"] = (g["BSP"] * 100).round(1)
        print(f"\n  {title}")
        print(g.to_string())

    table("by price band", "Odds", [0, 4, 8, 15, 33, 66, 1e9],
          ["<4", "4-8", "8-15", "15-33", "33-66", "66+"])
    table("by price move", "move_pct", [-1e9, -20, -5, 5, 20, 1e9],
          ["short 20%+", "short 5-20%", "flat", "drift 5-20%", "drift 20%+"])
    table("by bet type", "BetType")
    return led


def card_report(day):
    cands = sorted(glob.glob(os.path.join(ROOT, "reports",
                                          f"bens_today_{day}.csv")))
    if not cands:
        cands = sorted(glob.glob(os.path.join(ROOT, "reports",
                                              "bens_today_*.csv")))
    if not cands:
        print(f"\n=== TODAY'S CARD === none found for {day}")
        return
    card = pd.read_csv(cands[-1])
    card = card[card["BEN"].astype(str).str.lower().isin(("true", "1"))]
    card = card.drop_duplicates(["RaceTime", "CourseName", "HorseName"])
    print(f"\n=== TODAY'S CARD === {os.path.basename(cands[-1])}  "
          f"{len(card)} picks")

    conn = pyodbc.connect(CONN)
    book = pd.read_sql("SELECT * FROM dbo.BookOdds WHERE RaceDate = ?",
                       conn, params=[day])
    ex = pd.read_sql("SELECT * FROM dbo.BetfairLive WHERE RaceDate = ?",
                     conn, params=[day])
    conn.close()

    bk_map, bf_map = {}, {}
    if not book.empty:
        bk = book[(book["RunnerStatus"] == "entered")
                  & (book["IsReserve"] == 0) & (book["PriceDecimal"] > 1)]
        bk = bk[bk["SnapshotAt"] == bk.groupby(
            ["CourseClean", "RaceTime"])["SnapshotAt"].transform("max")]
        for _, r in bk.sort_values("PriceDecimal", ascending=False) \
                .drop_duplicates(["CourseClean", "HorseClean"]).iterrows():
            bk_map[(clean(r["CourseClean"]), clean(r["HorseClean"]))] = (
                r["PriceDecimal"], r["BookmakerName"])
    if not ex.empty:
        ex = ex[(ex["RunnerStatus"] == "ACTIVE") & ex["Back1"].notna()
                & (ex["Lay1"].fillna(9999) < 900)]
        ex = ex[ex["SnapshotAt"] == ex.groupby(
            ["VenueClean", "MarketID"])["SnapshotAt"].transform("max")]
        for _, r in ex.drop_duplicates(["VenueClean", "HorseClean"]).iterrows():
            bf_map[(clean(r["VenueClean"]), clean(r["HorseClean"]))] = r["Back1"]

    print("%-16s %-6s %-22s %8s %-12s %8s %8s"
          % ("COURSE", "TIME", "HORSE", "BOOK", "ACCOUNT", "BETFAIR", "EDGE"))
    n_beat = n_priced = 0
    for _, r in card.sort_values(["RaceTime", "CourseName", "HorseName"]) \
            .iterrows():
        k = (clean(r["CourseName"]), clean(r["HorseName"]))
        b = bk_map.get(k)
        f = bf_map.get(k)
        edge = ""
        if b and f:
            n_priced += 1
            ratio = b[0] / f
            n_beat += ratio >= 1
            edge = f"{(ratio - 1) * 100:+.1f}%"
        print("%-16s %-6s %-22s %8s %-12s %8s %8s"
              % (str(r["CourseName"])[:16], str(r["RaceTime"])[:5],
                 str(r["HorseName"])[:22],
                 f"{b[0]:.2f}" if b else "-", str(b[1])[:12] if b else "-",
                 f"{f:.2f}" if f else "-", edge))
    print(f"\n  {n_beat} of {n_priced} priced picks are at or above the Betfair "
          "back price")
    print("  EDGE = book/Betfair - 1. Above 0%: your price is longer than the "
          "exchange.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date", nargs="?", default=None)
    a = ap.parse_args()
    ledger_report()
    card_report(a.date or dt.date.today().isoformat())


if __name__ == "__main__":
    main()

