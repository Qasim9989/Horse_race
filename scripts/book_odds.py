"""
BOOK ODDS SNAPSHOT  (RacingTV / Oddschecker feed -> SQL)
========================================================
Captures EVERY bookmaker price for EVERY runner from RacingTV's JSON API
(see scripts/rtv_api.py) into PRODB.dbo.BookOdds, then compares those prices
with the Betfair SP held in PRODB.dbo.BFSP.

This finally answers the project's central question on real prices:
    how much worse is each bookmaker than Betfair?

    python scripts/book_odds.py snapshot [YYYY-MM-DD] [--limit N] [--delay S]
    python scripts/book_odds.py pricelog [YYYY-MM-DD]
    python scripts/book_odds.py report   [YYYY-MM-DD] [--book "Paddy Power"|BEST]
    python scripts/book_odds.py value    [YYYY-MM-DD]

The snapshot is timestamped, so running it repeatedly through the day builds a
price-movement history (morning -> BSP) for bookmaker prices, not just Betfair.

Notes
  * No browser, no API key: plain HTTPS to api.racingtv.com.
  * ~2 requests per race, so a full UK/IRE day (~35 races) takes ~60-90s.
  * RaceTime/CourseClean/HorseClean use the same normalisation as dbo.BFSP so
    the two tables join directly.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

import pyodbc

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import rtv_api

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
        r"Trusted_Connection=yes;MultipleActiveResultSets=True;Pooling=True;")

DDL = """
IF OBJECT_ID('dbo.BookOdds') IS NULL
BEGIN
CREATE TABLE dbo.BookOdds (
    ID              BIGINT IDENTITY(1,1) PRIMARY KEY,
    SnapshotAt      DATETIME2(0)  NOT NULL,
    RaceDate        DATE          NOT NULL,
    RaceTime        TIME(0)       NULL,
    CourseClean     VARCHAR(64)   NOT NULL,
    CourseName      VARCHAR(60)   NULL,
    RaceTitle       VARCHAR(200)  NULL,
    RunnerID        BIGINT        NULL,
    HorseClean      VARCHAR(64)   NOT NULL,
    HorseName       VARCHAR(80)   NULL,
    ClothNumber     INT           NULL,
    RunnerStatus    VARCHAR(16)   NULL,
    IsReserve       BIT           NULL,
    JockeyName      VARCHAR(80)   NULL,
    TrainerName     VARCHAR(80)   NULL,
    Age             INT           NULL,
    Weight          VARCHAR(12)   NULL,
    TimeformRating  VARCHAR(16)   NULL,
    Form            VARCHAR(32)   NULL,
    BookmakerID     INT           NOT NULL,
    BookmakerName   VARCHAR(40)   NULL,
    PriceDecimal    FLOAT         NOT NULL,
    PriceFractional VARCHAR(12)   NULL,
    Fluctuation     VARCHAR(14)   NULL,
    EWPlaces        INT           NULL,
    EWDenominator   INT           NULL
);
CREATE INDEX IX_BookOdds_Key
    ON dbo.BookOdds (RaceDate, CourseClean, RaceTime, HorseClean);
