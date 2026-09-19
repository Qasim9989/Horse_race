"""
Automated Results & System Performance Settlement Sync
======================================================
Syncs latest finished race results from RACINGTV_2023_2026 into cloud_app/racing_form.db
and computes Early Price vs SP performance across all 3 systems (Tips, Speed & Stride, AI System).
"""

import os
import re
import sqlite3
import sys

import pandas as pd
import pyodbc

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, "cloud_app", "racing_form.db")

# The Speed & Stride rule (thresholds, junk bands, category labels) lives in the
# deployed repo so the app, the cloud cache and this ledger share one copy.
sys.path.insert(0, os.path.join(PROJECT_DIR, "cloud_app"))
from speed_stride_rule import AGREE as SS_AGREE
from speed_stride_rule import SPEED as SS_SPEED
from speed_stride_rule import SPEED_BAND, STRIDE_BAND
from speed_stride_rule import STRIDE as SS_STRIDE
from speed_stride_rule import evaluate as evaluate_speed_stride

# Ledger sub_system labels - kept from the original table so the rows already
# stored in system_results_ledger still group with the new ones.
SS_SUB_SYSTEM = {SS_AGREE: "Dual Agree", SS_SPEED: "Top Speed #1", SS_STRIDE: "Top Stride #1"}

# --- RaceIQ telemetry sourcing ---------------------------------------------
# raceiq_scrape_v2.py writes dbo.Scraped_RaceIQ_v2 and is the authority;
# dbo.Scraped_RaceIQ (v1) is only a stride-only fallback for older dates.
# TELEMETRY_FROM is the first date the v2 backfill covers.
TELEMETRY_FROM = "2026-08-01"
# Plausible physical bands - used purely to discard parser junk (v2 reads
# 6.39-7.95 m strides and 33.84-43.95 mph top speeds; v1 also emits 1.0 m
# strides and the "0-20MPH" label captured as 20).
STRIDE_MIN, STRIDE_MAX = STRIDE_BAND
SPEED_MIN, SPEED_MAX = SPEED_BAND

def strip_country(name):
    if not name:
        return ""
    return re.sub(r"\s*\([A-Z]{2,4}\)$", "", str(name), flags=re.IGNORECASE).strip()

def parse_sp(sp_val):
    if not sp_val or pd.isna(sp_val):
        return None
    s = str(sp_val).strip()
    if s.lower() in ("evens", "evs", "evensf", "evsf", "1/1"):
        return 2.0
    m = re.match(r"^(\d+)/(\d+)", s)
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        return round(1.0 + (num / den), 2)
    try:
        return round(float(s), 2)
    except (ValueError, TypeError):
        return None

