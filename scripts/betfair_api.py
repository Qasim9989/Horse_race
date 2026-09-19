"""
BETFAIR EXCHANGE API - LIVE PRICES + SP
=======================================
Reads the app key / login from scripts/betfair_creds.py (environment first,
then E:\\CGMBET\\betfair_api_config.json).  Nothing is hardcoded here and no
credential is ever printed.

What it does
  login      - exchange the app key + login for a session token (24h)
  markets    - list today's UK/IRE win markets
  snapshot   - store live back/lay prices per runner -> PRODB.dbo.BetfairLive
  sp         - pull the Betfair Starting Price for finished races -> dbo.BFSP
               (same number as the daily file, but available the same evening)

Usage
  python scripts/betfair_api.py login
  python scripts/betfair_api.py markets  [YYYY-MM-DD]
  python scripts/betfair_api.py snapshot [YYYY-MM-DD]
  python scripts/betfair_api.py sp       [YYYY-MM-DD]

Endpoints (Exchange API-NG, REST)
  POST https://identitysso.betfair.com/api/login
  POST https://api.betfair.com/exchange/betting/rest/v1.0/listMarketCatalogue/
  POST https://api.betfair.com/exchange/betting/rest/v1.0/listMarketBook/
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pyodbc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import betfair_creds as creds

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;"
        r"Trusted_Connection=yes;MultipleActiveResultSets=True;")

LOGIN_URL = "https://identitysso.betfair.com/api/login"
API = "https://api.betfair.com/exchange/betting/rest/v1.0/{}"
TOKEN_CACHE = os.path.join(os.environ.get("TEMP", "."), "bf_token.json")


class BetfairError(RuntimeError):
    pass


def posix(d):
    return f"{d.isoformat()}T00:00:00Z"


def login(use_cache=True, verbose=True):
    """Return a session token, reusing a cached one for up to 20 hours."""
    if use_cache and os.path.isfile(TOKEN_CACHE):
        try:
            with open(TOKEN_CACHE, encoding="utf-8") as f:
                c = json.load(f)
            age = time.time() - c.get("at", 0)
            if c.get("token") and age < 20 * 3600:
                if verbose:
                    print(f"  using cached session ({age/3600:.1f}h old)")
                return c["token"]
        except Exception:
            pass

    app_key = creds.get("app_key")
    user = creds.get("username")
    pw = creds.get("password")
    if not (app_key and user and pw):
        raise BetfairError("missing credentials - run: "
                           "python scripts\\betfair_creds.py --diagnose")

    body = urllib.parse.urlencode({"username": user, "password": pw}).encode()
    req = urllib.request.Request(
        LOGIN_URL, data=body,
        headers={"X-Application": app_key, "Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            payload = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise BetfairError(f"login HTTP {e.code}: {detail}") from e

    status = payload.get("status")
    if status != "SUCCESS":
        raise BetfairError(f"login rejected: {status} "
                           f"{payload.get('error') or ''}".strip())
    token = payload.get("token")
    try:
        with open(TOKEN_CACHE, "w", encoding="utf-8") as f:
            json.dump({"token": token, "at": time.time()}, f)
    except OSError:
        pass
    if verbose:
        print(f"  logged in as {creds.mask(user)}  token {creds.mask(token)}")
    return token


def call(method, payload, token=None):
    """POST to an Exchange API-NG method and return the parsed JSON."""
    token = token or login(verbose=False)
    req = urllib.request.Request(
        API.format(method), data=json.dumps(payload).encode(),
        headers={"X-Application": creds.get("app_key"),
                 "X-Authentication": token,
                 "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        raise BetfairError(f"{method} HTTP {e.code}: "
                           f"{e.read().decode('utf-8', 'ignore')[:300]}") from e



def markets(date_str, token=None):
    """A day's UK/IRE win markets: market_id, venue, start_utc, runners."""
    d = dt.date.fromisoformat(date_str)
    payload = {
        "filter": {
            "eventTypeIds": ["7"],                    # horse racing
            "marketCountries": ["GB", "IE"],
            "marketTypeCodes": ["WIN"],
            "marketStartTime": {"from": posix(d),
                                "to": posix(d + dt.timedelta(days=1))},
        },
        "sort": "FIRST_TO_START",
        "maxResults": "1000",
        "marketProjection": ["EVENT", "RUNNER_DESCRIPTION",
                             "MARKET_START_TIME", "MARKET_DESCRIPTION"],
    }
    res = call("listMarketCatalogue/", payload, token)
    out = []
    for m in res or []:
        ev = m.get("event") or {}
        out.append({
            "market_id": m.get("marketId"),
            "venue": ev.get("venue"),
            "country": ev.get("countryCode"),
            "name": m.get("marketName"),
            "start_utc": (m.get("marketStartTime") or "").replace("Z", ""),
            "runners": {str(r["selectionId"]): r.get("runnerName")
                        for r in (m.get("runners") or [])},
        })
    out.sort(key=lambda x: x["start_utc"])
    return out