END;
"""

INSERT_SQL = """
INSERT INTO dbo.BookOdds
 (SnapshotAt, RaceDate, RaceTime, CourseClean, CourseName, RaceTitle, RunnerID,
  HorseClean, HorseName, ClothNumber, RunnerStatus, IsReserve, JockeyName,
  TrainerName, Age, Weight, TimeformRating, Form, BookmakerID, BookmakerName,
  PriceDecimal, PriceFractional, Fluctuation, EWPlaces, EWDenominator)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def snapshot(date_str, limit=None, delay=0.35, purge=False):
    races = rtv_api.day_races(date_str)
    if limit:
        races = races[:limit]
    if not races:
        print(f"No UK/IRE races found for {date_str}.")
        return 0

    conn = pyodbc.connect(CONN, autocommit=True)
    cur = conn.cursor()
    cur.execute(DDL)
    if purge:
        cur.execute("DELETE FROM dbo.BookOdds WHERE RaceDate = ?", (date_str,))
        print(f"purged existing rows for {date_str}")

    snap = dt.datetime.now().replace(microsecond=0)
    total, races_ok, no_odds = 0, 0, 0
    for i, r in enumerate(races, 1):
        try:
            detail = rtv_api.race_detail(r["date"], r["course_slug"], r["hhmm"])
            runners = rtv_api.runners_of(detail)
            if not runners:
                continue
            odds, _books = rtv_api.runner_odds([x["runner_id"] for x in runners])
            rows = []
            skipped = 0
            for run in runners:
                for o in odds.get(run["runner_id"], []):
                    if not o["decimal"] or o["decimal"] <= 1.0:
                        skipped += 1          # "0.00" placeholder = book not quoting
                        continue
                    rows.append((
                        snap, r["date"], f"{r['time']}:00" if r["time"] else None,
                        rtv_api.clean_name(r["course_name"]), r["course_name"],
                        (r["title"] or "")[:200], run["runner_id"],
                        run["horse_clean"], run["horse_name"], run["cloth_number"],
                        run["status"], run["reserve"], run["jockey"], run["trainer"],
                        run["age"], run["weight"], run["timeform_rating"],
                        run["form"], o["bookmaker_id"], o["bookmaker_name"],
                        o["decimal"], o["fractional"], o["fluctuation"],
                        o["places"], o["denominator"]))
            if not rows:
                no_odds += 1
                print(f"  [{i}/{len(races)}] {r['time']} {r['course_name'][:16]:16s}"
                      "  no odds published")
            else:
                cur.fast_executemany = True
                cur.executemany(INSERT_SQL, rows)
                total += len(rows)
                races_ok += 1
                print(f"  [{i}/{len(races)}] {r['time']} {r['course_name'][:16]:16s}"
                      f"  {len(runners):2d} runners  {len(rows):4d} prices"
                      + (f"  ({skipped} blank)" if skipped else ""))
        except Exception as e:
            print(f"  [{i}/{len(races)}] {r['time']} {r['course_name'][:16]:16s}"
                  f"  ! {e}")
        time.sleep(delay)
    conn.close()
    print(f"\nSnapshot {snap}: {races_ok}/{len(races)} races priced, "
          f"{no_odds} without odds, {total} rows -> PRODB.dbo.BookOdds")
    return total

def report(date_from, date_to=None, book=None, top=30):
    """Compare stored bookmaker prices with Betfair BSP + per-book margins."""
    import warnings

    import pandas as pd

    warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

    date_to = date_to or date_from
    conn = pyodbc.connect(CONN)
    bo = pd.read_sql(
        "SELECT * FROM dbo.BookOdds WHERE RaceDate BETWEEN ? AND ?",
        conn, params=[date_from, date_to])
    bf = pd.read_sql(
        "SELECT RaceDate, RaceTime, CourseClean, HorseClean, BSP_TRUE "
        "FROM dbo.BFSP WHERE RaceDate BETWEEN ? AND ?",
        conn, params=[date_from, date_to])
    conn.close()
    if bo.empty:
        print("No BookOdds rows. Run:  python scripts/book_odds.py snapshot")
        return

    live = bo[(bo["RunnerStatus"] == "entered") & (bo["IsReserve"] == 0)
              & (bo["PriceDecimal"] > 1)].copy()
    print(f"=== BOOK ODDS vs BETFAIR BSP ===  {date_from}..{date_to}")
    print(f"rows {len(bo):,}   live-runner rows {len(live):,}   "
          f"races {live.groupby(['CourseClean', 'RaceTime']).ngroups}   "
          f"snapshots {live['SnapshotAt'].nunique()}\n")

    # ---- per-book margin, measured on the live runners of each race ----------
    grp = (live.groupby(["SnapshotAt", "RaceDate", "CourseClean", "RaceTime",
                         "BookmakerName"])["PriceDecimal"]
           .apply(lambda s: sum(1.0 / v for v in s if v > 1)).reset_index())
    grp.columns = [*list(grp.columns[:-1]), "Overround"]
    marg = grp.groupby("BookmakerName")["Overround"].median()

    # ---- best-of-market per runner (max price at that snapshot) --------------
    best = (live.groupby(["SnapshotAt", "RaceDate", "CourseClean", "RaceTime",
                          "HorseClean"])["PriceDecimal"].max().reset_index())
    bo_g = (best.groupby(["SnapshotAt", "RaceDate", "CourseClean", "RaceTime"])
            ["PriceDecimal"].apply(lambda s: sum(1.0 / v for v in s if v > 1))
            .reset_index())
    bo_g.columns = [*list(bo_g.columns[:-1]), "Overround"]
    best_margin = bo_g["Overround"].median()

    best_price_share(live)

    # ---- join to BSP ---------------------------------------------------------
    bf = bf.dropna(subset=["BSP_TRUE"]).drop_duplicates(
        ["CourseClean", "HorseClean"], keep="first")
    m = live.merge(bf[["CourseClean", "HorseClean", "BSP_TRUE"]],
                   on=["CourseClean", "HorseClean"], how="left")
    matched = m.dropna(subset=["BSP_TRUE"])
    print(f"BSP rows available {len(bf):,}   price rows matched to a BSP: "
          f"{len(matched):,} of {len(m):,} "
          f"({(len(matched)/max(len(m),1))*100:.1f}%)\n")

    if matched.empty:
        print("No BSP for this date yet (race not run / BSP not backfilled).")
        print("Margins only (median overround per race, live runners):")
        for nm, ov in marg.sort_values().items():
            print("  %-18s %.4f  (%+.2f%%)" % (nm, ov, (ov - 1) * 100))
        print("  %-18s %.4f  (%+.2f%%)" % ("BEST-OF-MARKET", best_margin,
                                           (best_margin - 1) * 100))
        return

    matched = matched.copy()
    matched["Ratio"] = matched["PriceDecimal"] / matched["BSP_TRUE"]
    _compare_bsp(matched, marg, best_margin, book, top)


