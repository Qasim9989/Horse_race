"""
BETFAIR TRUE BSP BACKFILL  (the fix that applies to ALL systems)
================================================================
PRODB's HIR_BSP is NOT Betfair Starting Price.  Measured book overround:

    PRODB HIR_BSP, 42,313 handicap races   = 1.2130   (21.3% bookmaker margin)
    TRUE Betfair BSP, 20 races             = 1.0025   (0.25% exchange margin)

Every lay backtest in this repo settles against HIR_BSP, so it books the
bookmaker's margin as profit.  That single fact is the entire reported edge
(Master Lay +19.75% -> +4.56% once de-margined).

This script downloads the real Betfair SP files and stores them in PRODB so
every system can settle against a genuine, tradeable exchange price.

Source (free, public, verified working for 2021-2026):
    https://promo.betfair.com/betfairsp/prices/dwbfpricesukwinDDMMYYYY.csv
Columns carried: bsp, ipmin, ipmax, morningwap, ppwap, ppmax, ppmin,
                 iptradedvol, pptradedvol, win_lose
Note: the file published on DDMMYYYY contains the PREVIOUS day's races, so
RaceDate is always taken from the file's own event_dt column.

USAGE
    python scripts/betfair_bsp_backfill.py 2021-01-01 2026-09-12
    python scripts/betfair_bsp_backfill.py 2026-08-01 2026-09-12 --map
    --map      also (re)build the HIR_BSP_TRUE column on NEW_HIR
    --force    re-download days already present in dbo.BFSP
"""

import csv
import datetime
import io
import re
import sys
import time
import urllib.error
import urllib.request

import numpy as np
import pyodbc

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

CONN_PROFORM = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=(localdb)\MSSQLLocalDB;"
    r"Database=PRODB;"
    r"Trusted_Connection=yes;"
)

URLS = {
    "uk":  "https://promo.betfair.com/betfairsp/prices/dwbfpricesukwin{}.csv",
    "ire": "https://promo.betfair.com/betfairsp/prices/dwbfpricesirewin{}.csv",
}
UA = {"User-Agent": "Mozilla/5.0"}
# Diagnostics: Betfair rate-limits (429) aggressively and silently.
STATS = {"ok": 0, "404": 0, "429": 0, "fail": 0}

CREATE_BFSP = """
IF OBJECT_ID('dbo.BFSP') IS NULL
BEGIN
    CREATE TABLE dbo.BFSP (
        RaceDate      DATE          NOT NULL,
        RaceTime      TIME(0)       NOT NULL,
        CourseClean   VARCHAR(64)   NOT NULL,
        HorseClean    VARCHAR(64)   NOT NULL,
        BSP_TRUE      FLOAT         NULL,
        IPMin         FLOAT         NULL,
        IPMax         FLOAT         NULL,
        MorningWAP    FLOAT         NULL,
        PPWAP         FLOAT         NULL,
        PPMax         FLOAT         NULL,
        PPMin         FLOAT         NULL,
        IPTradedVol   FLOAT         NULL,
        PPTradedVol   FLOAT         NULL,
        WinLose       TINYINT       NULL,
        EventID       BIGINT        NULL,
        Source        VARCHAR(8)    NULL
    );
    CREATE INDEX IX_BFSP_Key
        ON dbo.BFSP (RaceDate, CourseClean, RaceTime, HorseClean);
END
IF COL_LENGTH('dbo.BFSP','Source') IS NULL
    ALTER TABLE dbo.BFSP ADD Source VARCHAR(8) NULL;
"""