def books(market_ids, token=None, price_data=("EX_BEST_OFFERS",), batch=10):
    """Best back/lay and/or SP for the given markets.

    Betfair rejects heavy requests with TOO_MUCH_DATA, so markets are fetched in
    small batches and only the price types actually needed are requested:
    live snapshots want EX_BEST_OFFERS, the SP pull wants SP_* only.
    """
    out: list[dict] = []
    for i in range(0, len(market_ids), batch):
        chunk = market_ids[i:i + batch]
        payload = {
            "marketIds": chunk,
            "priceProjection": {"priceData": list(price_data),
                                "virtualise": True},
            "orderProjection": "ALL",
        }
        try:
            out.extend(call("listMarketBook/", payload, token) or [])
        except BetfairError as e:
            if "TOO_MUCH_DATA" in str(e) and batch > 2:
                # halve the batch and retry this chunk
                mid = max(1, len(chunk) // 2)
                out.extend(books(chunk[:mid], token, price_data,
                                 max(2, batch // 2)))
                out.extend(books(chunk[mid:], token, price_data,
                                 max(2, batch // 2)))
            else:
                raise
        time.sleep(0.25)
    return out


DDL = """
IF OBJECT_ID('dbo.BetfairLive') IS NULL
BEGIN
CREATE TABLE dbo.BetfairLive (
    ID            BIGINT IDENTITY(1,1) PRIMARY KEY,
    SnapshotAt    DATETIME2(0)  NOT NULL,
    RaceDate      DATE          NOT NULL,
    StartUTC      DATETIME2(0)  NULL,
    VenueClean    VARCHAR(64)   NOT NULL,
    Venue         VARCHAR(64)   NULL,
    MarketID      VARCHAR(24)   NOT NULL,
    MarketName    VARCHAR(120)  NULL,
    SelectionID   BIGINT        NULL,
    HorseClean    VARCHAR(64)   NOT NULL,
    HorseName     VARCHAR(80)   NULL,
    MarketStatus  VARCHAR(24)   NULL,
    RunnerStatus  VARCHAR(24)   NULL,
    Back1         FLOAT         NULL,
    Back1Size     FLOAT         NULL,
    Lay1          FLOAT         NULL,
    Lay1Size      FLOAT         NULL,
    LastTraded    FLOAT         NULL,
    BSP           FLOAT         NULL,
    TradedVolume  FLOAT         NULL
);
CREATE INDEX IX_BetfairLive_Key
    ON dbo.BetfairLive (RaceDate, VenueClean, HorseClean, SnapshotAt);
END;
"""

INSERT_SQL = """
INSERT INTO dbo.BetfairLive
 (SnapshotAt, RaceDate, StartUTC, VenueClean, Venue, MarketID, MarketName,
  SelectionID, HorseClean, HorseName, MarketStatus, RunnerStatus, Back1,
  Back1Size, Lay1, Lay1Size, LastTraded, BSP, TradedVolume)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""



def clean_name(s):
    return "".join(c for c in str(s or "").lower() if c.isalnum())


def snapshot(date_str, token=None):
    """Store the current back/lay for every runner of every market of the day."""
    t0 = time.time()
    mk = markets(date_str, token)
    if not mk:
        print(f"  no UK/IRE win markets returned for {date_str}")
        return 0
    print(f"  {len(mk)} markets found")
    conn = pyodbc.connect(CONN, autocommit=True)
    cur = conn.cursor()
    cur.execute(DDL)
    snap = dt.datetime.now().replace(microsecond=0)
    rows = 0
    for i in range(0, len(mk), 10):
        chunk = mk[i:i + 10]
        for b in books([m["market_id"] for m in chunk], token,
                       price_data=("EX_BEST_OFFERS",), batch=10):
            meta = next((m for m in chunk
                         if m["market_id"] == b.get("marketId")), None)
            if not meta:
                continue
            try:
                start_utc = dt.datetime.fromisoformat(meta["start_utc"])
            except Exception:
                start_utc = None
            for s in b.get("runners") or []:
                sid = str(s.get("selectionId"))
                hname = meta["runners"].get(sid)
                ex = s.get("ex") or {}
                bk = ex.get("availableToBack") or []
                ly = ex.get("availableToLay") or []
                bk = bk[0] if bk else {}
                ly = ly[0] if ly else {}
                cur.execute(INSERT_SQL, (
                    snap, date_str, start_utc, clean_name(meta["venue"]),
                    meta["venue"], meta["market_id"], meta["name"], sid,
                    clean_name(hname), hname, b.get("status"), s.get("status"),
                    bk.get("price"), bk.get("size"), ly.get("price"),
                    ly.get("size"), s.get("lastPriceTraded"),
                    s.get("bsp"), s.get("totalMatched")))
                rows += 1
        time.sleep(0.4)
    conn.close()
    print(f"  stored {rows:,} runner prices -> PRODB.dbo.BetfairLive "
          f"({time.time()-t0:.0f}s)")
    return rows


SP_INSERT = """
INSERT INTO dbo.BFSP
 (RaceDate, RaceTime, CourseClean, HorseClean, BSP_TRUE, IPMin, IPMax,
  MorningWAP, PPWAP, PPMax, PPMin, IPTradedVol, PPTradedVol, WinLose, EventID,
  Source)
VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?,NULL,?)
"""


def sp(date_str, token=None):
    """Pull finished BSPs into dbo.BFSP (Source='API').

    Race times are read from dbo.BookOdds so they match everything else
    (Betfair sends UTC; our tables are UK local).  This is the same number as
    the next-day public file, available the same evening.
    """
    mk = markets(date_str, token)
    if not mk:
        print(f"  no markets for {date_str}")
        return 0
    conn = pyodbc.connect(CONN, autocommit=True)
    cur = conn.cursor()
    times = {}
    try:
        cur.execute("SELECT CourseClean, HorseClean, RaceTime FROM dbo.BookOdds "
                    "WHERE RaceDate = ?", (date_str,))
        times = {(clean_name(c), h): t for c, h, t in cur.fetchall()}
    except pyodbc.Error:
        pass

    written = closed = 0
    for i in range(0, len(mk), 20):
        chunk = mk[i:i + 20]
        for b in books([m["market_id"] for m in chunk], token,
                       price_data=("SP_AVAILABLE", "SP_TRADED"), batch=20):
            meta = next((m for m in chunk
                         if m["market_id"] == b.get("marketId")), None)
            if not meta or b.get("status") != "CLOSED":
                continue
            closed += 1
            venue = clean_name(meta["venue"])
            for s in b.get("runners") or []:
                bsp = s.get("bsp")
                if not bsp or float(bsp) <= 1.0:
                    continue
                hname = meta["runners"].get(str(s.get("selectionId")))
                hc = clean_name(hname)
                rt = times.get((venue, hc))
                if rt is None:
                    continue
                won = 1 if s.get("status") == "WINNER" else 0
                cur.execute("DELETE FROM dbo.BFSP WHERE RaceDate=? AND "
                            "CourseClean=? AND HorseClean=? AND Source='API'",
                            (date_str, venue, hc))
                cur.execute(SP_INSERT, (date_str, rt, venue, hc, float(bsp),
                                        won, "API"))
                written += 1
        time.sleep(0.4)
    conn.close()
    print(f"  {closed} settled markets, {written:,} BSP rows written to "
          f"dbo.BFSP for {date_str}")
    if written:
        print(f"  now run:  python scripts\\book_odds.py report {date_str} "
              "--book BEST")
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["login", "markets", "snapshot", "sp"])
    ap.add_argument("date", nargs="?", default=None)
    a = ap.parse_args()
    day = a.date or dt.date.today().isoformat()
    try:
        if a.mode == "login":
            login()
            print("  credentials work.")
        elif a.mode == "markets":
            ms = markets(day)
            print(f"  {len(ms)} markets")
            for m in ms[:40]:
                print("  %-18s %-14s %s  %2d runners"
                      % (m["start_utc"][:16].replace("T", " "),
                         str(m["venue"])[:14], str(m["name"])[:34],
                         len(m["runners"])))
        elif a.mode == "snapshot":
            snapshot(day)
        else:
            sp(day)
    except BetfairError as e:
        print(f"  BETFAIR ERROR: {e}")
        print("\n  Fix the credentials, then retry:  "
              "python scripts\\betfair_creds.py --diagnose")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