def _compare_bsp(matched, marg, best_margin, book=None, top=30):
    """Print the per-book table: price/BSP, how often it beats the SP, margin.

    Split out of report() so it can be exercised without a database
    (see tests/test_book_odds.py).
    """
    stat = (matched.groupby("BookmakerName")
            .agg(Runners=("Ratio", "size"),
                 MedianRatio=("Ratio", "median"),
                 PctLonger=("Ratio", lambda s: (s > 1).mean() * 100),
                 PctLonger5=("Ratio", lambda s: (s >= 1.05).mean() * 100))
            .join(marg.rename("MedianMargin")))
    stat["MarginPct"] = (stat["MedianMargin"] - 1) * 100

    bm = (matched.groupby(["CourseClean", "RaceTime", "HorseClean"],
                          as_index=False)
          .agg(BestPrice=("PriceDecimal", "max"), BSP=("BSP_TRUE", "first"),
               HorseName=("HorseName", "first")))
    bm["Ratio"] = bm["BestPrice"] / bm["BSP"]

    print("%-16s %8s %12s %10s %11s %11s"
          % ("BOOKMAKER", "RUNNERS", "price/BSP", "% > BSP", "% > BSP+5%",
             "margin%"))
    for nm, r in stat.sort_values("MedianRatio").iterrows():
        print("%-16s %8d %12.3f %9.1f%% %10.1f%% %11.1f%%"
              % (nm[:16], int(r["Runners"]), r["MedianRatio"], r["PctLonger"],
                 r["PctLonger5"], r["MarginPct"]))
    print("%-16s %8d %12.3f %9.1f%% %10.1f%% %11.1f%%"
          % ("BEST-OF-MARKET", len(bm), bm["Ratio"].median(),
             (bm["Ratio"] > 1).mean() * 100, (bm["Ratio"] >= 1.05).mean() * 100,
             (best_margin - 1) * 100))

    print("\nprice/BSP > 1 means that book's price is LONGER than the Betfair SP.")
    if not book:
        return
    if book.lower() in ("best", "market", "best-of-market", "all"):
        sub = bm.sort_values("Ratio", ascending=False)
        print("\n=== BEST PRICE across all your accounts - top %d of %d "
              "runners vs BSP ===" % (top, len(sub)))
        print("%-22s %-10s %-8s %8s %8s %s"
              % ("HORSE", "COURSE", "TIME", "BEST", "BSP", "RATIO"))
        for _, r in sub.head(top).iterrows():
            print("%-22s %-10s %-8s %8.2f %8.2f %6.3f"
                  % (str(r["HorseName"])[:22], r["CourseClean"][:10],
                     str(r["RaceTime"])[:5], r["BestPrice"], r["BSP"], r["Ratio"]))
        _print_subsets(sub["Ratio"])
        return
    sub = matched[matched["BookmakerName"].str.lower() == book.lower()]
    if sub.empty:
        print("\nNo rows for '{}'. Available: {}".format(book, ", ".join(sorted(matched["BookmakerName"].unique()))))
        return
    sub = sub.sort_values("Ratio", ascending=False)
    print(f"\n=== '{book}' - best edges vs BSP ({len(sub)} runners) ===")
    print("%-22s %-10s %-8s %8s %8s %s"
          % ("HORSE", "COURSE", "TIME", "PRICE", "BSP", "RATIO"))
    for _, r in sub.head(top).iterrows():
        print("%-22s %-10s %-8s %8.2f %8.2f %6.3f"
              % (str(r["HorseName"])[:22], r["CourseClean"][:10],
                 str(r["RaceTime"])[:5], r["PriceDecimal"], r["BSP_TRUE"],
                 r["Ratio"]))
    _print_subsets(sub["Ratio"])