INSERT_SQL = """
INSERT INTO dbo.BFSP
 (RaceDate, RaceTime, CourseClean, HorseClean, BSP_TRUE, IPMin, IPMax,
  MorningWAP, PPWAP, PPMax, PPMin, IPTradedVol, PPTradedVol, WinLose, EventID,
  Source)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def clean_name(s):
    """Same normalisation the rest of the codebase uses for horse/course keys."""
    return "".join(c for c in str(s or "").lower() if c.isalnum())


def clean_course(menu_hint):
    """Normalise the venue field.

    2023+ : 'Chepstow 18th Aug'            -> 'chepstow'
    2021-22: 'GB / Fakenham  4th Jan'      -> 'fakenham'
    """
    s = str(menu_hint or "").strip()
    if " / " in s:                      # older format prefixes the country
        s = s.split(" / ", 1)[1]
    s = re.sub(r"\s+\d{1,2}(st|nd|rd|th)\s+\w+$", "", s, flags=re.IGNORECASE)
    return clean_name(s)


def f(x):
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return None


def parse_event_dt(s):
    """'18-08-2026 14:45' -> (date, time)."""
    s = str(s or "").strip()
    for fmt in ("%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return dt.date(), dt.time()
        except ValueError:
            continue
    return None, None


def fetch_day(pub_date, source="uk", retries=5):
    """Returns ('ok', text) | ('404', None) | ('fail', None).

    Betfair rate-limits aggressively (HTTP 429), so back off exponentially
    rather than raising.  UK and Ireland are published as SEPARATE files - the
    uk file contains no Irish racing at all (verified: 0 Irish courses).
    """
    url = URLS[source].format(pub_date.strftime("%d%m%Y"))
    delay = 4.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                STATS["ok"] += 1
                return "ok", r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                STATS["404"] += 1
                return "404", None
            if e.code == 429:
                STATS["429"] += 1
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            STATS["fail"] += 1
            return "fail", None
        except Exception:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            STATS["fail"] += 1
            return "fail", None
    return "fail", None


def iter_rows(text):
    for row in csv.DictReader(io.StringIO(text)):
        # Betfair changed the header CASE: 2021-2022 files ship UPPERCASE
        # (EVENT_DT, BSP...) while 2023+ are lowercase.  Normalise the keys
        # only - values must keep their case.
        row = {str(k).lower(): v for k, v in row.items()}
        d, t = parse_event_dt(row.get("event_dt"))
        if d is None:
            continue
        bsp = f(row.get("bsp"))
        if bsp is None or bsp <= 1.0:
            continue
        eid = row.get("event_id", "")
        yield (
            d,
            t,
            clean_course(row.get("menu_hint")),
            clean_name(row.get("selection_name")),
            bsp,
            f(row.get("ipmin")),
            f(row.get("ipmax")),
            f(row.get("morningwap")),
            f(row.get("ppwap")),
            f(row.get("ppmax")),
            f(row.get("ppmin")),
            f(row.get("iptradedvol")),
            f(row.get("pptradedvol")),
            1 if str(row.get("win_lose", "")).strip() == "1" else 0,
            int(eid) if str(eid).strip().isdigit() else None,
        )


def download_range(d_from, d_to, source="uk", force=False):
    conn = pyodbc.connect(CONN_PROFORM)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(CREATE_BFSP)

    done = set()
    if not force:
        cur.execute("SELECT DISTINCT RaceDate FROM dbo.BFSP WHERE Source = ?",
                    source)
        done = {r[0] for r in cur.fetchall()}
    print(f"  [{source}] race-dates already loaded: {len(done):,}")

    cur.fast_executemany = True
    pub = d_from
    end = d_to + datetime.timedelta(days=1)
    days = rows = missing = skipped = seen = 0
    failed = []
    t0 = time.time()

    while pub <= end:
        race_day = pub - datetime.timedelta(days=1)
        if race_day in done:
            skipped += 1
            pub += datetime.timedelta(days=1)
            continue
        status, text = fetch_day(pub, source, retries=1)
        batch = list(iter_rows(text)) if status == "ok" and text else []
        if batch:
            cur.executemany(INSERT_SQL, [(*r, source) for r in batch])
            rows += len(batch)
            days += 1
            done.add(race_day)
        elif status == "fail":
            failed.append(pub)
        else:
            missing += 1
        seen += 1
        if seen % 10 == 0:
            print(f"    {pub}  days={days:,} rows={rows:,} "
                  f"missing={missing:,} failed={len(failed)} "
                  f"http={STATS} {(time.time()-t0)/60:.1f}min", flush=True)
        time.sleep(0.6)
        pub += datetime.timedelta(days=1)

    # Retry pass - Betfair rate-limits, so recover what we can at a slow pace.
    if failed:
        print(f"  retry pass for {len(failed)} failed publish-dates ...")
        time.sleep(45)
        still = []
        for p in failed:
            status, text = fetch_day(p, source, retries=6)
            batch = list(iter_rows(text)) if status == "ok" and text else []
            if batch:
                cur.executemany(INSERT_SQL, [(*r, source) for r in batch])
                rows += len(batch)
                days += 1
            elif status == "404":
                missing += 1
            else:
                still.append(p)
            time.sleep(1.5)
        print(f"  retry pass recovered {len(failed)-len(still)}, "
              f"still failing {len(still)}")
        if still:
            print("  unresolved publish-dates: "
                  + ", ".join(d.strftime("%Y-%m-%d") for d in still[:20])
                  + (" ..." if len(still) > 20 else ""))

    cur.fast_executemany = False
    print(f"  DOWNLOAD DONE: days={days:,} rows={rows:,} "
          f"missing={missing:,} skipped={skipped:,} in {(time.time()-t0)/60:.1f}min")
    conn.close()


ALTER_HIR = """
IF COL_LENGTH('dbo.NEW_HIR','HIR_BSP_TRUE') IS NULL
    ALTER TABLE dbo.NEW_HIR ADD HIR_BSP_TRUE FLOAT NULL;