def sync_rtv_to_sqlite():
    print("--- Syncing RACINGTV_2023_2026 into cloud_app/racing_form.db ---")
    rtv_conn_str = r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;Database=RACINGTV_2023_2026;Trusted_Connection=yes;"
    try:
        cn = pyodbc.connect(rtv_conn_str, timeout=5)
    except Exception as e:
        print(f"Warning: Could not connect to localdb RACINGTV: {e}")
        return

    cur = cn.cursor()
    s_conn = sqlite3.connect(DB_PATH)
    s_cur = s_conn.cursor()

    cur.execute("""
        SELECT RaceDate, HorseName, CourseName, DistanceText, PosNo, SP, Comment, JockeyName, Weight, OfficialRating
        FROM Scraped_Results
        WHERE RaceDate >= '2026-09-10'
    """)
    rows = cur.fetchall()
    print(f"Fetched {len(rows)} runner result rows from localdb.")

    for r in rows:
        rdate = str(r[0])
        raw_name = str(r[1]).strip()
        hname = strip_country(raw_name)
        course = str(r[2]).strip()
        dist = str(r[3] or "")
        pos = str(r[4] or "")
        sp = str(r[5] or "")
        comm = str(r[6] or "")
        jock = str(r[7] or "")
        wgt = str(r[8] or "")
        oral = str(r[9] or "")

        s_cur.execute("SELECT 1 FROM race_results WHERE race_date=? AND LOWER(horse_name)=LOWER(?)", (rdate, hname))
        if not s_cur.fetchone():
            s_cur.execute("""
                INSERT INTO race_results (race_date, horse_name, meeting, distance, finish_pos, beaten_distance, weight_lbs, official_rating, topspeed, rpr, jockey, sp_odds, comment)
                VALUES (?, ?, ?, ?, ?, '', ?, ?, '', '', ?, ?, ?)
            """, (rdate, hname, course, dist, pos, wgt, oral, jock, sp, comm))

    # Also sync RaceIQ telemetry.
    #
    # The authority is the v2 scraper (raceiq_scrape_v2.py ->
    # dbo.Scraped_RaceIQ_v2): one row per runner per race holding TopSpeedMph,
    # StrideM and FspPct, parsed from the labelled RaceIQ table.  The old v1
    # table (dbo.Scraped_RaceIQ) is only a fallback for dates v2 has not
    # backfilled, and only for STRIDE - 35% of v1's TopSpeed reads are the
    # "0-20MPH" column label captured as the number 20, so those are dropped
    # rather than written into the cloud DB.
    def upsert_telemetry(d_str, h, c, st, top_sp, fsp, authoritative):
        s_cur.execute(
            "SELECT rowid FROM raceiq_telemetry WHERE race_date=? AND LOWER(horse_name)=LOWER(?)",
            (d_str, h),
        )
        row = s_cur.fetchone()
        if row is None:
            s_cur.execute("INSERT INTO raceiq_telemetry VALUES (?, ?, ?, ?, ?, ?)",
                          (d_str, h, c, st, top_sp, fsp))
        elif authoritative:
            # v2 wins outright: refresh the row so a bad v1 value cannot survive
            # (and a NULL from v2 clears a value v1 only guessed at).
            s_cur.execute(
                "UPDATE raceiq_telemetry SET course_name=?, stride_length=?, top_speed=?, fsp_pct=?"
                " WHERE rowid=?",
                (c, st, top_sp, fsp, row[0]),
            )

    cur.execute(f"""
        SELECT RaceDate, HorseName, CourseName, StrideLength
        FROM Scraped_RaceIQ
        WHERE RaceDate >= '{TELEMETRY_FROM}' AND StrideLength IS NOT NULL
    """)
    v1_rows = cur.fetchall()
    v1_used = 0
    for r in v1_rows:
        try:
            st = float(r[3]) if r[3] else None
        except (TypeError, ValueError):
            st = None
        if st is None or not (STRIDE_MIN <= st <= STRIDE_MAX):
            continue
        upsert_telemetry(str(r[0]), strip_country(str(r[1]).strip()), str(r[2]).strip(),
                         st, None, None, authoritative=False)
        v1_used += 1
    print(f"RaceIQ v1 fallback (stride only): {v1_used} of {len(v1_rows)} rows used.")

    try:
        cur.execute(f"""
            SELECT RaceDate, Horse, Venue, StrideM, TopSpeedMph, FspPct
            FROM Scraped_RaceIQ_v2
            WHERE RaceDate >= '{TELEMETRY_FROM}'
        """)
        v2_rows = cur.fetchall()
    except pyodbc.Error as e:
        v2_rows = []
        print(f"Warning: Scraped_RaceIQ_v2 unreadable ({e}); v1 stride only this run.")

    v2_used = 0
    for r in v2_rows:
        st = float(r[3]) if r[3] is not None else None
        top_sp = float(r[4]) if r[4] is not None else None
        fsp = float(r[5]) if r[5] is not None else None
        if st is not None and not (STRIDE_MIN <= st <= STRIDE_MAX):
            st = None
        if top_sp is not None and not (SPEED_MIN <= top_sp <= SPEED_MAX):
            top_sp = None
        upsert_telemetry(str(r[0]), strip_country(str(r[1]).strip()), str(r[2]).strip(),
                         st, top_sp, fsp, authoritative=True)
        v2_used += 1
    print(f"RaceIQ v2 authoritative (speed + stride): {v2_used} rows written.")

    s_conn.commit()
    cn.close()
    s_conn.close()