def _print_subsets(ratios):
    print("\nSubsets:")
    for lo in (1.0, 1.05, 1.10, 1.20):
        n = int((ratios >= lo).sum())
        print("  price >= BSP x %.2f : %4d of %d runners (%.1f%%)"
              % (lo, n, len(ratios), 100.0 * n / max(len(ratios), 1)))




def best_price_share(live, top=12):
    """Which of your accounts holds the best price, and by how much.

    Uses only the latest snapshot.  'Edge' is the average price advantage that
    account had over the next-best account on the same runner, so it measures
    how much you gain by shopping rather than taking one book's price.
    """

    if live is None or live.empty:
        return
    race_keys = ["RaceDate", "CourseClean", "RaceTime"]
    fresh = live.groupby(race_keys)["SnapshotAt"].transform("max")
    cur = live[live["SnapshotAt"] == fresh].copy()
    keys = [*race_keys, "HorseClean"]
    cur["rank"] = cur.groupby(keys)["PriceDecimal"].rank(method="first",
                                                         ascending=False)
    best = cur[cur["rank"] == 1].set_index(keys)
    second = cur[cur["rank"] == 2].set_index(keys)["PriceDecimal"]
    df = best[["PriceDecimal", "BookmakerName", "HorseName"]].copy()
    df["Second"] = df.index.map(second)
    df["Edge"] = df["PriceDecimal"] / df["Second"]
    g = (df.groupby("BookmakerName")
         .agg(Best=("PriceDecimal", "size"), Edge=("Edge", "mean"))
         .sort_values("Best", ascending=False))
    g["Share"] = g["Best"] / max(len(df), 1) * 100
    print(f"\nBEST-PRICE SHARE  (latest price per runner, {len(df)} runners)")
    print("  %-16s %6s %8s %12s" % ("ACCOUNT", "BEST", "SHARE", "edge vs 2nd"))
    for nm, r in g.head(top).iterrows():
        print("  %-16s %6d %7.1f%% %11.2f%%"
              % (nm[:16], int(r["Best"]), r["Share"], (r["Edge"] - 1) * 100))
    print("  'edge vs 2nd' = average % you would have lost taking the "
          "next-best account instead.")
    return df


