"""
BETFAIR EXCHANGE EACH-WAY SERVICE & EDGE ESTIMATOR
==================================================
Calculates the synthetic Exchange Each-Way market (Win Back/Lay + Place Back/Lay)
using Betfair's API and estimates how far bookmaker odds deviate from fair exchange
pricing.

Provides:
  - Bookmaker EW vs Exchange EW edge percentage
  - Place-only exploit edge percentage (bad each-way race analysis)
  - Racecard-level and day-wide scanner views
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

LOGIN_URL = "https://identitysso.betfair.com/api/login"
API_BASE = "https://api.betfair.com/exchange/betting/rest/v1.0/{}"
# gettempdir() rather than %TEMP%: this app also runs on Streamlit Cloud (Linux),
# where TEMP is unset and the token would otherwise be written into the repo root.
TOKEN_CACHE = os.path.join(tempfile.gettempdir(), "bf_token.json")


def get_credential(name: str, default: str = "") -> str:
    """Retrieve credential from Streamlit session, secrets, environment, or config file."""
    # 1. Check Streamlit session_state if available
    try:
        if "streamlit" in sys.modules:
            import streamlit as st
            if hasattr(st, "session_state") and "betfair_creds" in st.session_state:
                creds = st.session_state["betfair_creds"]
                if isinstance(creds, dict) and creds.get(name.lower()):
                    return str(creds[name.lower()]).strip()
    except Exception:
        pass

    # 2. Check Streamlit secrets if available
    try:
        if "streamlit" in sys.modules:
            import streamlit as st
            if hasattr(st, "secrets"):
                keys_to_try = [
                    name,
                    name.lower(),
                    name.upper(),
                    f"BETFAIR_{name.upper()}",
                    f"betfair_{name.lower()}",
                ]
                for k in keys_to_try:
                    if k in st.secrets:
                        return str(st.secrets[k]).strip()
                if "betfair" in st.secrets and isinstance(st.secrets["betfair"], dict):
                    for k in [name, name.lower(), name.upper()]:
                        if k in st.secrets["betfair"]:
                            return str(st.secrets["betfair"][k]).strip()
    except Exception:
        pass

    # 3. Check environment variables
    for env_key in [f"BETFAIR_{name.upper()}", name.upper(), name]:
        val = os.environ.get(env_key, "")
        if val:
            return val.strip()

    # 4. Check JSON config files
    candidates = [
        os.environ.get("BETFAIR_CONFIG", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "betfair_api_config.json"),
        r"E:\CGMBET\betfair_api_config.json",
        os.path.join(os.environ.get("APPDATA", ""), "racing-odds", "betfair.json"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cfg = json.load(f)
                match_val = cfg.get(name.lower()) or cfg.get(name.upper())
                if match_val:
                    return str(match_val).strip()
            except Exception:
                pass

    return default


def is_configured() -> bool:
    """Check if minimum required credentials exist."""
    app_key = get_credential("app_key")
    user = get_credential("username")
    pw = get_credential("password")
    return bool(app_key and user and pw)


def login(use_cache: bool = True) -> str:
    """Authenticate with Betfair and return a session token."""
    if use_cache and os.path.isfile(TOKEN_CACHE):
        try:
            with open(TOKEN_CACHE, encoding="utf-8") as f:
                c = json.load(f)
            age = time.time() - c.get("at", 0)
            if c.get("token") and age < 20 * 3600:
                return str(c["token"])
        except Exception:
            pass

    app_key = get_credential("app_key")
    user = get_credential("username")
    pw = get_credential("password")
    if not (app_key and user and pw):
        raise RuntimeError("Missing Betfair credentials. Ensure BETFAIR_APP_KEY, BETFAIR_USERNAME, and BETFAIR_PASSWORD are set.")

    body = urllib.parse.urlencode({"username": user, "password": pw}).encode()
    req = urllib.request.Request(
        LOGIN_URL,
        data=body,
        headers={
            "X-Application": app_key,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8", "ignore"))

    if payload.get("status") != "SUCCESS":
        raise RuntimeError(f"Betfair login rejected: {payload.get('status')} {payload.get('error', '')}")

    token = str(payload.get("token"))
    try:
        with open(TOKEN_CACHE, "w", encoding="utf-8") as f:
            json.dump({"token": token, "at": time.time()}, f)
    except OSError:
        pass
    return token


def call(method: str, payload: dict[str, Any], token: str | None = None) -> Any:
    """POST to Betfair Exchange API-NG endpoint."""
    tok = token or login()
    app_key = get_credential("app_key")
    req = urllib.request.Request(
        API_BASE.format(method),
        data=json.dumps(payload).encode(),
        headers={
            "X-Application": app_key,
            "X-Authentication": tok,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


_CATALOGUE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def fetch_today_catalogue(date_str: str, token: str | None = None, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Fetch all WIN and PLACE market catalogues for today in GB and IE (cached in memory for 5 mins)."""
    now = time.time()
    if not force_refresh and date_str in _CATALOGUE_CACHE:
        cache_time, cached_cat = _CATALOGUE_CACHE[date_str]
        if now - cache_time < 300:
            return cached_cat
    d = dt.date.fromisoformat(date_str)
    from_iso = f"{d.isoformat()}T00:00:00Z"
    to_iso = f"{(d + dt.timedelta(days=1)).isoformat()}T00:00:00Z"

    payload = {
        "filter": {
            "eventTypeIds": ["7"],
            "marketCountries": ["GB", "IE"],
            "marketTypeCodes": ["WIN", "PLACE"],
            "marketStartTime": {"from": from_iso, "to": to_iso},
        },
        "maxResults": "1000",
        "marketProjection": ["EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME", "MARKET_DESCRIPTION"],
    }
    res = call("listMarketCatalogue/", payload, token)
    if res:
        _CATALOGUE_CACHE[date_str] = (now, res)
    return res or []


