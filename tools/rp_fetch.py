#!/usr/bin/env python3
"""
rp_fetch.py  -  pull race results straight from Racing Post.

This is the answer to "how did they build the files": the raceform database is a
normalised extract of Racing Post results. Your `race_id` IS the RP race id, and

    day  : https://www.racingpost.com/results/<YYYY-MM-DD>/
    race : https://www.racingpost.com/results/<courseUid>/<courseKey>/<date>/<raceUid>/

both serve a __NEXT_DATA__ JSON blob holding every runner. This module fetches
those pages and caches the raw JSON so nothing is lost and nothing is refetched.

Racing Post's WAF returns HTTP 406 unless the request carries a full Chrome header
set (Sec-Fetch-*, Upgrade-Insecure-Requests and Accept-Encoding: gzip, deflate, br).

USAGE
    python rp_fetch.py --date 2026-06-04
    python rp_fetch.py --from 2026-06-04 --to 2026-06-10
    python rp_fetch.py --date 2026-06-04 --raw      # dump one race JSON and stop
"""

import argparse
import datetime as dt
import gzip
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import zlib

BASE = os.environ.get("RP_BASE") or r"D:\Mydata"
# On a machine that is not the archive host (e.g. a GitHub Actions runner) point
# RP_BASE at a scratch directory.  Nothing here needs the archive database -
# day_races()/race_result() are plain HTTP - so any writable dir will do.
try:
    os.makedirs(BASE, exist_ok=True)
except Exception:
    pass
CACHE = os.path.join(BASE, "_rp_cache")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

DELAY = float(os.environ.get("RP_DELAY") or 4.0)
                     # Racing Post rate-limits aggressively; the global cooldown in
                     # cooldown() backs everyone off further whenever a 429 appears.
THROTTLE_FILE = os.path.join(BASE, "_rp_throttle.lock")
_LAST = [0.0]


def rule(t=""):
    print("\n" + "=" * 74)
    if t:
        print(t)
        print("=" * 74)


def _read_throttle():
    try:
        with open(THROTTLE_FILE) as f:
            d = json.load(f)
        return float(d.get("last", 0)), float(d.get("cool", 0))
    except Exception:
        return 0.0, 0.0


def _write_throttle(last=None, cool=None):
    cur_last, cur_cool = _read_throttle()
    try:
        with open(THROTTLE_FILE, "w") as f:
            json.dump({"last": cur_last if last is None else last,
                       "cool": cur_cool if cool is None else cool}, f)
    except Exception:
        pass


def cooldown(seconds, why="429"):
    """Park EVERY process for a while. Racing Post's limiter is per-IP, so a backoff
    that only applies to one request just burns retries against the same wall."""
    until = time.time() + seconds
    _write_throttle(cool=until)
    print("      [%s] GLOBAL cooldown %.0fs - all workers pausing" % (why, seconds), flush=True)
    time.sleep(seconds)


def _throttle():
    """Rate limit that works ACROSS processes, and honours any global cooldown."""
    global _LAST
    want = max(DELAY, 0.3)
    for _ in range(100000):
        now = time.time()
        disk_last, cool = _read_throttle()
        if cool > now:
            time.sleep(min(cool - now, 5.0))
            continue
        last = max(_LAST[0], disk_last)
        wait = want - (now - last)
        if wait <= 0:
            _LAST[0] = now
            _write_throttle(last=now)
            return
        time.sleep(min(wait, 1.0) + random.uniform(0, 0.15))


def fetch(url, tries=7):
    """GET with Chrome headers; decodes gzip/br/deflate.

    Retries hard on 406/429/5xx - Racing Post's limiter returns 429 when we push too
    fast, and a short backoff is not enough to clear it.
    """
    last = None
    for attempt in range(1, tries + 1):
        _throttle()
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                enc = (r.headers.get("Content-Encoding") or "").lower()
                final = r.geturl()
                code = r.status
            if enc == "br":
                import brotli
                raw = brotli.decompress(raw)
            elif enc == "gzip":
                raw = gzip.decompress(raw)
            elif enc == "deflate":
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            return code, final, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 and attempt < tries:
                # park everyone - the limiter is per-IP, not per-request
                cooldown(min(900.0, 60.0 * (2 ** (attempt - 1))) + random.uniform(0, 10))
                continue
            if e.code in (406, 500, 502, 503, 504) and attempt < tries:
                back = min(120.0, 5.0 * attempt) + random.uniform(0, 3)
                print("      [%s] retry in %.0fs (attempt %d/%d)" % (e.code, back, attempt, tries))
                time.sleep(back)
                continue
            raise
        except Exception as e:
            last = e
            if attempt < tries:
                time.sleep(min(30.0, 3.0 * attempt))
                continue
            raise
    if last:
        raise last
    raise RuntimeError("unreachable")


NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def next_state(html):
    m = NEXT_DATA.search(html)
    if not m:
        return None
    return json.loads(m.group(1))["props"]["pageProps"].get("initialState", {}) or {}


def day_races(date):
    """Fetch /results/<date>/ -> list of races that have a result, with metadata."""
    code, final, html = fetch("https://www.racingpost.com/results/%s/" % date)
    st = next_state(html)
    if st is None:
        return [], None
    out = []
    seen = set()
    for course in ((st.get("results") or {}).get("data") or []):
        for race in course.get("races", []):
            if not race.get("isResult") or race.get("isAbandoned"):
                continue
            uid = race.get("raceUid")
            if uid in seen:            # the "Worldwide Stakes" pseudo-meeting repeats races
                continue
            seen.add(uid)
            out.append({
                "courseUid": race.get("courseUid") or course.get("courseId"),
                "courseKey": race.get("courseKey") or (course.get("courseName") or "").lower(),
                "date": date,
                "raceUid": race.get("raceUid"),
                "courseName": course.get("courseName"),
                "countryCode": course.get("countryCode"),
                "meetingGoing": course.get("meetingGoing"),
                "dayRaceMeta": race,
            })
    return out, st


def race_result(entry, use_cache=True):
    """Fetch one race result page -> raceResult.data (raw JSON cached on disk)."""
    uid, cid, key, date = entry["raceUid"], entry["courseUid"], entry["courseKey"], entry["date"]
    day_dir = os.path.join(CACHE, date)
    os.makedirs(day_dir, exist_ok=True)
    cache_file = os.path.join(day_dir, "%s.json" % uid)
    if use_cache and os.path.exists(cache_file):
        with open(cache_file, encoding="utf-8") as f:
            return json.load(f), None

    url = "https://www.racingpost.com/results/%s/%s/%s/%s/" % (cid, key, date, uid)
    code, final, html = fetch(url)
    st = next_state(html)
    if st is None:
        return None, html
    data = (st.get("raceResult") or {}).get("data")
    if data:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    return data, html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--from", dest="d0")
    ap.add_argument("--to", dest="d1")
    ap.add_argument("--raw", action="store_true", help="dump the first race JSON and stop")
    args = ap.parse_args()

    dates = []
    if args.date:
        dates = [args.date]
    elif args.d0:
        a = dt.date.fromisoformat(args.d0)
        b = dt.date.fromisoformat(args.d1 or args.d0)
        while a <= b:
            dates.append(a.isoformat())
            a += dt.timedelta(days=1)
    else:
        sys.exit("give --date or --from/--to")

    rule("RACING POST FETCH")
    os.makedirs(CACHE, exist_ok=True)
    print("  cache : %s" % CACHE)
    print("  dates : %d  %s" % (len(dates), ", ".join(dates[:5]) + (" ..." if len(dates) > 5 else "")))

    grand = 0
    for date in dates:
        try:
            races, st = day_races(date)
        except Exception as e:
            print("  %s  DAY FETCH FAILED: %s" % (date, e))
            continue
        print("\n  %s : %d races with results" % (date, len(races)))
        for i, r in enumerate(races, 1):
            try:
                data, html = race_result(r)
            except Exception as e:
                print("     [%2d/%d] race %s FAILED %s" % (i, len(races), r["raceUid"], e))
                continue
            if not data:
                print("     [%2d/%d] race %s no data" % (i, len(races), r["raceUid"]))
                continue
            nrun = len(data.get("runners") or [])
            grand += nrun
            print("     [%2d/%d] %-14s %-6s %-24s runners=%d"
                  % (i, len(races), str(r["courseName"])[:14], data["header"].get("raceTime"),
                     str(data["header"].get("raceTitle"))[:24], nrun))
            if args.raw:
                print(json.dumps(data, indent=1, ensure_ascii=False, default=str)[:6000])
                return
    print("\n  done. total runner records cached: %s" % format(grand, ","))
    print("  raw JSON cached under: %s" % CACHE)


if __name__ == "__main__":
    main()