def pricelog(date_str):
    """Write price_log/price_log_<date>_auto.csv with BOTH sides auto-filled.

    BookPrice    - best price available across your accounts (latest snapshot)
    BestBook     - which account that was
    BetfairPrice - Betfair SP from dbo.BFSP once the race has run
    Ratio/Result - computed, so scripts/price_log.py report works unchanged
    """
    import os
    import warnings

    import pandas as pd
    warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

    conn = pyodbc.connect(CONN)
    bo = pd.read_sql("SELECT * FROM dbo.BookOdds WHERE RaceDate = ?",
                     conn, params=[date_str])
    bf = pd.read_sql("SELECT CourseClean, HorseClean, BSP_TRUE, WinLose "
                     "FROM dbo.BFSP WHERE RaceDate = ?", conn, params=[date_str])
    conn.close()
    if bo.empty:
        print(f"No BookOdds rows for {date_str}. Run: book_odds.py snapshot")
        return

    live = bo[(bo["RunnerStatus"] == "entered") & (bo["IsReserve"] == 0)
              & (bo["PriceDecimal"] > 1)].copy()
    last = live["SnapshotAt"].max()
    race_keys = ["RaceDate", "CourseClean", "RaceTime"]
    fresh = live.groupby(race_keys)["SnapshotAt"].transform("max")
    cur = live[live["SnapshotAt"] == fresh].copy()
    keys = [*race_keys, "HorseClean"]
    cur["rank"] = cur.groupby(keys)["PriceDecimal"].rank(method="first",
                                                         ascending=False)
    best = cur[cur["rank"] == 1].copy()
    second = cur[cur["rank"] == 2].set_index(keys)["PriceDecimal"]
    best["Second"] = best.set_index(keys).index.map(second)
    best["NBooks"] = cur.groupby(keys)["BookmakerName"].transform("size")
    best["BookPrice"] = best["PriceDecimal"]
    best["Shop"] = (best["BookPrice"] / best["Second"]).round(3)

    if not bf.empty:
        bf = (bf.dropna(subset=["BSP_TRUE"])
              .drop_duplicates(["CourseClean", "HorseClean"]))
        best = best.merge(bf[["CourseClean", "HorseClean", "BSP_TRUE", "WinLose"]],
                          on=["CourseClean", "HorseClean"], how="left")
    else:
        best["BSP_TRUE"] = None
        best["WinLose"] = None
    best["BSP_TRUE"] = pd.to_numeric(best["BSP_TRUE"], errors="coerce")
    best["WinLose"] = pd.to_numeric(best["WinLose"], errors="coerce")
    best["Ratio"] = (best["BookPrice"] / best["BSP_TRUE"]).round(3)
    best["BetfairPrice"] = best["BSP_TRUE"]

    out = pd.DataFrame({
        "RaceTime": best["RaceTime"].astype(str).str[:5],
        "CourseName": best["CourseName"],
        "RaceTitle": best["RaceTitle"],
        "HorseName": best["HorseName"],
        "JockeyName": best["JockeyName"],
        "BookPrice": best["BookPrice"].round(2),
        "BetfairPrice": best["BetfairPrice"],
        "Ratio": best["Ratio"],
        "Result": best["WinLose"],
        "BestBook": best["BookmakerName"],
        "ShopEdge": best["Shop"],
        "NBooks": best["NBooks"],
        "Move": best["Fluctuation"],
        "TimeformRating": best["TimeformRating"].astype(str)
        .str.replace(r"<[^>]+>", " ", regex=True).str.strip(),
        "Age": best["Age"],
        "Weight": best["Weight"],
        "TrainerName": best["TrainerName"],
        "RunnerID": best["RunnerID"],
        "SnapshotAt": best["SnapshotAt"],
    }).sort_values(["RaceTime", "CourseName", "HorseName"])

    log_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "price_log")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"price_log_{date_str}_auto.csv")
    out.to_csv(path, index=False)
    have_bsp = int(out["BetfairPrice"].notna().sum())
    print(f"Wrote {path}  ({len(out)} runners, snapshot {last})")
    print(f"  {have_bsp} rows already have a Betfair SP; "
          f"{len(out) - have_bsp} will fill once the BSP backfill runs.")
    print("  BookPrice = best of your 12 accounts; BestBook says which one.")


def _fair_probs(cur):
    """De-vig every book, average the implied probabilities -> fair odds.

    For each book the implied probabilities (1/price) are normalised to sum to
    1 (removing that book's margin), then averaged across books.  The result is
    a consensus fair probability per runner - the market's own best estimate.
    """

    race_keys = ["RaceDate", "CourseClean", "RaceTime"]
    w = cur.copy()
    w["Imp"] = 1.0 / w["PriceDecimal"]
    w["ImpNorm"] = (w["Imp"] / w.groupby([*race_keys, "BookmakerName"])["Imp"]
                    .transform("sum"))
    fair = (w.groupby([*race_keys, "HorseClean"])
            .agg(FairProb=("ImpNorm", "mean"), Books=("ImpNorm", "size"))
            .reset_index())
    fair["FairOdds"] = 1.0 / fair["FairProb"]
    return fair