def fetch_market_books(market_ids: list[str], token: str | None = None) -> list[dict[str, Any]]:
    """Fetch order books with best back & lay prices."""
    if not market_ids:
        return []
    out: list[dict[str, Any]] = []
    batch_size = 25
    for i in range(0, len(market_ids), batch_size):
        chunk = market_ids[i:i + batch_size]
        payload = {
            "marketIds": chunk,
            "priceProjection": {"priceData": ["EX_BEST_OFFERS"], "virtualise": True},
            "orderProjection": "ALL",
        }
        res = call("listMarketBook/", payload, token)
        if res:
            out.extend(res)
        if i + batch_size < len(market_ids):
            time.sleep(0.05)
    return out


def normalize_name(name: str) -> str:
    """Normalize horse name by removing country codes, punctuation, and extra spaces."""
    s = re.sub(r"\([^)]*\)", "", name)
    s = re.sub(r"[^a-zA-Z0-9\s]", "", s)
    return s.strip().lower()


def parse_book_prices(book: dict[str, Any], catalogue: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract back and lay prices keyed by normalized runner name."""
    res: dict[str, dict[str, Any]] = {}
    if not book:
        return res
    runners_meta = catalogue.get("runners", [])
    id_to_name = {str(r.get("selectionId")): r.get("runnerName", "") for r in runners_meta}

    for r in book.get("runners", []):
        sid = str(r.get("selectionId"))
        raw_name = id_to_name.get(sid, sid)
        clean_name = normalize_name(raw_name)
        ex = r.get("ex", {})
        backs = ex.get("availableToBack", [])
        lays = ex.get("availableToLay", [])
        res[clean_name] = {
            "selection_id": sid,
            "runner_name": raw_name,
            "back": backs[0]["price"] if backs else None,
            "lay": lays[0]["price"] if lays else None,
        }
    return res


def compute_race_ew_comparison(
    course_name: str,
    course_slug: str,
    hhmm: str,
    time_str: str,
    runners: list[dict[str, Any]],
    odds_map: dict[Any, Any],
    catalogue: list[dict[str, Any]],
    token: str | None = None,
    prefetched_books: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Compute Exchange Each-Way market comparisons for a given race."""
    c_clean = course_name.lower().strip()
    r_clean_names = [normalize_name(r.get("horse_name", "")) for r in runners]
    r_name_set = set(r_clean_names)

    # Filter markets for this course
    course_markets = [
        m for m in catalogue
        if (m.get("event", {}).get("venue") or "").lower().strip() in c_clean
        or c_clean in (m.get("event", {}).get("venue") or "").lower().strip()
    ]

    win_candidates = [m for m in course_markets if m.get("description", {}).get("marketType") == "WIN"]
    place_candidates = [m for m in course_markets if m.get("description", {}).get("marketType") == "PLACE"]

    best_win_m = None
    best_score = 0
    for wm in win_candidates:
        bf_names = {normalize_name(r.get("runnerName", "")) for r in wm.get("runners", [])}
        overlap = len(r_name_set.intersection(bf_names))
        if overlap >= 3 and overlap > best_score:
            best_score = overlap
            best_win_m = wm

    if not best_win_m:
        return []

    # Find matching PLACE market
    w_eid = best_win_m.get("event", {}).get("id")
    w_st = best_win_m.get("marketStartTime")
    best_place_m = next(
        (pm for pm in place_candidates if pm.get("event", {}).get("id") == w_eid and pm.get("marketStartTime") == w_st),
        None,
    )

    if not best_place_m:
        return []

    # Fetch books
    if prefetched_books is not None:
        win_book = prefetched_books.get(best_win_m["marketId"])
        place_book = prefetched_books.get(best_place_m["marketId"])
    else:
        books = fetch_market_books([best_win_m["marketId"], best_place_m["marketId"]], token=token)
        win_book = next((b for b in books if b["marketId"] == best_win_m["marketId"]), None)
        place_book = next((b for b in books if b["marketId"] == best_place_m["marketId"]), None)

    if not (win_book and place_book):
        return []

    win_prices = parse_book_prices(win_book, best_win_m)
    place_prices = parse_book_prices(place_book, best_place_m)

    # Calculate bookmaker terms
    num_runners = len(runners)
    place_fraction = 0.25 if num_runners >= 8 else 0.20

    rows: list[dict[str, Any]] = []
    for run in runners:
        h_name = run.get("horse_name", "")
        h_clean = normalize_name(h_name)
        raw_rid = run.get("runner_id")
        quotes: list[dict[str, Any]] = []
        if raw_rid is not None and raw_rid in odds_map:
            quotes = list(odds_map[raw_rid])
        elif raw_rid is not None and str(raw_rid) in odds_map:
            quotes = list(odds_map[str(raw_rid)])
        valid_quotes = [q for q in quotes if q.get("decimal") and q["decimal"] > 1.0]
        if not valid_quotes:
            continue
        best_q = max(valid_quotes, key=lambda x: x["decimal"])
        best_win = round(float(best_q["decimal"]), 2)
        bookie = str(best_q.get("bookmaker_name", "-"))

        # Bookmaker place decimal based on standard terms
        book_pl = round(1.0 + (best_win - 1.0) * place_fraction, 2)
        book_ew_total = round(best_win + book_pl, 2)

        # Match Betfair prices
        bf_w = next((v for k, v in win_prices.items() if k in h_clean or h_clean in k), None)
        bf_p = next((v for k, v in place_prices.items() if k in h_clean or h_clean in k), None)

        w_back = bf_w.get("back") if bf_w else None
        w_lay = bf_w.get("lay") if bf_w else None
        p_back = bf_p.get("back") if bf_p else None
        p_lay = bf_p.get("lay") if bf_p else None

        ew_edge: float | None = None
        pl_edge: float | None = None
        win_edge: float | None = None
        verdict = "Neutral"

        if w_lay and p_lay:
            ex_lay_total = w_lay + p_lay
            ew_edge = round(((book_ew_total / ex_lay_total) - 1.0) * 100, 1)
            pl_edge = round(((book_pl / p_lay) - 1.0) * 100, 1)
            win_edge = round(((best_win / w_lay) - 1.0) * 100, 1)

            if ew_edge >= 5.0:
                verdict = "🚀 Super EW Value"
            elif ew_edge > 0.0:
                verdict = "🟢 Positive Edge"
            elif pl_edge >= 8.0:
                verdict = "🎯 Place Exploit"
            elif ew_edge >= -10.0:
                verdict = "🟡 Fair Market"
            else:
                verdict = "🔴 Underpriced"

        rows.append({
            "Race": f"{time_str} {course_name}",
            "course_slug": course_slug,
            "hhmm": hhmm,
            "Horse": h_name,
            "Bookmaker": bookie,
            "Book_Win": best_win,
            "Book_Place": book_pl,
            "Book_EW_Total": book_ew_total,
            "BF_Win_Back": w_back,
            "BF_Win_Lay": w_lay,
            "BF_Place_Back": p_back,
            "BF_Place_Lay": p_lay,
            "EW_Edge": ew_edge,
            "Place_Edge": pl_edge,
            "Win_Edge": win_edge,
            "Verdict": verdict,
        })

    return rows


def ew_verdict(ew_edge: float | None, pl_edge: float | None) -> str:
    """Same thresholds the live scanner uses, kept in one place."""
    if ew_edge is None:
        return "Neutral"
    if ew_edge >= 5.0:
        return "🚀 Super EW Value"
    if ew_edge > 0.0:
        return "🟢 Positive Edge"
    if pl_edge is not None and pl_edge >= 8.0:
        return "🎯 Place Exploit"
    if ew_edge >= -10.0:
        return "🟡 Fair Market"
    return "🔴 Underpriced"


def ew_metrics(book_win: float, book_place: float, win_lay, place_lay):
    """Bookmaker each-way against the exchange lays.

    Mirrors the maths in compute_race_ew_comparison so a scan rebuilt offline
    from a morning snapshot lands on the same numbers as a live scan.
    """
    if not (win_lay and place_lay):
        return None, None, None, "Neutral"
    ew_edge = round((((book_win + book_place) / (win_lay + place_lay)) - 1.0) * 100, 1)
    pl_edge = round(((book_place / place_lay) - 1.0) * 100, 1)
    win_edge = round(((book_win / win_lay) - 1.0) * 100, 1)
    return ew_edge, pl_edge, win_edge, ew_verdict(ew_edge, pl_edge)


def ew_rows_from_snapshot(payload: dict[str, Any], meeting_filter: str = "All Meetings Today",
                          label: str = "") -> list[dict[str, Any]]:
    """Rebuild the Exchange EW scan from a morning snapshot.

    morning_capture.py already records, for every runner: the best bookmaker win
    price, that bookmaker's each-way places and denominator, the bookmaker place
    price, and Betfair's win/place prices. That is everything the scanner needs -
    so the app can show today's each-way value WITHOUT a Betfair key of its own,
    using whatever the workflow last committed.
    """
    rows: list[dict[str, Any]] = []
    for race in payload.get("races", []):
        course = str(race.get("course") or "")
        if meeting_filter and meeting_filter != "All Meetings Today":
            if meeting_filter.lower() not in course.lower():
                continue
        time_str = str(race.get("time") or race.get("hhmm") or "")
        for run in race.get("runners", []):
            try:
                book_win = float(run.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if book_win <= 1.0:
                continue
            denom = run.get("denominator") or 0
            book_place = run.get("place_price")
            if not book_place and denom:
                book_place = round(1.0 + (book_win - 1.0) / float(denom), 2)
            try:
                book_place = float(book_place or 0)
            except (TypeError, ValueError):
                continue
            if book_place <= 1.0:
                continue
            # A true place LAY is what the edge maths wants.  Snapshots taken
            # before the dual-sided capture only hold a place BACK price, so we
            # use it and mark the row indicative rather than overstate the edge
            # silently.  From the next capture onwards real lays are present.
            win_lay = run.get("bf_win_lay") or run.get("bf_win")
            place_lay = run.get("bf_place_lay")
            place_used = place_lay or run.get("bf_place_back") or run.get("bf_place")
            exact = bool(run.get("bf_win_lay") and place_lay)
            basis = "lay" if exact else ("indicative" if place_used else "none")
            ew_edge, pl_edge, win_edge, verdict = ew_metrics(
                book_win, book_place, win_lay, place_used)
            if not exact:
                # a back price is not a lay: it would flag 228 of 575 runners as
                # "place exploits".  Show nothing rather than a number that
                # cannot be acted on.
                pl_edge = None
                if verdict != "Neutral":
                    verdict = f"{verdict} (indicative)"
            rows.append({
                "Race": f"{time_str} {course}".strip(),
                "course_slug": race.get("course_slug"),
                "hhmm": race.get("hhmm"),
                "Horse": run.get("horse"),
                "Bookmaker": run.get("bookmaker") or "-",
                "Book_Win": round(book_win, 2),
                "Book_Place": round(book_place, 2),
                "Book_EW_Total": round(book_win + book_place, 2),
                "EW_Places": run.get("places"),
                "EW_Denominator": denom,
                "BF_Win_Back": run.get("bf_win_back"),
                "BF_Win_Lay": win_lay,
                "BF_Place_Back": run.get("bf_place_back") or run.get("bf_place"),
                # the price the edge was actually computed from, so the table
                # never shows a number that differs from the maths
                "BF_Place_Lay": place_used,
                "EW_Edge": ew_edge,
                "Place_Edge": pl_edge,
                "Win_Edge": win_edge,
                "Verdict": verdict,
                "Snapshot": label,
                "Book_Basis": basis,
            })
    rows.sort(key=lambda r: (r["EW_Edge"] if r["EW_Edge"] is not None else -999), reverse=True)
    return rows


def load_snapshot_rows(snap_dir: str, meeting_filter: str = "All Meetings Today"):
    """Newest odds_*.json in snap_dir -> (rows, label).  No network calls."""
    import glob
    files = sorted(glob.glob(os.path.join(snap_dir, "odds_*.json")))
    if not files:
        return [], ""
    newest = files[-1]
    try:
        with open(newest, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return [], ""
    label = os.path.basename(newest).replace("odds_", "").replace(".json", "")
    return ew_rows_from_snapshot(payload, meeting_filter, label), label


def scan_day_ew_edges(
    date_str: str,
    meeting_filter: str = "All Meetings Today",
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Scan all UK/IRE races today and return ranked Each-Way edge opportunities."""
    try:
        import rtv_api
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import rtv_api  # type: ignore

    day_races = rtv_api.day_races(date_str)
    uk_races = [r for r in day_races if r.get("country") in ("GBR", "IRE", "GB", "IE", "UK", None)]
    if meeting_filter != "All Meetings Today":
        uk_races = [r for r in uk_races if r.get("course_name") == meeting_filter]

    if not uk_races:
        return []

    tok = token or login()
    catalogue = fetch_today_catalogue(date_str, tok)
    if not catalogue:
        return []

    # Bulk pre-fetch all market books in fast batches
    target_mkts = [m for m in catalogue if m.get("description", {}).get("marketType") in ("WIN", "PLACE")]
    m_ids = [m["marketId"] for m in target_mkts]
    all_books = fetch_market_books(m_ids, token=tok)
    book_by_id = {b["marketId"]: b for b in all_books}

    all_rows: list[dict[str, Any]] = []
    for race in uk_races:
        c_name = str(race.get("course_name") or "")
        c_slug = str(race.get("course_slug") or "")
        hhmm = str(race.get("hhmm") or "")
        time_str = str(race.get("time") or "")

        try:
            detail = rtv_api.race_detail(date_str, c_slug, hhmm)
            runners = rtv_api.runners_of(detail)
            if not runners:
                continue
            rids = [str(x["runner_id"]) for x in runners if x.get("runner_id")]
            odds_res, _ = rtv_api.runner_odds(rids)
            odds_map = odds_res or {}
            race_rows = compute_race_ew_comparison(
                course_name=c_name,
                course_slug=c_slug,
                hhmm=hhmm,
                time_str=time_str,
                runners=runners,
                odds_map=odds_map,
                catalogue=catalogue,
                token=tok,
                prefetched_books=book_by_id,
            )
            all_rows.extend(race_rows)
        except Exception:
            continue

    all_rows.sort(
        key=lambda x: (
            x.get("EW_Edge") is not None,
            x.get("EW_Edge") if x.get("EW_Edge") is not None else -999.0,
        ),
        reverse=True,
    )
    return all_rows

