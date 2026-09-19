"""
PRICE LOG - measure whether YOUR bookmaker price beats the Betfair price.

This is the only test that matters: if your price is longer than Betfair's,
you have value; if it is shorter, you do not.

  python scripts/price_log.py new [YYYY-MM-DD]   make today's log sheet
  python scripts/price_log.py report             analyse every log

Workflow each day:
  1. run  price_log.py new
  2. open price_log/price_log_<date>.csv
  3. for each runner, type the BEST price your accounts show into BookPrice
     AND the Betfair price at that same moment into BetfairPrice
     (leave RaceTime/Course/Horse/Jockey alone)
  4. after the race put 1 (won) or 0 into Result  - optional
  5. periodically run  price_log.py report
"""
import datetime
import glob
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "price_log")
CARD = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=SCRAPED_PRODB;"
        r"Trusted_Connection=yes;")


def new(day):
    import pyodbc
    os.makedirs(LOG_DIR, exist_ok=True)
    c = pyodbc.connect(CARD)
    card = pd.read_sql(
        "SELECT RaceTime, CourseName, RaceTitle, HorseName, JockeyName "
        "FROM dbo.Scraped_Racecards WHERE RaceDate = ? ORDER BY RaceTime, "
        "CourseName", c, params=[day])
    c.close()
    if card.empty:
        print(f"No scraped racecard for {day}. Run racecard_today.py first.")
        return
    card["BookPrice"] = ""
    card["BetfairPrice"] = ""
    card["Ratio"] = ""
    card["Result"] = ""
    out = os.path.join(LOG_DIR, f"price_log_{day}.csv")
    card.to_csv(out, index=False)
    print(f"Wrote {out}  ({len(card)} runners)")
    print("Fill in BookPrice + BetfairPrice (at the same moment), and Result.")


def report():
    files = sorted(glob.glob(os.path.join(LOG_DIR, "price_log_*.csv")))
    if not files:
        print("No price_log files yet. Run:  price_log.py new")
        return
    frames = []
    for f in files:
        d = pd.read_csv(f)
        d["src"] = os.path.basename(f)
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df["BookPrice"] = pd.to_numeric(df["BookPrice"], errors="coerce")
    df["BetfairPrice"] = pd.to_numeric(df["BetfairPrice"], errors="coerce")
    v = df[(df["BookPrice"] > 1) & (df["BetfairPrice"] > 1)].copy()
    if v.empty:
        print(f"{len(df)} card rows logged, but no completed price pairs yet.")
        return
    v["ratio"] = v["BookPrice"] / v["BetfairPrice"]
    print(f"=== PRICE LOG REPORT ===  {len(v)} priced runners "
          f"from {len(files)} day(s)\n")
    print(f"  median your price / Betfair : {v['ratio'].median():.3f}")
    print(f"  mean                        : {v['ratio'].mean():.3f}")
    print(f"  your price is longer        : {(v['ratio']>1).mean()*100:.1f}%")
    print(f"  longer by 5%+               : {(v['ratio']>=1.05).mean()*100:.1f}%")
    print(f"  longer by 10%+              : {(v['ratio']>=1.10).mean()*100:.1f}%")
    print("\n  verdict:")
    med = v["ratio"].median()
    if med >= 1.10:
        print("   YES - your price beats the market by 10%+.  Real edge.")
    elif med >= 1.03:
        print("   MARGINAL - small edge, likely eaten by error/variance.")
    else:
        print("   NO - your price does not beat the market. No edge.")
    idx = (v["ratio"] >= 1.05).mean()
    print(f"\n  If you only bet the '5% longer' subset, that is "
          f"{idx*100:.0f}% of runners.")
    print("  Compare that fraction with how many bets you actually want per day.")


def main():
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "report"
    if mode == "new":
        day = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()
        new(day)
    else:
        report()


if __name__ == "__main__":
    main()