def value(date_str, top=25, min_books=8, min_value=0.0):
    """Overlay finder: best price across your accounts vs the de-vigged market.

    value% = best_price x fair_prob - 1.  A positive value means the price you
    can take is longer than the market's own consensus estimate of the horse.

    NOTE: this is a *filter to be validated*, not a proven edge.  Re-run
    `report` once the Betfair SP exists to see whether per-race the
    positive-value subset actually beat BSP.
    """
    import warnings

    import pandas as pd
    warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

    conn = pyodbc.connect(CONN)
    bo = pd.read_sql("SELECT * FROM dbo.BookOdds WHERE RaceDate = ?",
                     conn, params=[date_str])
    conn.close()
    if bo.empty:
        print(f"No BookOdds rows for {date_str}. Run: book_odds.py snapshot")
        return

    live = bo[(bo["RunnerStatus"] == "entered") & (bo["IsReserve"] == 0)
              & (bo["PriceDecimal"] > 1)].copy()
    race_keys = ["RaceDate", "CourseClean", "RaceTime"]
    fresh = live.groupby(race_keys)["SnapshotAt"].transform("max")
    cur = live[live["SnapshotAt"] == fresh].copy()
    keys = [*race_keys, "HorseClean"]
    cur["rank"] = cur.groupby(keys)["PriceDecimal"].rank(method="first",
                                                         ascending=False)
    best = cur[cur["rank"] == 1].copy()
    fair = _fair_probs(cur)
    assessed = best.merge(fair, on=keys, how="inner")
    assessed = assessed[assessed["Books"] >= min_books].copy()
    assessed["Value"] = (assessed["PriceDecimal"] * assessed["FairProb"] - 1)
    df = assessed[assessed["Value"] >= min_value] \
        .sort_values("Value", ascending=False)

    print(f"=== OVERLAYS vs DE-VIGGED CONSENSUS ===  {date_str}")
    print(f"snapshot coverage: {cur['SnapshotAt'].max()}   "
          f"runners assessed: {len(assessed)} (>= {min_books} books)   "
          f"at or above {min_value*100:.0f}%: {len(df)}")
    if df.empty:
        print("No runner's best price beats the de-vigged consensus.")
        return
    print("\n%-8s %-12s %-22s %8s %-14s %8s %8s %s"
          % ("TIME", "COURSE", "HORSE", "BEST", "BOOK", "FAIR", "VALUE", "MOVE"))
    for _, r in df.head(top).iterrows():
        print("%-8s %-12s %-22s %8.2f %-14s %8.2f %7.1f%% %s"
              % (str(r["RaceTime"])[:5], str(r["CourseName"])[:12],
                 str(r["HorseName"])[:22], r["PriceDecimal"],
                 str(r["BookmakerName"])[:14], r["FairOdds"],
                 r["Value"] * 100, str(r["Fluctuation"] or "-")))
    print("\nDistribution of value across all priced runners:")
    v = assessed["Value"]
    for lo, label in ((0.0, "any value (>0%)"), (0.05, "5%+"),
                      (0.10, "10%+"), (0.20, "20%+")):
        n = int((v >= lo).sum())
        print("  %-16s %5d of %d runners (%.1f%%)"
              % (label, n, len(v), 100.0 * n / max(len(v), 1)))
    print("  median value    : %+.2f%%" % (v.median() * 100))
    print("\nThe median runner is negative by construction - you are only paid "
          "on the overlays.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["snapshot", "pricelog", "report", "value"])
    ap.add_argument("date", nargs="?", default=None)
    ap.add_argument("date_to", nargs="?", default=None)
    ap.add_argument("--limit", type=int, default=None,
                    help="snapshot only the first N races (testing)")
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--purge", action="store_true",
                    help="delete existing rows for that date first")
    ap.add_argument("--book", default=None,
                    help="focus the report on one bookmaker name")
    a = ap.parse_args()
    day = a.date or dt.date.today().isoformat()
    if a.mode == "snapshot":
        snapshot(day, limit=a.limit, delay=a.delay, purge=a.purge)
    elif a.mode == "pricelog":
        pricelog(day)
    elif a.mode == "value":
        value(day)
    else:
        report(day, a.date_to, book=a.book)


if __name__ == "__main__":
    main()