def build_system_results_ledger():
    print("\n--- Updating System Results Ledger Table in SQLite ---")
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # system_results_ledger is owned by settle_daily_results.settle_ledger()
    # (called from the app), which rebuilds the table with
    # df.to_sql(..., if_exists="replace"): its columns are placed, places_paid,
    # early_win_pl, sp_win_pl, early_ew_pl, sp_ew_pl, bf_odds, early_place_odds,
    # bf_place_odds.  This legacy writer used the older, narrower column set and
    # raised "table system_results_ledger has no column named early_pl" - which
    # aborted the whole sync, telemetry included.  It now steps aside instead.
    cur.execute("PRAGMA table_info(system_results_ledger)")
    live_columns = {row[1] for row in cur.fetchall()}
    if live_columns and not {"early_pl", "sp_pl"}.issubset(live_columns):
        print("Note: system_results_ledger is maintained by settle_daily_results.py")
        print(f"      columns: {', '.join(sorted(live_columns))}")
        print("      Skipping the legacy ledger write - RaceIQ telemetry is unaffected.")
        con.close()
        return

    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_results_ledger (
            race_date TEXT,
            system_name TEXT,
            sub_system TEXT,
            course TEXT,
            race_time TEXT,
            horse_name TEXT,
            early_odds REAL,
            best_bookmaker TEXT,
            sp_odds REAL,
            sp_text TEXT,
            finish_pos TEXT,
            won INTEGER,
            early_pl REAL,
            sp_pl REAL,
            PRIMARY KEY(race_date, system_name, sub_system, course, race_time, horse_name)
        );
    """)

    telemetry_df = pd.read_sql(
        f"""
        SELECT race_date, LOWER(horse_name) as h_clean, horse_name, stride_length, top_speed
        FROM raceiq_telemetry
        WHERE (top_speed IS NULL OR top_speed BETWEEN {SPEED_MIN} AND {SPEED_MAX})
          AND (stride_length IS NULL OR stride_length BETWEEN {STRIDE_MIN} AND {STRIDE_MAX})
        """,
        con,
    )
    telemetry_df["h_clean"] = telemetry_df["h_clean"].apply(strip_country)

    results_df = pd.read_sql("SELECT race_date, LOWER(horse_name) as h_clean, finish_pos, sp_odds FROM race_results", con)
    results_df["h_clean"] = results_df["h_clean"].apply(strip_country)

    dates = ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
    all_ledger_entries = []

    for dt in dates:
        sel_file = os.path.join(PROJECT_DIR, "reports", f"selections_{dt}.csv")
        pl_file = os.path.join(PROJECT_DIR, "price_log", f"price_log_{dt}_auto.csv")
        if not os.path.exists(sel_file) or not os.path.exists(pl_file):
            continue

        sel_df = pd.read_csv(sel_file)
        pl_df = pd.read_csv(pl_file)

        pl_df["h_clean"] = pl_df["HorseName"].astype(str).apply(strip_country).str.lower()
        sel_df["h_clean"] = sel_df["Horse"].astype(str).apply(strip_country).str.lower()

        pl_best = pl_df.sort_values("BookPrice", ascending=False).groupby("h_clean").first().reset_index()
        m = pd.merge(sel_df, pl_best[["h_clean", "BookPrice", "BestBook"]], on="h_clean", how="left")

        day_res = results_df[results_df["race_date"] == dt].groupby("h_clean").first().reset_index()
        m = pd.merge(m, day_res[["h_clean", "finish_pos", "sp_odds"]], on="h_clean", how="left")

        # Most recent reading held before race day, per metric - the same
        # "previous run" semantics the app tab and the cloud cache use.  (An
        # all-time best would let a two-year-old peak speed pick today's runner.)
        prior_tel = telemetry_df[telemetry_df["race_date"] < dt].sort_values("race_date")
        latest = None
        for metric in ("top_speed", "stride_length"):
            part = (prior_tel.dropna(subset=[metric])
                    .groupby("h_clean", as_index=False)
                    .last()[["h_clean", metric]])
            latest = part if latest is None else latest.merge(part, on="h_clean", how="outer")
        if latest is not None:
            m = pd.merge(m, latest, on="h_clean", how="left")

        def calc_power(r):
            pos_score = 30.0 if str(r.get("LTO_POS_fresh")) == "1" else (20.0 if str(r.get("LTO_POS_fresh")) in ["2", "3"] else 5.0)
            try:
                cmax = float(r.get("CAREER_MAX", 0) or 0)
            except (ValueError, TypeError):
                cmax = 0
            rating_score = min(cmax * 0.4, 40.0)
            try:
                dslr = float(r.get("DSLR_n", 30) or 30)
                recency = 20.0 if dslr <= 21 else (15.0 if dslr <= 45 else 5.0)
            except (ValueError, TypeError):
                recency = 10.0
            return round(pos_score + rating_score + recency, 1)

        m["power_score"] = m.apply(calc_power, axis=1)

        def make_entry(sys_name, sub_name, row, dt_val=dt):
            e_odds = float(row.get("BookPrice", 0) or 0) if pd.notna(row.get("BookPrice")) else None
            b_book = str(row.get("BestBook", "") or "")
            sp_txt = str(row.get("sp_odds", "") or "")
            sp_val = parse_sp(sp_txt)
            raw_pos = str(row.get("finish_pos", "") or "").replace(".0", "").strip()

            is_nr = raw_pos.upper() in ("NR", "NON-RUNNER", "NON RUNNER") or str(row.get("Status", "")).lower() == "scratched"
            if is_nr:
                f_pos = "NR (Void)"
                is_win = 0
                e_pl = 0.0
                sp_pl = 0.0
            else:
                f_pos = raw_pos if raw_pos and raw_pos not in ("nan", "None") else "-"
                is_win = 1 if f_pos in ("1", "1st") else 0

                if e_odds and is_win == 1:
                    e_pl = round(e_odds - 1.0, 2)
                elif e_odds:
                    e_pl = -1.0
                else:
                    e_pl = 0.0 if is_win == 0 else (round(sp_val - 1.0, 2) if sp_val else -1.0)

                if sp_val and is_win == 1:
                    sp_pl = round(sp_val - 1.0, 2)
                elif sp_val:
                    sp_pl = -1.0
                else:
                    sp_pl = -1.0 if is_win == 0 else 0.0

            course_val = str(row.get("Course") or row.get("CourseName") or "")
            rtime_val = str(row.get("RaceTime") or "")
            horse_val = str(row.get("Horse") or row.get("HorseName") or "")

            return (
                dt_val, sys_name, sub_name, course_val, rtime_val, horse_val,
                e_odds, b_book, sp_val, sp_txt, f_pos, is_win, e_pl, sp_pl
            )

        for (_crs, _rtm), race_runners in m.groupby(["Course", "RaceTime"]):
            top_power = race_runners.sort_values("power_score", ascending=False).iloc[0]
            all_ledger_entries.append(make_entry("AI System", "Power Rank #1", top_power))

            # Speed & Stride - the rule itself lives in
            # cloud_app/speed_stride_rule.py, so the tab, the cloud cache and
            # this ledger can never drift apart again.  A race logs one row for
            # AGREE (one horse tops both) or up to two rows (speed + stride).
            race_rows = race_runners.to_dict("records")
            row_by_horse = {r["h_clean"]: r for r in race_rows}
            for horse, category in evaluate_speed_stride(
                (r["h_clean"], r.get("top_speed"), r.get("stride_length"))
                for r in race_rows
            ):
                ledger_row = row_by_horse.get(horse)
                if ledger_row is None:
                    continue
                all_ledger_entries.append(
                    make_entry("Speed & Stride", SS_SUB_SYSTEM[category], ledger_row)
                )

        tips_runners = m[m["SEL_HARD"] | m["SEL_SOFT"]]
        for _, tr in tips_runners.iterrows():
            sub = "Elite Hard Tip" if tr["SEL_HARD"] else "Soft Tip"
            all_ledger_entries.append(make_entry("Tips", sub, tr))

    cur.executemany("""
        INSERT OR REPLACE INTO system_results_ledger
        (race_date, system_name, sub_system, course, race_time, horse_name, early_odds, best_bookmaker, sp_odds, sp_text, finish_pos, won, early_pl, sp_pl)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, all_ledger_entries)
    con.commit()
    con.close()
    print(f"Successfully updated {len(all_ledger_entries)} system results ledger rows.")

if __name__ == "__main__":
    sync_rtv_to_sqlite()
    build_system_results_ledger()