"""

PULL_PRODB = """
SELECT RH.RH_RNo, HIR.HIR_HNo, HIR.HIR_BSP AS PRODB_BSP,
       C.C_Name AS CourseName, H.H_Name_No_Anything AS HorseName,
       CAST(RH.RH_DateTime AS DATE) AS RaceDate,
       CAST(RH.RH_DateTime AS TIME(0)) AS RaceTime
FROM dbo.NEW_RH RH
JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
LEFT JOIN dbo.NEW_C C ON C.C_ID = RH.RH_CNo
WHERE RH.RH_DateTime >= ? AND RH.RH_DateTime < ?
  AND HIR.HIR_BSP > 1.0
"""

PULL_BFSP = """
SELECT RaceDate, RaceTime, CourseClean, HorseClean, BSP_TRUE, WinLose, IPTradedVol
FROM dbo.BFSP WHERE RaceDate >= ? AND RaceDate < ?
"""

UPDATE_FROM_TMP = """
UPDATE HIR SET HIR_BSP_TRUE = T.BSP_TRUE
FROM dbo.NEW_HIR HIR
JOIN dbo._BSPMAP_TMP T
  ON T.HIR_RNo = HIR.HIR_RNo AND T.HIR_HNo = HIR.HIR_HNo;
"""


def map_to_hir(d_from, d_to):
    """Join dbo.BFSP to PRODB runners and populate NEW_HIR.HIR_BSP_TRUE."""
    import pandas as pd

    conn = pyodbc.connect(CONN_PROFORM)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(ALTER_HIR)

    d_end = d_to + datetime.timedelta(days=1)
    print("  pulling PRODB runner keys ...")
    pro = pd.read_sql(PULL_PRODB, conn, params=[d_from, d_end])
    bf = pd.read_sql(PULL_BFSP, conn, params=[d_from, d_end])
    print(f"    PRODB runners: {len(pro):,}   BFSP rows: {len(bf):,}")
    if pro.empty or bf.empty:
        print("    nothing to map.")
        conn.close()
        return

    pro["RaceDate"] = pd.to_datetime(pro["RaceDate"]).dt.date
    bf["RaceDate"] = pd.to_datetime(bf["RaceDate"]).dt.date
    pro["course_k"] = pro["CourseName"].map(clean_name)
    pro["horse_k"] = pro["HorseName"].map(clean_name)
    bf = bf.rename(columns={"CourseClean": "course_k", "HorseClean": "horse_k"})

    bcols = ["RaceDate", "RaceTime", "course_k", "horse_k", "BSP_TRUE", "WinLose"]
    bf = bf[bcols].drop_duplicates(
        subset=["RaceDate", "course_k", "horse_k"]).copy()

    # Try progressively looser keys.  A horse runs at most once a day, so
    # (date, horse) is effectively unique on its own.  The looser keys matter
    # because pre-2022 Betfair files write the venue as "GB / Fakenham  4th Jan"
    # rather than "Fakenham 18th Aug", and delayed races shift the off time.
    KEYCHAIN = [
        ["RaceDate", "RaceTime", "course_k", "horse_k"],
        ["RaceDate", "course_k", "horse_k"],
        ["RaceDate", "RaceTime", "horse_k"],
        ["RaceDate", "horse_k"],
    ]
    m = pro.copy()
    m["BSP_TRUE"] = np.nan
    for i, keys in enumerate(KEYCHAIN):
        need = m["BSP_TRUE"].isna()
        if not need.any():
            break
        # Per-runner join.  A horse runs at most once a day, so each key level
        # (ending in (date, horse)) is effectively 1:1.  The old code dropped
        # to ONE row per RH_RNo and broadcast that single BSP across the race,
        # which is why the joined book summed to 0.78 instead of ~1.00.
        right = bf.drop_duplicates(subset=keys)[[*keys, "BSP_TRUE"]]
        merged = m.loc[need, keys].merge(right, on=keys, how="left")
        if len(merged) != int(need.sum()):
            # a many-to-many would mean two runners share every key in this
            # level; fall back to a per-(RH_RNo,HIR_HNo) positional map
            merged = (m.loc[need, ["RH_RNo", "HIR_HNo", *keys]]
                        .merge(right, on=keys, how="left")
                        .drop_duplicates(subset=["RH_RNo", "HIR_HNo"])
                        .sort_values(["RH_RNo", "HIR_HNo"]))
        before = int(m["BSP_TRUE"].notna().sum())
        m.loc[need, "BSP_TRUE"] = merged["BSP_TRUE"].to_numpy()
        after = int(m["BSP_TRUE"].notna().sum())
        if after > before:
            print(f"    key {i+1} {keys}: +{after-before:,}")

    matched = m[m["BSP_TRUE"].notna()].copy()
    pct = len(matched) / max(len(m), 1) * 100
    print(f"    MATCHED {len(matched):,} / {len(m):,} = {pct:.1f}%")

    # Where is the coverage being lost?
    un = m[m["BSP_TRUE"].isna()]
    if len(un):
        top = un["course_k"].value_counts().head(6).to_dict()
        print(f"    unmatched by course (top): {top}")

    # The point of the exercise: compare the two books' margins.
    for col, label in (("PRODB_BSP", "PRODB HIR_BSP (was used)"),
                       ("BSP_TRUE", "BETFAIR BSP_TRUE (real)")):
        g = (1.0 / matched[col]).groupby(matched["RH_RNo"]).sum().mean()
        print(f"    mean overround on {label:<28} = {g:.4f}")

    cur.execute("IF OBJECT_ID('dbo._BSPMAP_TMP') IS NOT NULL "
                "DROP TABLE dbo._BSPMAP_TMP;")
    cur.execute("CREATE TABLE dbo._BSPMAP_TMP (HIR_RNo INT NOT NULL, "
                "HIR_HNo INT NOT NULL, BSP_TRUE FLOAT NULL)")
    rows = [(int(a), int(b), float(c))
            for a, b, c in zip(matched["RH_RNo"], matched["HIR_HNo"],
                               matched["BSP_TRUE"], strict=False)]
    cur.fast_executemany = True
    cur.executemany("INSERT INTO dbo._BSPMAP_TMP (HIR_RNo,HIR_HNo,BSP_TRUE) "
                    "VALUES (?,?,?)", rows)
    cur.fast_executemany = False
    cur.execute(UPDATE_FROM_TMP)
    print(f"    NEW_HIR.HIR_BSP_TRUE populated for {len(rows):,} runners")
    cur.execute("DROP TABLE dbo._BSPMAP_TMP")
    conn.close()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) >= 2:
        d_from = datetime.date.fromisoformat(args[0])
        d_to = datetime.date.fromisoformat(args[1])
    else:
        d_to = datetime.date.today()
        d_from = d_to - datetime.timedelta(days=30)

    print("=" * 100)
    print(f"  BETFAIR TRUE BSP BACKFILL   {d_from} -> {d_to}")
    print("=" * 100)
    for src in ("uk", "ire"):
        download_range(d_from, d_to, source=src, force="--force" in flags)
    if "--nomap" not in flags:
        print("  mapping dbo.BFSP -> dbo.NEW_HIR.HIR_BSP_TRUE ...")
        map_to_hir(d_from, d_to)
    print("  DONE.")


if __name__ == "__main__":
    main()
