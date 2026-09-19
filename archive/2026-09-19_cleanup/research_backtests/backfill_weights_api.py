"""
FAST WEIGHT BACKFILL VIA THE RACINGTV JSON API
==============================================
The results pages needed a headless browser (~30 races/min).  The API serves
the same history as JSON - one request per race - and returns more than the
results page did:

    weight ("8-13"), age, jockey, trainer, starting_price,
    timeform_rating, form, days_since_run

Discovered 2026-09-15: /racing/racecards/{date}/{slug}/{HHMM} answers for
HISTORICAL dates (verified back to 2021-01-05, 11/11 runners with weights), so
the five-year rebuild is a plain HTTP pull.

  python scripts/backfill_weights_api.py --from 2021-01-01 --to 2026-09-14
  python scripts/backfill_weights_api.py --from 2021-01-01 --to 2022-12-31 --threads 10

Resumable at race level.  Writes to every target database that still has the
race unfilled, so one pass fills the live DB and SCRAPED_PRODB together.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pyodbc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import contextlib

import rtv_api
from backfill_weights import conn_str, lbs

LOG = os.path.join(ROOT, "reports", "backfill_weights.log")
TARGETS = ("RACINGTV_2023_2026", "SCRAPED_PRODB")
# the old table's text columns are varchar(80): slicing to 90 would fail on a
# long trainer name with "string or binary data would be truncated"
COL_LIMITS = {"SCRAPED_PRODB": {"jockey": 80, "trainer": 80},
              "RACINGTV_2023_2026": {"jockey": 100, "trainer": 100}}
lock = threading.Lock()
# pyodbc connections are not thread-safe: concurrent cursors on one connection
# fail with HY000 ("string or binary data would be truncated" / driver error),
# which silently lost most of the weights on the first run of this script.
db_lock = threading.Lock()
stats = {"dates": 0, "races": 0, "written": 0, "nomatch": 0, "nodata": 0,
         "noname": 0, "writefail": 0}


def log(msg):
    line = f"{dt.datetime.now():%H:%M:%S}  {msg}"
    with lock:
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def norm(s):
    return "".join(c for c in str(s or "").lower() if c.isalnum())


def course_match(db_course, api_race):
    """DB course name vs the API's slug/name.  The scrapers took these from
    RacingTV, so they agree except for suffixes: London courses come back as
    'lingfield-park' / 'kempton-park' while the DB holds 'lingfield'."""
    a = norm(db_course)
    for cand in (norm(api_race["course_slug"]), norm(api_race["course_name"])):
        if not cand:
            continue
        if a == cand or cand.startswith(a) or a.startswith(cand):
            return True
    return False


def norm_name(s):
    """Match key for a horse name.

    Two differences to bridge:
      * the old scraper stripped spaces and punctuation ('BrideysLettuce'
        vs the API's "Bridey's Lettuce")
      * the databases keep the country suffix ('Amenita (IRE)') while the API
        returns 'Amenita' - that alone cost every Irish race its weights

    So drop parenthesised origin tags, then keep alphanumerics only.
    """
    return norm(re.sub(r"\([A-Za-z]{2,4}\)", "", str(s or "")))


def load_names(conn, date_str, d_to=None):
    """{(date, course, hhmm): {normalised_name: exact_name_in_db}}.

    Loaded for the WHOLE range in one query per database: the first version
    queried per (db, date) behind the write lock, so 24 workers each waited
    ~8s on a full scan of the old table before the first write landed - that
    was the five-minute stall before any progress appeared.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT CONVERT(char(10), RaceDate, 120) AS d, CourseName, RaceTime,
               HorseName
        FROM dbo.Scraped_Results
        WHERE RaceDate BETWEEN ? AND ?
          AND (Weight IS NULL OR DistanceYards IS NULL)
    """, (date_str, d_to or date_str))
    out: dict[tuple, dict] = {}
    for d, course, rtime, horse in cur.fetchall():
        hhmm = str(rtime)[:5].replace(":", "")
        out.setdefault((d, course, hhmm), {})[norm_name(horse)] = horse
    return out


def preload_names(conns, d_from, d_to):
    names = {}
    with db_lock:
        for db, conn in conns.items():
            try:
                names[db] = load_names(conn, d_from, d_to)
                log(f"  names loaded {db}: {len(names[db]):,} races")
            except pyodbc.Error as e:
                log(f"  ! names {db}: {str(e)[:120]}")
                names[db] = {}
    return names


def pending_by_date(conns, d_from, d_to):
    """{date_str: [(hhmm, course, {target db names})]} - only what needs work.

    A race is fetched once and written to every target still missing it, so
    the live DB and SCRAPED_PRODB are filled by the same pass.
    """
    merged: dict[str, dict[tuple, list]] = {}
    for db, conn in conns.items():
        cur = conn.cursor()
        cur.execute("""
            SELECT CONVERT(char(10), RaceDate, 120) AS d, RaceTime, CourseName
            FROM dbo.Scraped_Results
            WHERE RaceDate BETWEEN ? AND ?
            GROUP BY CONVERT(char(10), RaceDate, 120), RaceTime, CourseName
            HAVING COUNT(Weight) < COUNT(*) OR COUNT(DistanceYards) < COUNT(*)
        """, (d_from, d_to))
        for d, t, c in cur.fetchall():
            key = (str(t)[:5].replace(":", ""), c)
            slot = merged.setdefault(d, {}).setdefault(key, [key[0], c, set()])
            slot[2].add(db)
    return {d: list(v.values()) for d, v in merged.items()}


def write_race(conns, names, targets, date_str, hhmm, course, runners, race):
    """UPDATE every target that still needs this race (never the others: the
    live DB has no 2021-2022 rows at all, so writing there is pure waste).

    Weight is the goal; Age/JockeyName/TrainerName come free because they are
    empty in both databases.  SP is never touched - the whole BSP study rests
    on the recorded SP staying exactly as it was - so the API's starting price
    goes to its own ApiSP column.

    The race-level fields are the ones Ben's rules were starved of: distance
    (rule 4, proven at the trip) and the handicap band (rules 1-3, the mark
    reconstruction where PRODB's marks stop).
    """
    dist = race.get("distance")
    dist_txt = race.get("distance_formatted") or race.get("distance_text")
    band = race.get("rating_limit") or {}
    band_top = band.get("end") if isinstance(band, dict) else None
    band_txt = race.get("rating_limit_range")
    rclass = race.get("race_class")
    rtype = race.get("race_type")
    n = 0
    with db_lock:
        for db in targets:
            conn = conns.get(db)
            if conn is None:
                continue
            lookup = (names.get(db) or {}).get((date_str, course, hhmm))
            if not lookup:
                continue
            lim = COL_LIMITS.get(db, {})
            rows = []
            for r in runners:
                w = lbs(r.get("weight"))
                if w is None:
                    continue
                exact = lookup.get(norm_name(r.get("horse_name")))
                if not exact:
                    with lock:
                        stats["noname"] += 1
                    continue
                sp = r.get("starting_price") or {}
                rows.append((
                    f"{w // 14}-{w % 14}",
                    str(r.get("age")) if r.get("age") else None,
                    str(r.get("jockey") or "")[:lim.get("jockey", 90)] or None,
                    str(r.get("trainer") or "")[:lim.get("trainer", 90)] or None,
                    int(dist) if dist else None,
                    str(dist_txt)[:20] if dist_txt else None,
                    int(band_top) if band_top else None,
                    str(band_txt)[:20] if band_txt else None,
                    str(rclass)[:10] if rclass is not None else None,
                    str(rtype)[:30] if rtype else None,
                    str(r.get("timeform_rating"))[:16]
                    if r.get("timeform_rating") else None,
                    str(r.get("form"))[:30] if r.get("form") else None,
                    int(r["days_since_run"])
                    if isinstance(r.get("days_since_run"), int) else None,
                    float(sp["decimal"]) if sp.get("decimal") else None,
                    date_str, hhmm, course, exact))
            if not rows:
                continue
            cur = conn.cursor()
            try:
                # one round trip per race instead of one commit per runner:
                # per-row autocommit was most of the runtime
                cur.executemany(
                    "UPDATE dbo.Scraped_Results SET Weight=?, "
                    "Age=COALESCE(Age, ?), JockeyName=COALESCE(JockeyName, ?), "
                    "TrainerName=COALESCE(TrainerName, ?), "
                    "DistanceYards=COALESCE(DistanceYards, ?), "
                    "DistanceText=COALESCE(DistanceText, ?), "
                    "RatingBandTop=COALESCE(RatingBandTop, ?), "
                    "RatingBandText=COALESCE(RatingBandText, ?), "
                    "RaceClass=COALESCE(RaceClass, ?), "
                    "RaceType=COALESCE(RaceType, ?), "
                    "TimeformRating=COALESCE(TimeformRating, ?), "
                    "FormText=COALESCE(FormText, ?), "
                    "DaysSinceRun=COALESCE(DaysSinceRun, ?), "
                    "ApiSP=COALESCE(ApiSP, ?) "
                    "WHERE RaceDate=? AND RaceTime=? AND CourseName=? "
                    "AND HorseName=?", rows)
                n += len(rows)
            except pyodbc.Error:
                # full text: HY000 is generic, the detail is in the rest of the
                # tuple ("connection is busy", truncation)
                for row in rows:
                    try:
                        cur.execute(
                            "UPDATE dbo.Scraped_Results SET Weight=?, "
                            "Age=COALESCE(Age, ?), "
                            "JockeyName=COALESCE(JockeyName, ?), "
                            "TrainerName=COALESCE(TrainerName, ?) "
                            "WHERE RaceDate=? AND RaceTime=? AND CourseName=? "
                            "AND HorseName=?", row)
                        n += max(cur.rowcount, 0)
                    except pyodbc.Error as e2:
                        with lock:
                            stats["writefail"] += 1
                            if stats["writefail"] < 4:
                                log(f"  ! write {db} {date_str} {course} "
                                    f"{hhmm}: "
                                    f"{' | '.join(str(x) for x in e2.args)[:250]}"
                                    )
    return n


_name_cache: dict[tuple, dict] = {}


def get_names(db, date_str, conns):
    """Cached name map for (db, date): 74k races over 1,900 dates means the
    same date would otherwise be queried once per race."""
    key = (db, date_str)
    with db_lock:
        if key not in _name_cache:
            try:
                _name_cache[key] = load_names(conns[db], date_str)
            except pyodbc.Error as e:
                log(f"  ! names {db} {date_str}: {str(e)[:50]}")
                _name_cache[key] = {}
        return _name_cache[key]


def hhmm_minutes(hhmm):
    """'1420' -> 860 minutes, for time-distance comparisons."""
    s = str(hhmm).zfill(4)
    return int(s[:2]) * 60 + int(s[2:4])


def match_api_race(api_races, hhmm, course, used, tolerance=25):
    """Find the API race behind a database race.

    Exact time is tried first.  If that fails, allow +/-`tolerance` minutes:
    RacingTV reschedules meetings, so the database holds Limerick at 13:45
    where the API now lists 13:55 - the same race, and the only reason those
    races were left without weights.  An API race is never assigned twice.
    """
    cands = [r for r in api_races if course_match(course, r)
             and r["race_id"] not in used]
    if not cands:
        return None
    exact = [r for r in cands if r["hhmm"] == hhmm]
    if exact:
        used.add(exact[0]["race_id"])
        return exact[0]
    want = hhmm_minutes(hhmm)
    near = sorted(cands, key=lambda r: abs(hhmm_minutes(r["hhmm"]) - want))
    if near and abs(hhmm_minutes(near[0]["hhmm"]) - want) <= tolerance:
        used.add(near[0]["race_id"])
        return near[0]
    return None


def do_race(date_str, hhmm, course, targets, api_race, conns, names):
    """Pull one race's card and write it to every target that needs it."""
    try:
        d = rtv_api.race_detail(date_str, api_race["course_slug"],
                                api_race["hhmm"])
        runners = [r for r in rtv_api.runners_of(d)
                   if not r.get("withdrawn") and r.get("status") != "void"]
        race = d.get("race") or {}
    except Exception as e:
        log(f"  ! race {date_str} {course} {hhmm}: {type(e).__name__}")
        return 0, 0
    return 1, write_race(conns, names, targets, date_str, hhmm, course,
                         runners, race)


def connect_db(db):
    """Open a target connection with the two timeouts that stop a worker from
    wedging forever: SQL Server lock waits give up after 5s, and pyodbc's own
    statement timeout after 10s (its default is to wait indefinitely)."""
    conn = pyodbc.connect(conn_str(db), autocommit=True)
    conn.timeout = 10
    cur = conn.cursor()
    with contextlib.suppress(pyodbc.Error):
        cur.execute("SET LOCK_TIMEOUT 5000")
    return conn


def start_watchdog(state, stall_seconds=75):
    """Exit the process if no race has completed for `stall_seconds`.

    A wedged process writes nothing and raises nothing (the stall is below
    Python, in the driver or the socket), so the only reliable detector is
    "nothing has finished recently".  Exiting lets the chunked wrapper start a
    fresh process, which is served instantly - that turns a 10-minute stall
    into a 75-second one.
    """
    def watch():
        while True:
            time.sleep(15)
            if time.time() - state["last_done"] > stall_seconds:
                log(f"  ! no race completed in {stall_seconds}s - exiting so "
                    f"the wrapper can restart a fresh process")
                sys.stdout.flush()
                os._exit(3)

    t = threading.Thread(target=watch, daemon=True)
    t.start()


def run(d_from, d_to, threads, dbs):
    conns = {}
    for db in dbs:
        try:
            conns[db] = connect_db(db)
        except Exception as e:
            log(f"  ! cannot connect {db}: {str(e)[:60]}")
    if not conns:
        return 1
    # watchdog from the very start: a child process that wedges during the
    # date listing or the name preload used to sit there until the wrapper's
    # 300s timeout, and with a wedge every ~7 minutes that was most of the
    # wall-clock time.  150s of no progress is enough to declare it dead.
    state = {"last_done": time.time()}
    start_watchdog(state, stall_seconds=150)
    todo = pending_by_date(conns, d_from, d_to)
    race_count = sum(len(v) for v in todo.values())
    log(f"=== API backfill {d_from} .. {d_to}  dbs={','.join(conns)}  "
        f"threads={threads} ===")
    log(f"range {d_from}..{d_to}: {race_count:,} races need weights "
        f"across {len(todo):,} dates")
    if not race_count:
        return 0
    dates = sorted(todo, reverse=True)          # newest form first
    t0 = time.time()

    # name maps for the whole range, once per database, before any fetching
    names = preload_names(conns, d_from, d_to)

    # stage 1: each day's race list (one request per date, all in parallel)
    day_cache: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(rtv_api.day_races, d): d for d in dates}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                day_cache[d] = fut.result() or []
            except Exception:
                day_cache[d] = []
                with lock:
                    stats["nodata"] += 1
            state["last_done"] = time.time()   # a listed date is progress
    log(f"  listed {len(day_cache):,} dates "
        f"({sum(1 for v in day_cache.values() if not v):,} empty)")

    # stage 2: a flat queue of every race, shared pool.  One worker per date
    # ran at 2.3 dates/min (~14h for 5 years); per-race workers are what make
    # this fast.
    jobs = []
    for d in dates:
        used: set[str] = set()       # one API race per database race
        for hhmm, course, targets in todo[d]:
            hit = match_api_race(day_cache[d], hhmm, course, used)
            if hit is None:
                with lock:
                    stats["nomatch"] += 1
                continue
            jobs.append((d, hhmm, course, targets, hit))

    # only now: the date listing above can legitimately take a couple of
    # minutes, and the watchdog would kill us mid-listing
    state["last_done"] = time.time()

    with ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(do_race, *job, conns, names): job[0] for job in jobs}
        last_log = [time.time()]
        for fut in as_completed(futs):
            try:
                races, written = fut.result()
            except Exception as e:
                log(f"  ! worker died: {type(e).__name__} {str(e)[:50]}")
                races = written = 0
            with lock:
                stats["races"] += races
                stats["written"] += written
                n = stats["races"]
                state["last_done"] = time.time()
                due = (n % 100 < max(races, 1)
                       or time.time() - last_log[0] > 20
                       or n == race_count)
                if due:
                    last_log[0] = time.time()
            if due:
                rate = n / max(time.time() - t0, 1) * 60
                per_race = stats["written"] / max(n, 1)
                log(f"  {n:,}/{race_count:,} races, {stats['written']:,} "
                    f"weights ({per_race:.1f}/race), {rate:.0f} races/min, "
                    f"noname={stats['noname']}, writefail={stats['writefail']}"
                    f", nomatch={stats['nomatch']}, "
                    f"eta {(race_count - n) / max(rate, 1):.0f} min")
    for c in conns.values():
        c.close()
    log(f"DONE API {d_from}..{d_to}: {stats['races']:,} races, "
        f"{stats['written']:,} weights written in "
        f"{(time.time() - t0) / 60:.1f} min")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to",
                    default=dt.date.today().isoformat())
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--db", action="append", default=None,
                    help="target database (repeatable); default: both")
    ap.add_argument("--log", default=None,
                    help="log file (default reports\\backfill_weights.log). "
                         "Each parallel worker needs its own, otherwise a "
                         "healthy worker's writes refresh the shared log and "
                         "hide a wedged one from its supervisor.")
    a = ap.parse_args()
    if a.log:
        global LOG
        LOG = a.log if os.path.isabs(a.log) else os.path.join(ROOT, a.log)
    return run(a.d_from, a.d_to, a.threads, a.db or list(TARGETS))


if __name__ == "__main__":
    sys.exit(main())

