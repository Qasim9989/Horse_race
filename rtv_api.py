"""
RACINGTV JSON API CLIENT
========================
RacingTV's racecard is a React app fed by api.racingtv.com.  Every price shown
on the page ("odds courtesy of Oddschecker") comes from a plain JSON endpoint,
so there is no need to scrape the DOM or fight Oddschecker's bot walls.

Endpoints (discovered 2026-09-15, see reports/_o7.log .. _o11.log):
    GET /racing/racecards/list/{YYYY-MM-DD}        whole day, all meetings
    GET /racing/racecards/{date}/{course}/{HHMM}   one race + runner fields
    GET /racing/runner/odds?runner_ids[]=...       every book's price per runner

The API 403s unless the SPA's headers are sent, in particular
`x-requested-with: racingtv-web/<version>`.  `authorization` is sent EMPTY for
anonymous visitors - no API key or login is required.

Odds payload shape (per runner):
  {"id": <runner id>,
   "odds": [{"price": {"decimal": "13.00", "fractional": "12/1",
                       "moneyline": "+1200"},
             "each_way_places": 3, "each_way_denominator": 5,
             "fluctuation_type": "drifting",     # or "shortening"
             "bookmaker_id": 5}, ...]}
Bookmaker id -> name lives in the same payload under "bookmakers".

CLI:
    python scripts/rtv_api.py day  2026-09-15
    python scripts/rtv_api.py race 2026-09-15 punchestown 1340
    python scripts/rtv_api.py odds 2026-09-15 punchestown 1340
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "https://api.racingtv.com"

# RacingTV throttles bursts: after a few hundred quick requests the server
# stops answering existing connections (requests hang until the socket
# timeout) while a fresh process is served instantly.  A global ceiling of
# ~12 requests/second keeps a long backfill sustainable, where 20 threads in
# a burst stalls after ~300 races.
_MIN_GAP = 0.08
_gate = threading.Lock()
_last_call = [0.0]


def _throttle():
    with _gate:
        gap = time.time() - _last_call[0]
        if gap < _MIN_GAP:
            time.sleep(_MIN_GAP - gap)
        _last_call[0] = time.time()

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "authorization": "",
    "x-requested-with": "racingtv-web/5.6.0",
    "referer": "https://www.racingtv.com/",
    "accept": "application/json",
    "content-type": "application/json",
}


def clean_name(s):
    """Same normalisation the rest of the codebase uses for horse/course keys."""
    return "".join(c for c in str(s or "").lower() if c.isalnum())


def get_json(path, retries=3, timeout=12):
    """GET a path (or full URL) from api.racingtv.com and return parsed JSON.

    Rate limited globally (~12 req/s) and fails fast: a 12s socket timeout
    instead of 30s so a throttled connection is retried rather than stalling
    a worker for half a minute.
    """
    url = path if path.startswith("http") else BASE + path
    last = None
    for attempt in range(retries):
        _throttle()
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"{last} for {url}") from e
        except Exception as e:
            last = str(e)
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"failed after {retries} tries: {last} ({url})")


def day_races(date_str, countries=("GBR", "IRE")):
    """All races for a date, sorted by time.

    `countries` filters on the governing body code from the API
    ("GBR", "IRE", other codes are e.g. South Africa / USA); pass None for all.

    Each row: {"date","course_slug","course_name","country","time","hhmm",
               "race_id","title","title_full","track_condition","start_iso",
               "runners_text"}
    """
    j = get_json(f"/racing/racecards/list/{date_str}")
    out = []
    for m in j.get("meetings", []):
        slug = (m.get("track", {}).get("slug") or "").strip("/")
        country = ((m.get("track", {}).get("country_code") or {})
                   .get("racing") or "")
        if countries and country.upper() not in countries:
            continue
        for rc in m.get("races", []):
            iso = rc.get("start_time_scheduled") or ""
            hhmm = iso[11:16].replace(":", "") if len(iso) >= 16 else ""
            out.append({
                "date": m.get("date") or date_str,
                "course_slug": slug,
                "course_name": m.get("track", {}).get("name") or slug,
                "country": country,
                "time": f"{iso[11:13]}:{iso[14:16]}" if len(iso) >= 16 else "",
                "hhmm": hhmm,
                "race_id": rc.get("id"),
                "title": rc.get("title"),
                "title_full": rc.get("title_full"),
                "track_condition": (rc.get("track_condition")
                                    or m.get("track_condition")),
                "start_iso": iso,
                "runners_text": rc.get("runner_count_text"),
            })
    out.sort(key=lambda r: (r["time"], r["course_name"]))
    return out


def race_detail(date_str, course_slug, hhmm):
    """Race metadata + runner rows (clean age/weight/rating/jockey/trainer)."""
    return get_json(f"/racing/racecards/{date_str}/{course_slug}/{hhmm}")


def clean_text(s):
    """Strip the inline markup Timeform uses (e.g. <em>(73)</em> = unrated guess)."""
    return re.sub(r"<[^>]+>", "", str(s or "")).strip()


def runners_of(detail):
    """Flat list of the fields we care about, one dict per runner."""
    rows = []
    for r in detail.get("runners", []):
        rows.append({
            "runner_id": r.get("id"),
            "horse_id": r.get("horse_id"),
            "horse_name": r.get("horse_name"),
            "horse_clean": clean_name(r.get("horse_name")),
            "cloth_number": r.get("cloth_number"),
            "status": (r.get("status") or {}).get("state"),
            "withdrawn": bool(r.get("withdrawn")),
            "reserve": bool(r.get("reserve")),
            "jockey": clean_text(r.get("jockey_name")),
            "trainer": clean_text(r.get("trainer_name")),
            "age": r.get("age"),
            "weight": clean_text(r.get("format_weight")),
            # RacingTV exposes the handicap mark as timeform_original_rating;
            # verified against the card weights (e.g. mark 100 -> 10-0, 92 -> 9-6).
            "official_rating": r.get("timeform_original_rating"),
            "timeform_rating": clean_text(r.get("format_timeform_rating")
                                          or r.get("timeform_rating")),
            "form": clean_text(r.get("form")),
            "days_since_run": r.get("days_since_last_run"),
            "starting_price": r.get("starting_price"),
        })
    return rows


def is_live(runner):
    """A runner that will actually take part (excludes reserves + scratched)."""
    return (runner.get("status") == "entered" and not runner.get("withdrawn")
            and not runner.get("reserve"))


def runner_odds(runner_ids):
    """({runner_id: [rows]}, {bookmaker_id: name}) for the given runner ids.

    Each row: {bookmaker_id, bookmaker_name, decimal, fractional,
               places, denominator, fluctuation}
    """
    q = "&".join(f"runner_ids[]={i}" for i in runner_ids)
    j = get_json("/racing/runner/odds?" + q)
    books = {int(k): v.get("name")
             for k, v in (j.get("bookmakers") or {}).items()}
    out = {}
    for r in j.get("runners", []):
        rows = []
        for o in r.get("odds") or []:
            try:
                dec = float(o["price"]["decimal"])
            except (KeyError, TypeError, ValueError):
                continue
            bid = o.get("bookmaker_id")
            rows.append({
                "bookmaker_id": bid,
                "bookmaker_name": books.get(bid, f"book{bid}"),
                "decimal": dec,
                "fractional": (o.get("price") or {}).get("fractional"),
                "places": o.get("each_way_places"),
                "denominator": o.get("each_way_denominator"),
                "fluctuation": o.get("fluctuation_type"),
            })
        out[r.get("id")] = rows
    return out, books


def best_of(odds_rows):
    """(best decimal, bookmaker name, movement flag) for one runner."""
    if not odds_rows:
        return None, None, None
    top = max(odds_rows, key=lambda o: o["decimal"])
    return top["decimal"], top["bookmaker_name"], top["fluctuation"]


def price_for(odds_rows, book_name):
    """Price a named bookmaker offers for this runner, plus its movement flag."""
    for o in odds_rows:
        if (o["bookmaker_name"] or "").lower() == book_name.lower():
            return o["decimal"], o["fluctuation"]
    return None, None


def overround(decimals):
    """sum(1/price) over the given prices; book margin = value - 1."""
    vals = [d for d in decimals if d and d > 1]
    return sum(1.0 / d for d in vals) if vals else None


def _cli():
    mode = sys.argv[1] if len(sys.argv) > 1 else "day"
    if mode == "day":
        import datetime as _dt
        date_str = sys.argv[2] if len(sys.argv) > 2 else _dt.date.today().isoformat()
        races = day_races(date_str)
        print(f"{len(races)} UK/IRE races on {date_str}")
        for r in races:
            print("  %-8s %-18s %-46s %s  [%s]"
                  % (r["time"], r["course_name"][:18], str(r["title"])[:46],
                     r["runners_text"] or "", r["country"]))
    elif mode == "race":
        _, _mode, date_str, course, hhmm = sys.argv
        d = race_detail(date_str, course, hhmm)
        print("RACE:", d["race"]["title"], "| displays_odds:",
              d["race"].get("displays_odds"))
        for r in runners_of(d):
            print("  %3s %-26s %-20s %-4s %-6s %-6s %s"
                  % (r["cloth_number"], str(r["horse_name"])[:26],
                     str(r["jockey"])[:20], r["age"], r["weight"],
                     r["timeform_rating"] or "-",
                     "RESERVE" if r["reserve"] else r["status"]))
    elif mode == "odds":
        _, _mode, date_str, course, hhmm = sys.argv
        d = race_detail(date_str, course, hhmm)
        rows = runners_of(d)
        odds, books = runner_odds([r["runner_id"] for r in rows])
        print("books in feed:", ", ".join(sorted(books.values())))
        print("%-26s %8s %-14s %-11s %6s"
              % ("HORSE", "BEST", "BEST_BOOK", "MOVE", "BOOKS"))
        for r in rows:
            o = odds.get(r["runner_id"]) or []
            b, bk, mv = best_of(o)
            print("%-26s %8s %-14s %-11s %6d"
                  % (str(r["horse_name"])[:26], f"{b:.2f}" if b else "-",
                     str(bk or "-")[:14], str(mv or "-"), len(o)))
        live = [r for r in rows if is_live(r)]
        for name in [*sorted(books.values()), "BEST-OF-MARKET"]:
            vals = []
            for r in live:
                o = odds.get(r["runner_id"]) or []
                if not o:
                    continue
                v, _ = price_for(o, name)
                if v is None and name == "BEST-OF-MARKET":
                    v = best_of(o)[0]
                if v:
                    vals.append(v)
            ov = overround(vals)
            if ov:
                print("  %-18s overround %.4f (%+.2f%% margin) on %d runners"
                      % (name, ov, (ov - 1) * 100, len(vals)))
    else:
        print(__doc__)


if __name__ == "__main__":
    _cli()

