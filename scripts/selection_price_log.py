r"""
SELECTION PRICE LOG - what price could we ACTUALLY have taken?
==============================================================
Answers one question, cheaply: for every selection, what was the price when we
could have bet, and what did it become by the off.

IT MAKES NO WEB REQUESTS.  Nothing here touches the site.  Prices are read from
the snapshots the site-facing scripts already write:

    PRODB.dbo.BookOdds    (book_odds.py)   best price, each-way terms per runner
    PRODB.dbo.BetfairLive (betfair_api.py) exchange back/lay
    PRODB.dbo.BFSP        the settled BSP
    RACINGTV_2023_2026.dbo.Scraped_Results  SP, finishing position

So the load on the site is whatever those scripts already do - this adds none of
its own.  Run it once per snapshot you want (the snapshot itself is taken by
book_odds.py, not by this).

NOTE ON SNAPSHOTS: BookOdds currently holds ONE snapshot per day, so 'book_open'
and 'book_price' come out identical and the drift tables have nothing to chew on.
To measure how the price decays through the day, take the snapshot three times:
morning, midday, and just before the off -

    python scripts\book_odds.py snapshot
    python scripts\selection_price_log.py capture --selections reports\picks.csv

That is the same sequential single sweep that already runs once a day (0.35s
between requests, no threads), so it is 3x today's load rather than a new kind
of load.  Do NOT point price_watch.py at this - that takes a snapshot every 60
seconds and is meant for short bursts before a race, not all day.

    python scripts\selection_price_log.py capture  --selections picks.csv
    python scripts\selection_price_log.py settle          (re-run as results land)
    python scripts\selection_price_log.py report

selections.csv needs: race_date, meeting, horse   (race_time optional)
The log lives in reports\selection_prices.csv.  capture only appends (so the
timestamps are an audit trail); settle updates the result columns in place and
is meant to be re-run until every row is settled, because results are published
late.  Nothing is ever deleted.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys
import unicodedata
import warnings

import pandas as pd
import numpy as np

# pyodbc connections are fine here; keep pandas from warning about them on
# every run, which otherwise shows up in red text in the console.
warnings.filterwarnings("ignore", message=".*SQLAlchemy.*")

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
REPORTS = os.path.join(PROJECT, "reports")
LOG = os.path.join(REPORTS, "selection_prices.csv")

PRO = (r"Driver={ODBC Driver 17 for SQL Server};"
       r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;"
       r"MultipleActiveResultSets=True;Connection Timeout=120;")
RTV = (r"Driver={ODBC Driver 17 for SQL Server};"
       r"Server=(localdb)\MSSQLLocalDB;Database=RACINGTV_2023_2026;"
       r"Trusted_Connection=yes;MultipleActiveResultSets=True;Connection Timeout=120;")

FIELDS = ["captured_at", "race_date", "race_time", "meeting", "horse", "rule",
          "book_open", "book_price", "book_bookie", "bookmakers", "ew_places",
          "ew_denom", "field_size", "places_std", "places_offered", "extra_place",
          "bf_back", "bf_lay", "bf_snap", "bsp", "sp", "implied",
          "finish_pos", "status"]

# 'Bai Tong (IRE)' / 'Al Hussar (FR)' -> 'Bai Tong' / 'Al Hussar'
COUNTRY = re.compile(r"\((?:" + "|".join((
    "ire", "gb", "usa", "can", "fr", "ger", "ity", "spa", "aus", "nz", "jpn",
    "hk", "swe", "den", "nor", "pol", "hun", "cz", "bel", "hol", "neth", "uae",
    "arg", "brz", "chi", "ind", "kor", "mex", "per", "phi", "rus", "saf",
    "sing", "swi", "sui", "tur", "urug")) + r")\)\s*$")


def clean(value) -> str:
    """Join key for a name.

    The results feed writes 'Bai Tong (IRE)' where the price feed writes
    'Bai Tong', so the breeding suffix has to come off or nothing ever matches.
    """
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("'", "").replace("\u2019", "")
    text = COUNTRY.sub("", text.strip())
    return "".join(ch for ch in text if ch.isalnum())


def venue_match(pick: str, source: str) -> bool:
    """Do two cleaned venue names refer to the same meeting?

    Betfair says 'hamilton', the bookmaker feed says 'Hamilton Park', so an
    exact match silently drops every Betfair price.  Accept a prefix either way,
    with a length guard so short names cannot collide by accident.
    """
    if not pick or not source:
        return False
    if pick == source:
        return True
    short, long = sorted((pick, source), key=len)
    return len(short) >= 4 and long.startswith(short)


def index_quotes(frame, date_col: str) -> dict:
    """(date, horse) -> rows, so a selection can be looked up without a join."""
    out: dict = {}
    if frame is None or frame.empty:
        return out
    for row in frame.itertuples():
        key = (getattr(row, date_col), row.horse_clean)
        out.setdefault(key, []).append(row)
    return out


def quotes_for(index: dict, race_date, horse_clean: str, meeting_clean: str) -> list:
    """Rows for this runner, preferring those at the meeting we asked for.

    If the venue cannot be reconciled and there is more than one candidate, we
    return nothing rather than guess - a wrong price is worse than a blank.
    """
    rows = index.get((race_date, horse_clean), [])
    if not rows:
        return []
    at_venue = [r for r in rows if venue_match(r.venue_key, meeting_clean)]
    if at_venue:
        return at_venue
    return rows if len(rows) == 1 else []


def parse_sp(value):
    text = str(value or "").strip().lower().replace("f", "").replace("j", "")
    if not text:
        return None
    if text in ("evs", "evens", "1/1"):
        return 2.0
    m = re.match(r"^(\d+)\s*/\s*(\d+)", text)
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        return round(1.0 + num / den, 2) if den else None
    try:
        v = float(text)
        return v if v > 1 else None
    except ValueError:
        return None


def connect(dsn: str):
    import pyodbc
    return pyodbc.connect(dsn)


def load_selections(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        sys.exit(f"{path} not found.\n"
                 f"Copy reports\\picks_template.csv to {path} and list today's "
                 f"selections (race_date, meeting, horse).")
    with open(path, encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        sys.exit(f"{path} is empty")
    for row in rows:
        for key in row:
            row[key] = (row[key] or "").strip()
    df = pd.DataFrame(rows)
    missing = [c for c in ("race_date", "meeting", "horse") if c not in df.columns]
    if missing:
        sys.exit(f"{path} is missing columns: {missing}")
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce").dt.date
    df["meeting_clean"] = df["meeting"].map(clean)
    df["horse_clean"] = df["horse"].map(clean)
    df = df.dropna(subset=["race_date"])
    before = len(df)
    df = df.drop_duplicates(["race_date", "meeting_clean", "horse_clean"])
    if len(df) != before:
        print(f"  (dropped {before - len(df)} duplicate selection row(s))")
    today = dt.date.today()
    if all(d < today for d in df["race_date"]):
        newest = max(df["race_date"])
        print(f"  note: these selections are from {newest}, not today - logging "
              f"them is fine, but run 'picks' if you meant today's sheet")
    return df


def picks_from_sheet(flag: str, date_str: str | None) -> None:
    """Build reports\\picks.csv from the day's racecard sheet.

    reports\\selections_YYYY-MM-DD.csv holds every runner with the system's own
    SEL_HARD / SEL_SOFT flags, so the log can be fed from the system instead of
    typed by hand.  It overwrites reports\\picks.csv - that file is an input
    mirror, not the log.
    """
    import glob
    day = date_str or dt.date.today().isoformat()
    src = os.path.join(REPORTS, f"selections_{day}.csv")
    if not os.path.exists(src):
        found = sorted(glob.glob(os.path.join(REPORTS, "selections_*.csv")))
        latest = os.path.basename(found[-1])[11:21] if found else "none"
        sys.exit(f"{src} not found.\n"
                 f"The latest sheet on disk is selections_{latest}.csv - pass "
                 f"--date {latest} to use it.")
    sheet = pd.read_csv(src, encoding="utf-8-sig")
    sheet.columns = [c.strip() for c in sheet.columns]
    flags = [c for c in ("SEL_HARD", "SEL_SOFT") if c in sheet.columns]
    if flag != "all" and not flags:
        sys.exit(f"{src} has no SEL_HARD/SEL_SOFT column - cannot pick selections")
    if flag == "all":
        keep = sheet
        why = "every runner"
    else:
        if flag not in ("any",) and flag not in flags:
            sys.exit(f"{src} has no {flag} column (has: {flags})")
        wanted = flags if flag == "any" else [flag]
        truthy = sheet[wanted].astype(str).apply(
            lambda col: col.str.strip().str.lower().isin(("true", "1", "yes", "y")))
        keep = sheet[truthy.any(axis=1)]
        why = " or ".join(wanted)
        if keep.empty:
            sys.exit(f"no rows flagged {why} in {src} - nothing to log today")
    picks = pd.DataFrame({
        "race_date": day,
        "meeting": keep["Course"].astype(str),
        "horse": keep["Horse"].astype(str),
        "race_time": keep.get("RaceTime", pd.Series("", index=keep.index)).astype(str),
        # which rule flagged it: all five (hard) or the 3-of-5 soft set - the log is
        # split by this at review time, so a week can show which rule is paying
        "rule": [_row_rule(keep, i, flag) for i in keep.index],
    })
    out = os.path.join(REPORTS, "picks.csv")
    picks.to_csv(out, index=False)
    print(f"picks from {os.path.basename(src)} ({why}): {len(picks)} selection(s)")
    for _, r in picks.head(15).iterrows():
        print(f"    {r['race_time']:<6} {r['meeting']:<18} {r['horse']}")
    if len(picks) > 15:
        print(f"    ...and {len(picks) - 15} more")
    print(f"  -> {out}")


def capture(path: str) -> None:
    """Append one snapshot per selection: what is available RIGHT NOW."""
    sel = load_selections(path)
    days = sorted({d.isoformat() for d in sel["race_date"]})
    con = connect(PRO)
    books = pd.read_sql(f"""
        SELECT RaceDate, CourseClean, HorseClean, RaceTime, SnapshotAt,
               BookmakerName, PriceDecimal, EWPlaces, EWDenominator
        FROM dbo.BookOdds WHERE RaceDate >= '{days[0]}' AND RaceDate <= '{days[-1]}'
    """, con)
    live = pd.read_sql(f"""
        SELECT RaceDate, VenueClean, HorseClean, SnapshotAt, Back1, Lay1
        FROM dbo.BetfairLive WHERE RaceDate >= '{days[0]}' AND RaceDate <= '{days[-1]}'
    """, con)
    bfsp = pd.read_sql(f"""
        SELECT RaceDate, CourseClean, HorseClean, BSP_TRUE, EventID
        FROM dbo.BFSP WHERE RaceDate >= '{days[0]}' AND RaceDate <= '{days[-1]}'
    """, con)
    con.close()

    for frame, cols in ((books, ("CourseClean", "HorseClean")),
                        (live, ("VenueClean", "HorseClean")),
                        (bfsp, ("CourseClean", "HorseClean"))):
        if not frame.empty:
            frame["race_date"] = pd.to_datetime(frame["RaceDate"]).dt.date
            frame["venue_key"] = frame[cols[0]].map(clean)
            frame["meeting_clean"] = frame["venue_key"]
            frame["horse_clean"] = frame[cols[1]].map(clean)
    books["PriceDecimal"] = pd.to_numeric(books["PriceDecimal"], errors="coerce")
    if len(live):
        live["Back1"] = pd.to_numeric(live["Back1"], errors="coerce")
        live["Lay1"] = pd.to_numeric(live["Lay1"], errors="coerce")
    if len(bfsp):
        bfsp["BSP_TRUE"] = pd.to_numeric(bfsp["BSP_TRUE"], errors="coerce")
    book_idx = index_quotes(books, "race_date")
    live_idx = index_quotes(live, "race_date")
    bfsp_idx = index_quotes(bfsp, "race_date")
    # field size per race, for working out whether the places on offer were standard
    field_by_event = {}
    if len(bfsp) and "EventID" in bfsp.columns:
        for (day, event), group in bfsp.groupby(["race_date", "EventID"]):
            field_by_event[(day, event)] = len(group)
    # ...and before the race has run there is no BFSP row, so count runners in the
    # bookmaker snapshot instead (the tradeable field, matched on course and time)
    field_by_race = {}
    if len(books) and "RaceTime" in books.columns:
        books["_hhmm"] = books["RaceTime"].astype(str).str.slice(0, 5)
        for key, group in books.groupby(["race_date", "meeting_clean", "_hhmm"]):
            field_by_race[key] = group["horse_clean"].nunique()

    now = dt.datetime.now().isoformat(timespec="seconds")
    os.makedirs(REPORTS, exist_ok=True)
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        written = 0
        for _, s in sel.iterrows():
            b = [r for r in quotes_for(book_idx, s["race_date"], s["horse_clean"],
                                       s["meeting_clean"])
                 if r.PriceDecimal and r.PriceDecimal > 1]
            lv = quotes_for(live_idx, s["race_date"], s["horse_clean"],
                            s["meeting_clean"])
            bs = quotes_for(bfsp_idx, s["race_date"], s["horse_clean"],
                            s["meeting_clean"])
            row = {f: "" for f in FIELDS}
            row.update({
                "captured_at": now,
                "race_date": s["race_date"],
                "race_time": s.get("race_time", ""),
                "meeting": s["meeting"],
                "horse": s["horse"],
                "rule": s.get("rule", ""),
                "bookmakers": len({r.BookmakerName for r in b}),
                "bsp": float(bs[0].BSP_TRUE) if bs and pd.notna(bs[0].BSP_TRUE) else "",
                "status": "pending",
            })
            if b:
                # book_open  = best price on the first snapshot of the day (morning)
                # book_price = best price each bookmaker is showing NOW (shop around)
                by_time = sorted(b, key=lambda r: r.SnapshotAt)
                first_seen, last_seen = {}, {}
                for r in by_time:
                    first_seen.setdefault(r.BookmakerName, r)
                    last_seen[r.BookmakerName] = r
                opens = [r.PriceDecimal for r in first_seen.values()
                         if pd.notna(r.PriceDecimal)]
                if opens:
                    row["book_open"] = float(max(opens))
                best = max((r for r in last_seen.values() if pd.notna(r.PriceDecimal)),
                           key=lambda r: r.PriceDecimal)
                row["book_price"] = float(best.PriceDecimal)
                row["book_bookie"] = str(best.BookmakerName)
                places = [r.EWPlaces for r in b if pd.notna(r.EWPlaces)]
                denoms = [r.EWDenominator for r in b if pd.notna(r.EWDenominator)]
                if places:
                    row["ew_places"] = int(places[0])
                if denoms:
                    row["ew_denom"] = int(denoms[0])

                # Was an EXTRA place on offer in this race?  UK standard is 4 places
                # for a 16+ runner handicap, 3 for 8+, 2 below that; anything above
                # the standard in the market is the promotion worth having.
                field = None
                if bs and pd.notna(bs[0].EventID):
                    field = field_by_event.get((s["race_date"], bs[0].EventID))
                if not field:
                    field = field_by_race.get((s["race_date"], s["meeting_clean"],
                                               str(s.get("race_time") or "")[:5]))
                offered = int(max(places)) if places else None
                if field:
                    row["field_size"] = int(field)
                    std = 4 if field >= 16 else (3 if field >= 8 else 2)
                    row["places_std"] = std
                    if offered:
                        row["places_offered"] = offered
                        row["extra_place"] = 1 if offered > std else 0
            if lv:
                last = max(lv, key=lambda r: r.SnapshotAt)
                row["bf_back"] = float(last.Back1) if pd.notna(last.Back1) else ""
                row["bf_lay"] = float(last.Lay1) if pd.notna(last.Lay1) else ""
                row["bf_snap"] = str(last.SnapshotAt)
            writer.writerow(row)
            written += 1
    print(f"captured {written} selection(s) at {now}")
    print(f"  -> {LOG}")
    print("  a selection with no book_price means no snapshot exists for it yet: "
          "run book_odds.py, then capture again")


def settle() -> None:
    """Fill in the SP, BSP, position and field size for rows where results exist.

    Safe to re-run: rows whose result has not been published yet stay 'pending'
    and are picked up on the next run.
    """
    if not os.path.exists(LOG):
        sys.exit(f"no log yet: {LOG}")
    log = pd.read_csv(LOG)
    log["race_date"] = pd.to_datetime(log["race_date"], errors="coerce").dt.date
    days = sorted({d.isoformat() for d in log["race_date"].dropna().unique()})
    con = connect(RTV)
    res = pd.read_sql(f"""
        SELECT RaceDate, CourseName, HorseName, RaceTime, PosNo, SP
        FROM dbo.Scraped_Results
        WHERE RaceDate >= '{days[0]}' AND RaceDate <= '{days[-1]}'
    """, con)
    con.close()
    res["race_date"] = pd.to_datetime(res["RaceDate"]).dt.date
    res["venue_key"] = res["CourseName"].map(clean)
    res["horse_clean"] = res["HorseName"].map(clean)
    res["field_size"] = res.groupby(
        ["race_date", "venue_key", "RaceTime"])["HorseName"].transform("size")
    res["sp_dec"] = res["SP"].map(parse_sp)
    res = res.drop_duplicates(["race_date", "venue_key", "horse_clean"])
    res_idx = index_quotes(res, "race_date")

    log["meeting_clean"] = log["meeting"].map(clean)
    log["horse_clean"] = log["horse"].map(clean)
    log["finish_pos"] = log["finish_pos"].astype(object)
    log["sp"] = log["sp"].astype(object)
    hit = 0
    for i, r in log.iterrows():
        found = quotes_for(res_idx, r["race_date"], r["horse_clean"],
                           r["meeting_clean"])
        if not found:
            log.at[i, "status"] = "pending"
            continue
        got = found[0]
        hit += 1
        log.at[i, "finish_pos"] = got.PosNo
        log.at[i, "sp"] = got.sp_dec
        log.at[i, "field_size"] = got.field_size
        log.at[i, "status"] = "settled"
    log["implied"] = (1.0 / pd.to_numeric(log["bsp"], errors="coerce")).round(4)
    log = log.reindex(columns=[c for c in FIELDS if c in log])
    log.to_csv(LOG, index=False)
    print(f"settled {hit:,} of {len(log):,} rows"
          f"  ({len(log) - hit:,} still waiting on results)")
    lagging = log[log["status"] != "settled"].drop_duplicates(["race_date", "meeting"])
    if len(lagging):
        print("  no result published yet for:")
        for _, r in lagging.head(12).iterrows():
            print(f"    {r['race_date']}  {r['meeting']}")
        if len(lagging) > 12:
            print(f"    ...and {len(lagging) - 12} more")
        print("  re-run 'settle' later - nothing is lost")


def report() -> None:
    """The four tables that decide how to bet."""
    if not os.path.exists(LOG):
        sys.exit(f"no log yet: {LOG}\n"
                 f"Run: log_selections.bat sweep   (then settle, then report)")
    log = pd.read_csv(LOG)
    numeric = ("book_open", "book_price", "bf_back", "bf_lay", "bsp", "sp",
               "ew_places", "ew_denom", "field_size")
    for c in numeric:
        if c in log.columns:
            log[c] = pd.to_numeric(log[c], errors="coerce")
    settled = log[log["status"] == "settled"].copy()
    if settled.empty:
        print("nothing settled yet - run 'settle' again once results are published")
        return
    captures = settled.groupby(["race_date", "meeting", "horse"]).size()
    settled["captured_at"] = pd.to_datetime(settled["captured_at"], errors="coerce")
    settled = settled.sort_values("captured_at")
    settled["posn"] = pd.to_numeric(
        settled["finish_pos"].astype(str).str.extract(r"(\d+)")[0],
        errors="coerce").fillna(99)
    settled["won"] = settled["posn"] == 1
    s = settled.drop_duplicates(["race_date", "meeting", "horse"])   # first look
    print(f"{len(s):,} settled selections   "
          f"win {s['won'].mean() * 100:.2f}%   top3 {(s['posn'] <= 3).mean() * 100:.2f}%"
          f"   avg field {s['field_size'].mean():.1f}")
    if int(captures.max()) > 1:
        print(f"   (prices at the first look for each selection; "
              f"up to {int(captures.max())} captures each)")

    def roi(price_col, extra_places=0):
        if price_col not in s.columns:
            return None
        p = s[s[price_col].notna() & (s[price_col] > 1)]
        if len(p) < 10:
            return None
        price = p[price_col].to_numpy()
        win = np.where(p["won"].to_numpy(), (price - 1) * 0.98, -1.0)
        places = np.minimum(p["ew_places"].fillna(3).to_numpy() + extra_places,
                            p["field_size"].fillna(8).to_numpy())
        denom = p["ew_denom"].fillna(5).replace(0, 5).to_numpy()
        place_odds = 1.0 + (price - 1.0) / denom
        place = np.where(p["posn"].to_numpy() <= places, place_odds - 1.0, -1.0)
        return win.mean() * 100, (win + place).mean() / 2 * 100

    print("\n1  PRICE BASIS (win-only, net 2% on wins)")
    for label, col in (("morning price (bookmakers)", "book_open"),
                       ("price when captured", "book_price"),
                       ("Betfair back when captured", "bf_back"),
                       ("Betfair BSP", "bsp"),
                       ("bookmaker SP", "sp")):
        got = roi(col)
        if got:
            print(f"   {label:<30}{got[0]:>+8.2f}%")

    print("\n2  PRICE DRIFT (average price by basis)")
    for label, col in (("morning (bookmakers)", "book_open"),
                       ("when captured (bookmakers)", "book_price"),
                       ("when captured (Betfair back)", "bf_back"),
                       ("BSP", "bsp"), ("SP", "sp")):
        if col not in s.columns:
            continue
        p = s[s[col].notna() & (s[col] > 1)]
        if len(p) >= 10:
            print(f"   {label:<30}{p[col].mean():>8.2f}   median {p[col].median():.2f}")
    pair = s[s["book_price"].notna() & s["bsp"].notna()]
    if len(pair) >= 10:
        beat = (pair["book_price"] > pair["bsp"]).mean() * 100
        print(f"   the captured price BEAT BSP on {beat:.1f}% of {len(pair):,} selections")
    pair = s[s["book_open"].notna() & s["bsp"].notna()]
    if len(pair) >= 10:
        beat = (pair["book_open"] > pair["bsp"]).mean() * 100
        print(f"   the morning price  BEAT BSP on {beat:.1f}% of {len(pair):,} selections")

    print("\n3  WIN RATE vs THE MARKET")
    p = s[s["bsp"].notna() & (s["bsp"] > 1)]
    if len(p) >= 10:
        imp = (1 / p["bsp"]).mean() * 100
        print(f"   actual {p['won'].mean() * 100:.2f}%   implied from BSP {imp:.2f}%   "
              f"gap {p['won'].mean() * 100 - imp:+.2f}pp")

    print("\n4  EACH-WAY AT THE CAPTURED PRICE (places capped at the field size)")
    print("\n6  BY RULE - which rule flagged the picks that paid")
    if "rule" in s.columns:
        for rule, group in s.groupby(s["rule"].fillna("")):
            p = group[group["book_price"].notna() & (group["book_price"] > 1)]
            if len(p) < 10:
                print(f"   {str(rule) or '(unset)':<10} n={len(group):<5} (too few to read)")
                continue
            imp = (1 / p["bsp"].replace(0, np.nan)).mean() * 100 if p["bsp"].gt(1).any() else float("nan")
            win = np.where(p["won"].to_numpy(),
                           (p["book_price"].to_numpy() - 1) * 0.98, -1.0)
            print(f"   {str(rule) or '(unset)':<10} n={len(p):<5} "
                  f"win {p['won'].mean() * 100:>5.1f}%  "
                  f"implied {imp:>5.1f}%  gap {p['won'].mean() * 100 - imp:>+5.1f}pp  "
                  f"ROI(early) {win.mean() * 100:>+6.1f}%")
    else:
        print("   (no rule column in this log - it fills in from the next capture)")

    print("\n7  EXTRA PLACES - was an extra place on offer, and does it pay?")
    if "extra_place" not in log.columns:
        print("   (this log predates the extra-place flag - it fills in from the next capture)")
        return
    for label, n in (("with an extra place", int((s["extra_place"] == 1).sum())),
                     ("standard places only", int((s["extra_place"] == 0).sum())),
                     ("unknown (no field size)", int(s["extra_place"].isna().sum()))):
        print(f"   {label:<26}{n:>5} of {len(s)}")
    for flag in (1, 0):
        p = s[s["extra_place"] == flag]
        if len(p) < 10:
            continue
        p2 = p[p["book_price"].notna() & (p["book_price"] > 1)]
        if len(p2) < 10:
            continue
        price = p2["book_price"].to_numpy()
        win = np.where(p2["won"].to_numpy(), (price - 1) * 0.98, -1.0)
        places = np.minimum(p2["ew_places"].fillna(3).to_numpy(), p2["field_size"].fillna(8).to_numpy())
        denom = p2["ew_denom"].fillna(5).replace(0, 5).to_numpy()
        place_odds = 1.0 + (price - 1.0) / denom
        place = np.where(p2["posn"].to_numpy() <= places, place_odds - 1.0, -1.0)
        tag = "extra place" if flag == 1 else "standard only"
        print(f"   {tag:<26}{len(p2):>5} priced   win {win.mean() * 100:+6.1f}%"
              f"   EW {(win + place).mean() / 2 * 100:+6.1f}%")

    if len(settled) > len(s):
        print("\n5  DOES THE PRICE HOLD? (selections captured more than once)")
        rows = []
        for _, r in settled.iterrows():
            if pd.isna(r["captured_at"]) or pd.isna(r["book_price"]):
                continue
            try:
                off = pd.Timestamp(f"{r['race_date']} {r['race_time']}")
            except (ValueError, TypeError):
                continue
            if pd.isna(off):
                continue
            hours = (off - r["captured_at"]).total_seconds() / 3600
            if -1 <= hours <= 72:
                rows.append({"hours": hours, "price": r["book_price"],
                             "won": r["won"], "bsp": r["bsp"]})
        d = pd.DataFrame(rows)
        if len(d) >= 10:
            d["band"] = pd.cut(d["hours"], [-1, 2, 6, 12, 24, 72],
                               labels=["under 2h", "2-6h", "6-12h", "12-24h", "1-3 days"])
            for band, g in d.groupby("band", observed=True):
                if len(g) >= 3:
                    print(f"   {str(band):<10} n={len(g):>4}  price {g['price'].mean():>6.2f}"
                          f"   BSP {g['bsp'].mean():>6.2f}   win {g['won'].mean() * 100:>5.1f}%")


def _row_rule(sheet, index, flag):
    """Which flag on the day's sheet put this runner into the log."""
    if flag == "all":
        return "all"
    for name, label in (("SEL_HARD", "hard"), ("SEL_SOFT", "soft")):
        if name in sheet.columns:
            value = str(sheet.at[index, name]).strip().lower()
            if value in ("true", "1", "yes", "y"):
                return label
    return str(flag).lower()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action",
                    choices=["capture", "settle", "report", "picks"])
    ap.add_argument("--selections", default=os.path.join(REPORTS, "picks.csv"))
    ap.add_argument("--flag", default="any",
                    choices=["any", "SEL_HARD", "SEL_SOFT", "all"],
                    help="which flag on the day's sheet counts as a selection")
    ap.add_argument("--date", default=None, help="sheet date, default today")
    a = ap.parse_args()
    if a.action == "capture":
        capture(a.selections)
    elif a.action == "settle":
        settle()
    elif a.action == "picks":
        picks_from_sheet(a.flag, a.date)
    else:
        report()


if __name__ == "__main__":
    main()


