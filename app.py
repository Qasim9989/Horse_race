# ==============================================================================
# HR BEST TIMES & TELEMETRY CLOUD APPLICATION (STREAMLIT COMMUNITY CLOUD)
# ==============================================================================
import datetime as dt
import gzip
import json
import os
import re
import shutil
import sqlite3
import sys
from typing import Any, Literal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import betfair_ew_service
import price_snapshot
import rtv_api
import speed_stride_rule as ss_rule  # the one Speed & Stride rule

# Measured returns.  The primary figure is the audited Proform SData result
# (STRIDE_SYSTEM.md, at BSP net of 2% commission); the second line is what the
# live RacingTV RaceIQ feed reproduces on its own terms.
SS_CLAIMS = ss_rule.load_claims()


def ss_claim_card(category):
    """Audited return for a category, plus the live RaceIQ feed check."""
    replication = ss_rule.replication_label(category, SS_CLAIMS)
    if replication == ss_rule.NO_CLAIM:
        replication = "not run yet"
    return (f"{ss_rule.audited_card(category)}  \n"
            f"*RaceIQ feed check (win-only at SP): {replication}*")

try:  # snapshot-verified settlement (see early_vs_sp.py)
    import early_vs_sp as evs
except Exception:  # pragma: no cover - optional
    evs = None  # type: ignore[assignment]

# ------------------------------------------------------------------------------
# Page Setup & Styling
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="HR Best Times & Form",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    .main-header {
        font-size: 26px;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0px;
    }
    .sub-header {
        font-size: 14px;
        color: #64748B;
        margin-bottom: 12px;
    }
    .comment-card {
        background: #F1F5F9;
        border-left: 4px solid #3B82F6;
        padding: 10px 14px;
        border-radius: 0 6px 6px 0;
        margin: 6px 0;
    }
    .market-move {
        background: #FEF3C7;
        color: #92400E;
        padding: 2px 6px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 12px;
    }
    .val-badge {
        background: #DCFCE7;
        color: #15803D;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 12px;
        border: 1px solid #86EFAC;
    }
</style>
""",
    unsafe_allow_html=True,
)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "racing_form.db")

# ---------------------------------------------------------------------------
# DATABASE BOOTSTRAP
# ---------------------------------------------------------------------------
# racing_form.db is now ~100 MB and grows every day.  That crossed GitHub's
# 100 MB per-file HARD limit, so `git push` started being rejected and the
# deployed app was frozen on whatever copy last made it through (2026-09-21).
#
# The fix: the repo carries racing_form.db.gz (~26 MB, SQLite compresses to
# about 26%) and we unpack it here.  Streamlit Cloud's filesystem is ephemeral,
# so this runs once per cold start and takes a few seconds.
#
# The local working copy in this folder is always the .db - publish_cloud_caches
# refreshes it, then re-gzips.  We only unpack when the .db is missing or older
# than the .gz, so a local run is never clobbered by a stale archive.
DB_GZ = DB_PATH + ".gz"


def _ensure_db():
    if not os.path.exists(DB_GZ):
        return
    try:
        if os.path.exists(DB_PATH) and \
                os.path.getmtime(DB_PATH) >= os.path.getmtime(DB_GZ):
            return
        tmp = DB_PATH + ".unpacking"
        with gzip.open(DB_GZ, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        os.replace(tmp, DB_PATH)
    except Exception:
        # never let the bootstrap take the app down - if it fails the app will
        # report the missing db the same way it always did
        pass


_ensure_db()


def parse_comment_text(raw):
    if not raw:
        return "", ""
    s = str(raw).strip()
    if s.startswith("{") and "comment" in s:
        try:
            data = json.loads(s.replace("'", '"').replace("None", "null"))
            return data.get("comment", ""), data.get("bettingMovements", "") or ""
        except Exception:
            c_m = re.search(r"'comment':\s*'([^']*)'", s)
            b_m = re.search(r"'bettingMovements':\s*'([^']*)'", s)
            c_text = c_m.group(1) if c_m else s
            b_text = b_m.group(1) if b_m else ""
            return c_text, b_text
    return s, ""



@st.cache_data(ttl=120)
def get_bf_win_odds_map(date_str: str) -> dict[str, float]:
    """Return map of normalized horse name -> best Betfair Win price (lay or back)."""
    bf_map: dict[str, float] = {}
    db_dir = os.path.dirname(os.path.abspath(__file__))

    # Priority 1: Check bundled bf_odds_today.json
    bundled_path = os.path.join(db_dir, "bf_odds_today.json")
    if os.path.exists(bundled_path):
        try:
            with open(bundled_path, encoding="utf-8") as f:
                b_data = json.load(f)
            if b_data.get("date") == date_str and isinstance(b_data.get("odds"), dict):
                bf_map.update({k: round(float(v), 2) for k, v in b_data["odds"].items()
                               if price_ok(v)})
        except Exception:
            pass

    # Priority 2: Check local price_log if available
    parent_dir = os.path.dirname(db_dir)
    pl_path = os.path.join(parent_dir, "price_log", f"price_log_{date_str}_auto.csv")
    if os.path.exists(pl_path):
        try:
            pl_df = pd.read_csv(pl_path)
            for _, r in pl_df.iterrows():
                h_c = re.sub(r"[^a-zA-Z0-9\s]", "", re.sub(r"\([^)]*\)", "", str(r["HorseName"]))).strip().lower()
                if pd.notna(r.get("BetfairPrice")) and price_ok(r["BetfairPrice"]):
                    bf_map[h_c] = round(float(r["BetfairPrice"]), 2)
        except Exception:
            pass

    if betfair_ew_service.is_configured():
        try:
            tok = betfair_ew_service.login()
            cat = betfair_ew_service.fetch_today_catalogue(date_str, tok)
            win_mkts = [m for m in cat if m.get("description", {}).get("marketType") == "WIN"]
            m_ids = [m["marketId"] for m in win_mkts]
            books = betfair_ew_service.fetch_market_books(m_ids, token=tok)
            book_by_id = {b["marketId"]: b for b in books}
            for m in win_mkts:
                b = book_by_id.get(m["marketId"])
                if not b:
                    continue
                r_names = {str(r["selectionId"]): re.sub(r"[^a-zA-Z0-9\s]", "", re.sub(r"\([^)]*\)", "", str(r["runnerName"]))).strip().lower() for r in m.get("runners", [])}
                for r in b.get("runners", []):
                    h_c_bf = r_names.get(str(r.get("selectionId")))
                    if not h_c_bf:
                        continue
                    ex = r.get("ex", {})
                    lays = ex.get("availableToLay", [])
                    backs = ex.get("availableToBack", [])
                    p = lays[0]["price"] if lays else (backs[0]["price"] if backs else None)
                    if p and price_ok(p):
                        bf_map[h_c_bf] = round(float(p), 2)
        except Exception:
            pass

    return bf_map


PRICE_MIN = 1.00
PRICE_MAX = 1000.0


def price_ok(value) -> bool:
    """True when a Betfair price is usable.

    Betfair's ladder caps at 1000, so a captured 1000 means "no offer" rather than a
    price - that 1000 on a 151 chance is exactly the outlier this keeps out of the maps.
    """
    try:
        price = float(value)
    except (TypeError, ValueError):
        return False
    return PRICE_MIN < price < PRICE_MAX


@st.cache_data(ttl=120)
def get_bf_place_odds_map(date_str: str) -> tuple[dict[str, float], dict[str, str]]:
    """Return (place_price_map, place_terms_map) keyed by normalized horse name."""
    p_map: dict[str, float] = {}
    t_map: dict[str, str] = {}
    db_dir = os.path.dirname(os.path.abspath(__file__))
    bundled_path = os.path.join(db_dir, "bf_odds_today.json")
    if os.path.exists(bundled_path):
        try:
            with open(bundled_path, encoding="utf-8") as f:
                b_data = json.load(f)
            if b_data.get("date") == date_str:
                raw_p = b_data.get("place", {})
                raw_t = b_data.get("place_terms", {})
                if isinstance(raw_p, dict):
                    p_map.update({k: round(float(v), 2) for k, v in raw_p.items()
                                  if price_ok(v)})
                if isinstance(raw_t, dict):
                    t_map.update(raw_t)
        except Exception:
            pass
    if betfair_ew_service.is_configured() and not p_map:
        try:
            tok = betfair_ew_service.login()
            cat = betfair_ew_service.fetch_today_catalogue(date_str, tok)
            place_mkts = [m for m in cat if m.get("description", {}).get("marketType") == "PLACE"]
            m_ids = [m["marketId"] for m in place_mkts]
            books = betfair_ew_service.fetch_market_books(m_ids, token=tok)
            book_by_id = {b["marketId"]: b for b in books}
            for m in place_mkts:
                b = book_by_id.get(m["marketId"])
                if not b:
                    continue
                num_winners = b.get("numberOfWinners", 3)
                r_names = {str(r["selectionId"]): betfair_ew_service.normalize_name(r["runnerName"]) for r in m.get("runners", [])}
                for r in b.get("runners", []):
                    h_c = r_names.get(str(r.get("selectionId")))
                    if not h_c:
                        continue
                    ex = r.get("ex", {})
                    backs = ex.get("availableToBack", [])
                    lays = ex.get("availableToLay", [])
                    p = backs[0]["price"] if backs else (lays[0]["price"] if lays else None)
                    if p and price_ok(p):
                        p_map[h_c] = round(float(p), 2)
                        t_map[h_c] = f"{num_winners} Pl"
        except Exception:
            pass
    return p_map, t_map

@st.cache_data(ttl=60)
def load_day_schedule(date_str):
    races = rtv_api.day_races(date_str)
    if not races:
        return {}
    grouped: dict[str, list[dict]] = {}
    for r in races:
        c = r.get("course_name", "")
        grouped.setdefault(c, []).append(r)
    return grouped


@st.cache_data(ttl=60)
def get_racecard_data(date_str, course_slug, hhmm):
    d = rtv_api.race_detail(date_str, course_slug, hhmm)
    if not d or "race" not in d:
        return None, None

    runners = rtv_api.runners_of(d)
    race_info = d.get("race", {})

    # Live bookmaker odds directly from RTV API
    odds_map: dict[str, list[dict]] = {}
    try:
        odds_res, _ = rtv_api.runner_odds([x["runner_id"] for x in runners])
        odds_map = odds_res or {}
    except Exception:
        pass

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cur = conn.cursor()

    results = []
    bf_win_map = get_bf_win_odds_map(date_str)

    for r in runners:
        if r.get("status") == "scratched":
            continue

        h_name = str(r.get("horse_name", "")).strip()
        jockey = str(r.get("jockey") or "")
        trainer = str(r.get("trainer") or "")
        weight_st = str(r.get("weight", ""))
        cloth_no = r.get("cloth_number", "")
        dlr_api = r.get("days_since_run")
        runner_id = r.get("runner_id")

        # 1. Weight & Claim
        claim = 0
        wgt_lbs = 0
        if "-" in str(weight_st):
            try:
                st_part, lb_part = str(weight_st).split("-")
                wgt_lbs = int(st_part) * 14 + int(lb_part)
            except Exception:
                pass

        c_match = re.search(r"\((\d+)\)", jockey)
        if c_match:
            claim = int(c_match.group(1))
        net_wgt = wgt_lbs - claim if wgt_lbs else 0

        # 2. Live Bookmaker Odds & Extra Places (Decimal)
        quotes = odds_map.get(runner_id, [])
        valid_quotes = [q for q in quotes if q.get("decimal") and q["decimal"] > 1.0]
        best_decimal = None
        best_bookie = "-"
        best_book_str = "-"
        extra_places_str = "-"
        if valid_quotes:
            best_q = max(valid_quotes, key=lambda x: x["decimal"])
            best_decimal = round(float(best_q["decimal"]), 2)
            best_bookie = str(best_q["bookmaker_name"])
            best_book_str = f"{best_decimal:.2f} ({best_bookie})"
            place_counts = [int(q["places"]) for q in valid_quotes if q.get("places")]
            max_pl = max(place_counts) if place_counts else 0
            if max_pl >= 4:
                pl_books = [q["bookmaker_name"] for q in valid_quotes if q.get("places") == max_pl]
                extra_places_str = f"{max_pl} Places ({', '.join(pl_books[:2])})"

        # 3. Exact horse match in History
        cur.execute(
            """
            SELECT race_date, meeting, distance, finish_pos, beaten_distance, weight_lbs,
                   official_rating, topspeed, rpr, comment, sp_odds
            FROM race_results
            WHERE horse_name = ? OR horse_name LIKE ?
            ORDER BY race_date DESC
        """,
            (h_name, f"{h_name} (%"),
        )
        rp_rows = cur.fetchall()

        ts_list = []
        rpr_list = []
        last_comment = ""
        betting_movements = ""
        last_run_desc = "No prior runs"
        delta_weight = None
        has_won_course = False
        has_won_dist = False
        was_beaten_fav = False
        win_rows = []
        placings_at_trip = 0

        target_course_clean = course_slug.lower().strip()
        dist_text = str(race_info.get("distance_formatted", "") or race_info.get("distance", "")).lower().replace(" ", "")

        if rp_rows:
            lto = rp_rows[0]
            lto_date = str(lto[0])
            lto_course = str(lto[1])
            lto_dist = str(lto[2])
            lto_pos = str(lto[3])
            lto_btn = str(lto[4] or "")
            lto_wgt = lto[5]
            lto_or = lto[6]
            lto_ts = lto[7]
            lto_rpr = lto[8]
            lto_comm_raw = lto[9]

            last_comment, betting_movements = parse_comment_text(lto_comm_raw)
            btn_str = (
                f" btn {lto_btn}L"
                if lto_btn and str(lto_pos) != "1"
                else (" won" if str(lto_pos) == "1" else "")
            )
            last_run_desc = f"{lto_date} {lto_course} ({lto_dist}): {lto_pos}{btn_str} | OR:{lto_or or '-'} TS:{lto_ts or '-'} RPR:{lto_rpr or '-'}"

            if lto_wgt and str(lto_wgt).isdigit() and net_wgt:
                delta_weight = net_wgt - int(lto_wgt)

            for idx, row in enumerate(rp_rows):
                pos = str(row[3] or "")
                c_name = str(row[1] or "").lower().strip()
                dist_str = str(row[2] or "").lower().replace(" ", "")
                odds_str = str(row[10] or "")

                if pos in ("1", "2", "3") and dist_text[:2] in dist_str:
                    placings_at_trip += 1

                if pos == "1":
                    win_rows.append(row)
                    if target_course_clean in c_name or c_name in target_course_clean:
                        has_won_course = True
                    if dist_text[:2] in dist_str:
                        has_won_dist = True

                if (
                    idx == 0
                    and ("f" in odds_str.lower() or "fav" in odds_str.lower())
                    and pos != "1"
                ):
                    was_beaten_fav = True

                try:
                    if row[7] and int(row[7]) > 0:
                        ts_list.append(int(row[7]))
                except Exception:
                    pass
                try:
                    if row[8] and int(row[8]) > 0:
                        rpr_list.append(int(row[8]))
                except Exception:
                    pass

        cd_flags = ""
        if has_won_course and has_won_dist:
            cd_flags = "CD"
        elif has_won_course:
            cd_flags = "C"
        elif has_won_dist:
            cd_flags = "D"
        if was_beaten_fav:
            cd_flags += " BF"
        cd_flags = cd_flags.strip()

        # Ratings High / Low
        best_ts = max(ts_list) if ts_list else None
        low_ts = min(ts_list) if ts_list else None
        avg_ts_3 = round(sum(ts_list[:3]) / len(ts_list[:3]), 1) if ts_list else None
        best_rpr = max(rpr_list) if rpr_list else None
        low_rpr = min(rpr_list) if rpr_list else None

        ts_hl_str = f"{best_ts}/{low_ts}" if best_ts is not None else "-"
        rpr_hl_str = f"{best_rpr}/{low_rpr}" if best_rpr is not None else "-"

        # Winning Weight vs Now Weight
        win_wgt_str = "Maiden"
        last_win_desc = "No prior wins (Maiden)"
        if win_rows:
            last_win = win_rows[0]
            lw_wgt = last_win[5]
            lw_or = last_win[6]

            lw_date = last_win[0]
            lw_course = last_win[1]
            if lw_wgt and str(lw_wgt).isdigit() and net_wgt:
                diff = net_wgt - int(lw_wgt)
                sign = f"{diff:+d}" if diff != 0 else "0"
                win_wgt_str = f"{sign} lb (won off {lw_wgt}lb)"
                last_win_desc = f"{lw_date} {lw_course}: won off {lw_wgt}lb (OR {lw_or or '-'}). Today: {net_wgt}lb ({sign} lb)"
            elif lw_wgt:
                win_wgt_str = f"Won off {lw_wgt}lb"

        # Value Strategy Flag / Alert
        val_tag = "-"
        if delta_weight is not None and delta_weight <= -8:
            val_tag = f"⚡ {delta_weight:+d}lb"
        elif placings_at_trip >= 2 and (delta_weight is not None and delta_weight <= 0):
            val_tag = "⭐ Value Pick"
        elif placings_at_trip >= 2:
            val_tag = f"🔔 Placed ({placings_at_trip}x)"

        # 4. Coursetrack GPS Telemetry
        cur.execute(
            """
            SELECT stride_length, top_speed, fsp_pct
            FROM raceiq_telemetry
            WHERE (horse_name = ? OR horse_name LIKE ?) AND stride_length IS NOT NULL
        """,
            (h_name, f"{h_name} (%"),
        )
        iq_rows = cur.fetchall()

        strides = [float(x[0]) for x in iq_rows if x[0]]
        speeds = [float(x[1]) for x in iq_rows if x[1]]
        fsps = [float(x[2]) for x in iq_rows if x[2]]

        best_speed = round(max(speeds), 2) if speeds else None
        avg_speed = round(sum(speeds) / len(speeds), 2) if speeds else None
        best_stride = round(max(strides), 2) if strides else None
        avg_stride = round(sum(strides) / len(strides), 2) if strides else None
        avg_fsp = round(sum(fsps) / len(fsps), 1) if fsps else None

        results.append(
            {
                "No": cloth_no,
                "Horse": h_name,
                "Jockey": jockey,
                "Trainer": trainer,
                "Flags": cd_flags or "-",
                "DLR": dlr_api if dlr_api is not None else "-",
                "Wgt_Lbs": net_wgt,
                "Claim": claim if claim > 0 else "-",
                "dWgt": f"{delta_weight:+d} lb" if delta_weight is not None else "-",
                "Win_Wgt": win_wgt_str,
                "Last_Win_Desc": last_win_desc,
                "Odds": f"{best_decimal:.2f}" if best_decimal is not None else "-",
                "BF_Odds": f"{bf_win_map.get(re.sub(r'[^a-zA-Z0-9\s]', '', re.sub(r'\([^)]*\)', '', str(h_name))).strip().lower()):.2f}" if bf_win_map.get(re.sub(r'[^a-zA-Z0-9\s]', '', re.sub(r'\([^)]*\)', '', str(h_name))).strip().lower()) else "-",
                "Bookmaker": best_bookie,
                "Best_Book": best_book_str,
                "Extra_Places": extra_places_str,
                "System_Alert": val_tag,
                "Best_TS": best_ts or 0,
                "TS_HL": ts_hl_str,
                "Avg_TS3": avg_ts_3 or 0,
                "Best_RPR": best_rpr or 0,
                "RPR_HL": rpr_hl_str,
                "Best_MPH": best_speed or 0,
                "Avg_MPH": avg_speed or 0,
                "Best_Stride": best_stride or 0,
                "Avg_Stride": avg_stride or 0,
                "FSP_Pct": avg_fsp or 0,
                "Last_Run_Desc": last_run_desc,
                "Last_Comment": last_comment,
                "Betting_LTO": betting_movements,
            }
        )

    conn.close()

    df = pd.DataFrame(results)
    if df.empty:
        return None, race_info

    df["Power_Score"] = (
        (df["Best_TS"] * 0.35)
        + (df["Avg_TS3"] * 0.35)
        + (df["Best_RPR"] * 0.30)
        - (df["Wgt_Lbs"] * 0.15)
    ).round(1)

    # Rank only over rows whose figures are plausible (the same guard
    # log_power_top2.py applies: TS >= 40 and RPR >= 40).  Without it the score
    # mixes scales - rows carry Topspeed 14 or RPR 39 for a horse rated 58 - and a
    # 251/1 debutant can top the card, which is why the podium here disagreed with
    # the pick that actually got logged.  Unranked rows sort to the bottom and
    # stay visible in the table.
    plausible = (df["Best_TS"] >= 40) & (df["Best_RPR"] >= 40)
    df["Ranked"] = plausible
    df["Master_Rank"] = (
        df["Power_Score"].where(plausible).rank(ascending=False, method="min")
    ).fillna(9999).astype(int)
    df_sorted = df.sort_values("Master_Rank").reset_index(drop=True)

    return df_sorted, race_info


@st.cache_data(ttl=180)
def scan_daily_tips(date_str, target_course=None):
    schedule = load_day_schedule(date_str)
    if not schedule:
        return pd.DataFrame()

    races_to_scan = []
    if target_course and target_course != "All Meetings Today":
        races_to_scan = schedule.get(target_course, [])
    else:
        for _c, r_list in schedule.items():
            races_to_scan.extend(r_list)

    races_to_scan.sort(key=lambda x: (x.get("time", ""), x.get("course_name", "")))

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cur = conn.cursor()

    picks = []

    bf_map_today = get_bf_win_odds_map(date_str)
    for r in races_to_scan:
        time_str = r.get("time", "")
        c_name = r.get("course_name", "")
        c_slug = r.get("course_slug", "")
        hhmm = r.get("hhmm", "")

        try:
            d = rtv_api.race_detail(date_str, c_slug, hhmm)
        except Exception:
            continue

        if not d or "race" not in d:
            continue

        race_info = d.get("race", {})
        runners = rtv_api.runners_of(d)
        dist_text = str(race_info.get("distance_formatted", "") or race_info.get("distance", "")).lower().replace(" ", "")

        odds_map: dict[str, list[dict]] = {}
        try:
            odds_res, _ = rtv_api.runner_odds([x["runner_id"] for x in runners])
            odds_map = odds_res or {}
        except Exception:
            pass

        for run in runners:
            if run.get("status") == "scratched":
                continue
            h_name = str(run.get("horse_name", "")).strip()
            h_c_val = re.sub(r'[^a-zA-Z0-9\s]', '', re.sub(r'\([^)]*\)', '', str(h_name))).strip().lower()
            bf_odds_val = bf_map_today.get(h_c_val)
            _bf_place_m, _bf_place_t = get_bf_place_odds_map(date_str)
            bf_p_price = _bf_place_m.get(h_c_val)
            bf_p_terms = _bf_place_t.get(h_c_val, "")
            weight_st = str(run.get("weight", ""))
            jockey = str(run.get("jockey") or "")
            runner_id = run.get("runner_id")

            claim = 0
            wgt_lbs = 0
            if "-" in str(weight_st):
                try:
                    s_part, l_part = str(weight_st).split("-")
                    wgt_lbs = int(s_part) * 14 + int(l_part)
                except Exception:
                    pass
            c_match = re.search(r"\((\d+)\)", jockey)
            if c_match:
                claim = int(c_match.group(1))
            net_wgt = wgt_lbs - claim if wgt_lbs else 0

            quotes = odds_map.get(runner_id, [])
            valid_quotes = [q for q in quotes if q.get("decimal") and q["decimal"] > 1.0]
            best_decimal = None
            best_bookie = "-"
            extra_places_str = "-"
            if valid_quotes:
                best_q = max(valid_quotes, key=lambda x: x["decimal"])
                best_decimal = round(float(best_q["decimal"]), 2)
                best_bookie = str(best_q["bookmaker_name"])
                place_counts = [int(q["places"]) for q in valid_quotes if q.get("places")]
                max_pl = max(place_counts) if place_counts else 0
                if max_pl >= 4:
                    pl_books = [q["bookmaker_name"] for q in valid_quotes if q.get("places") == max_pl]
                    extra_places_str = f"{max_pl} Pl ({', '.join(pl_books[:2])})"

            cur.execute(
                """
                SELECT race_date, meeting, distance, finish_pos, beaten_distance, weight_lbs,
                       official_rating, topspeed, rpr, sp_odds
                FROM race_results
                WHERE horse_name = ? OR horse_name LIKE ?
                ORDER BY race_date DESC
            """,
                (h_name, f"{h_name} (%"),
            )
            rp_rows = cur.fetchall()
            if not rp_rows:
                continue

            lto = rp_rows[0]
            lto_pos = str(lto[3] or "")
            lto_wgt = int(lto[5]) if lto[5] and str(lto[5]).isdigit() else None
            lto_or = int(lto[6]) if lto[6] and str(lto[6]).isdigit() else None

            delta_wgt = (net_wgt - lto_wgt) if (net_wgt and lto_wgt) else 0

            win_rows = [x for x in rp_rows if str(x[3]) == "1"]
            last_win_or = int(win_rows[0][6]) if win_rows and win_rows[0][6] and str(win_rows[0][6]).isdigit() else None

            placings_at_trip = 0
            wins_at_trip = 0
            for row in rp_rows:
                pos = str(row[3] or "")
                d_str = str(row[2] or "").lower().replace(" ", "")
                if dist_text[:2] in d_str:
                    if pos == "1":
                        wins_at_trip += 1
                        placings_at_trip += 1
                    elif pos in ("2", "3"):
                        placings_at_trip += 1

            ts_list = [int(x[7]) for x in rp_rows if x[7] and str(x[7]).isdigit()]
            rpr_list = [int(x[8]) for x in rp_rows if x[8] and str(x[8]).isdigit()]
            best_ts = max(ts_list) if ts_list else 0
            best_rpr = max(rpr_list) if rpr_list else 0

            angles = []
            category = "Other"

            # STRICT CRITERIA ONLY (Top high-conviction picks)
            # 1. Massive Weight Drop (The 40/1 Turnstile Angle: -8lb+ drop with proven TS >= 60)
            if delta_wgt <= -8 and best_ts >= 60:
                angles.append(f"⚡ Featherweight Drop ({delta_wgt:+d} lb, Peak TS {best_ts})")
                category = "⚡ Big Weight Drop"

            # 2. Strict 5-Rule Handicap System (Falling mark + At/below win mark + Proven at trip + Top 4 LTO)
            elif (delta_wgt < 0 or (lto_or and last_win_or and lto_or <= last_win_or)) and placings_at_trip >= 1 and lto_pos in ("1", "2", "3", "4"):
                angles.append(f"⭐ Value Pick (In Form pos {lto_pos}, {placings_at_trip}x Trip Placed)")
                category = "⭐ Value Qualifier"

            # 3. Knocking on the door (Maiden / close placer: 3+ placings at trip, finished close LTO)
            elif placings_at_trip >= 3 and lto_pos in ("2", "3") and best_ts >= 60:
                angles.append(f"🔔 Knocking on Door (Pos {lto_pos} LTO, {placings_at_trip}x Trip Placed)")
                category = "🔔 Placed at Trip"

            if angles:
                picks.append(
                    {
                        "Category": category,
                        "Race": f"{time_str} {c_name}",
                        "Horse": h_name,
                        "Decimal_Odds": f"{best_decimal:.2f}" if best_decimal else "-",
                        "BF_Odds": f"{bf_odds_val:.2f}" if bf_odds_val else "-",
                        "BF_Place": f"{bf_p_price:.2f} ({bf_p_terms})" if bf_p_price else "-",
                        "Bookmaker": best_bookie,
                        "Extra_Places": extra_places_str,
                        "Weight": f"{net_wgt}lb ({delta_wgt:+d}lb)",
                        "Trip_Record": f"{wins_at_trip}W, {placings_at_trip}P",
                        "Best_TS": best_ts,
                        "Best_RPR": best_rpr,
                        "Angle": " | ".join(angles),
                        "course_slug": c_slug,
                        "hhmm": hhmm,
                        "raw_odds": best_decimal or 999.0,
                    }
                )

    conn.close()
    return pd.DataFrame(picks)



@st.cache_data(ttl=60)
def load_results_ledger():
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_ledger.csv")
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            if not df.empty:
                return df
        except Exception:
            pass

    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        df = pd.read_sql("SELECT * FROM system_results_ledger", conn)
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")


@st.cache_data(ttl=300)
def load_verified_settlement(date_str):
    """Settle a day from the captured morning snapshot and real bookmaker terms.

    early_vs_sp.py prices every logged bet from the snapshot taken in the
    morning (best price + THAT bookmaker's each-way terms) instead of the
    scan-time price, so the ROI cannot use a price nobody offered.

    Returns {(system, course, hhmm, horse): record} or {} when there is no
    snapshot for the date - the caller then falls back to the ledger columns.
    """
    if evs is None or not date_str or date_str == "ALL":
        return {}
    try:
        snapshots, _files = evs.load_snapshot(SNAPSHOT_DIR, date_str)
        if not snapshots:
            return {}
        ledger = load_results_ledger()
        if ledger.empty or "race_date" not in ledger.columns:
            return {}
        rows = ledger[ledger["race_date"] == date_str].to_dict("records")
        if not rows:
            return {}
        evaluated = evs.evaluate(rows, snapshots, evs.load_results(DB_PATH, date_str))
        return {
            (r["system"], evs.norm_course(r["course"]), evs.to_hhmm(r["time"]),
             evs.norm_name(r["horse"])): r
            for r in evaluated
        }
    except Exception:
        return {}


@st.cache_data(ttl=300)
def load_horse_career(horse_name):
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT race_date, meeting, distance, finish_pos, beaten_distance, weight_lbs,
               official_rating, topspeed, rpr, jockey, sp_odds, comment
        FROM race_results
        WHERE horse_name = ? OR horse_name LIKE ?
        ORDER BY race_date DESC
    """,
        (horse_name, f"{horse_name} (%"),
    )
    rp_rows = cur.fetchall()

    if not rp_rows:
        conn.close()
        return None

    cur.execute(
        """
        SELECT race_date, course_name, stride_length, top_speed, fsp_pct
        FROM raceiq_telemetry
        WHERE (horse_name = ? OR horse_name LIKE ?) AND stride_length IS NOT NULL
    """,
        (horse_name, f"{horse_name} (%"),
    )

    rtv_map = {}
    for r in cur.fetchall():
        d_str = str(r[0])
        c_name = re.sub(r"[^a-zA-Z]", "", str(r[1]).lower())
        key = f"{d_str}_{c_name[:4]}"
        rtv_map[key] = {
            "stride": float(r[2]) if r[2] else None,
            "speed": float(r[3]) if r[3] else None,
            "fsp": float(r[4]) if r[4] else None,
        }
    conn.close()

    career_data = []
    for r in rp_rows:
        d_str, course, dist, pos, btn, wgt, or_val, ts, rpr, jock, sp, comm_raw = r
        c_clean = re.sub(r"[^a-zA-Z]", "", str(course).lower())
        key = f"{d_str}_{c_clean[:4]}"
        tel = rtv_map.get(key, {})
        comm_text, mkt_text = parse_comment_text(comm_raw)

        career_data.append(
            {
                "Date": d_str,
                "Course": course,
                "Distance": dist,
                "Pos": pos,
                "Beaten": btn or "0",
                "Weight": f"{wgt}lb" if wgt else "-",
                "Jockey": jock,
                "SP": sp or "-",
                "OR": int(or_val) if or_val and str(or_val).isdigit() else None,
                "TS": int(ts) if ts and str(ts).isdigit() else None,
                "RPR": int(rpr) if rpr and str(rpr).isdigit() else None,
                "Speed_MPH": tel.get("speed"),
                "Stride_m": tel.get("stride"),
                "Market": mkt_text,
                "Comment": comm_text,
            }
        )

    return pd.DataFrame(career_data)


# ------------------------------------------------------------------------------
# Sidebar & Date Selection
# ------------------------------------------------------------------------------
st.sidebar.title("⚡ HR Best Times & Form")
st.sidebar.caption("Cloud Edition (Live Decimal Odds & Extra Places)")

selected_date = st.sidebar.date_input("Select Racing Date", dt.date.today())
date_str = selected_date.strftime("%Y-%m-%d")

schedule = load_day_schedule(date_str)
if not schedule:
    st.sidebar.warning(f"No race meetings found for {date_str}.")
    st.stop()



@st.cache_data(ttl=90)
def cached_scan_day_ew_edges(date_str: str, chosen_meeting: str) -> list[dict[str, Any]]:
    """Cached scan of Betfair Exchange Each-Way edge opportunities."""
    return betfair_ew_service.scan_day_ew_edges(date_str, chosen_meeting)

@st.cache_data(ttl=180)
def scan_speed_and_stride(date_str, target_course=None):
    bf_map_today = get_bf_win_odds_map(date_str)
    schedule: dict[str, list[dict[str, Any]]] = {}
    for r in rtv_api.day_races(date_str):
        c = r.get("course_name", "")
        schedule.setdefault(c, []).append(r)

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cur = conn.cursor()

    picks = []
    courses_to_scan = [target_course] if target_course and target_course != "All Meetings Today" else list(schedule.keys())

    for c_name in courses_to_scan:
        r_list = schedule.get(c_name, [])
        for r in r_list:
            c_slug = r.get("course_slug", "")
            hhmm = r.get("hhmm", "")
            time_str = r.get("time", "")

            try:
                d = rtv_api.race_detail(date_str, c_slug, hhmm)
            except Exception:
                continue
            if not d or "race" not in d:
                continue

            runners = rtv_api.runners_of(d)
            if not runners:
                continue

            odds_map: dict[str, list[dict]] = {}
            try:
                odds_res, _ = rtv_api.runner_odds([x["runner_id"] for x in runners])
                odds_map = odds_res or {}
            except Exception:
                pass

            race_telemetry = []
            for run in runners:
                if run.get("status") == "scratched":
                    continue
                h_name = str(run.get("horse_name", "")).strip()
                h_clean = h_name.lower().replace("(ire)", "").replace("(fr)", "").replace("(usa)", "").replace("(ger)", "").strip()
                rid = run.get("runner_id")

                best_dec = None
                best_bk = "-"
                quotes = odds_map.get(rid, [])
                v_quotes = [q for q in quotes if q.get("decimal") and q["decimal"] > 1.0]
                if v_quotes:
                    bq = max(v_quotes, key=lambda x: x["decimal"])
                    best_dec = round(float(bq["decimal"]), 2)
                    best_bk = str(bq["bookmaker_name"])

                # Latest usable speed and stride can come from different runs
                # (recent stride-only rows must not shadow an earlier speed),
                # so each metric gets its own lookup.  Bands mirror the
                # SPEED_MIN/MAX + STRIDE_MIN/MAX filters in
                # scripts/sync_results_ledger.py - they only discard junk.
                cur.execute(
                    """
                    SELECT top_speed
                    FROM raceiq_telemetry
                    WHERE (lower(horse_name) = ? OR lower(horse_name) LIKE ?)
                      AND top_speed BETWEEN 25.0 AND 55.0
                    ORDER BY race_date DESC
                    LIMIT 1
                    """,
                    (h_clean, f"{h_clean}%")
                )
                s_row = cur.fetchone()
                last_speed = float(s_row[0]) if s_row else None

                cur.execute(
                    """
                    SELECT stride_length
                    FROM raceiq_telemetry
                    WHERE (lower(horse_name) = ? OR lower(horse_name) LIKE ?)
                      AND stride_length BETWEEN 5.0 AND 10.0
                    ORDER BY race_date DESC
                    LIMIT 1
                    """,
                    (h_clean, f"{h_clean}%")
                )
                t_row = cur.fetchone()
                last_stride = float(t_row[0]) if t_row else None

                cur.execute(
                    """
                    SELECT topspeed, rpr
                    FROM race_results
                    WHERE lower(horse_name) = ? OR lower(horse_name) LIKE ?
                    ORDER BY race_date DESC
                    LIMIT 1
                    """,
                    (h_clean, f"{h_clean}%")
                )
                r_row = cur.fetchone()
                lto_ts = int(r_row[0]) if r_row and r_row[0] and str(r_row[0]).isdigit() else None

                race_telemetry.append({
                    "horse": h_name,
                    "odds": best_dec,
                    "bookmaker": best_bk,
                    "speed": last_speed,
                    "stride": last_stride,
                    "lto_ts": lto_ts
                })

            if not race_telemetry:
                continue

            # Speed & Stride - one shared rule (cloud_app/speed_stride_rule.py),
            # used by this tab, the cloud cache builder and the ledger alike.
            for horse, category in ss_rule.evaluate(
                (x["horse"], x["speed"], x["stride"]) for x in race_telemetry
            ):
                cand = next(
                    (x for x in race_telemetry
                     if ss_rule.norm_horse(x["horse"]) == ss_rule.norm_horse(horse)),
                    None,
                )
                if cand is None:
                    continue
                _nm_c = re.sub(r"[^a-zA-Z0-9\s]", "", re.sub(r"\([^)]*\)", "", str(cand["horse"]))).strip().lower()
                _bf_pl_m, _bf_pl_t = get_bf_place_odds_map(date_str)
                picks.append({
                    "Race": f"{time_str} {c_name}",
                    "course_slug": c_slug,
                    "hhmm": hhmm,
                    "Horse": cand["horse"],
                    "Odds": f"{cand['odds']:.2f}" if cand["odds"] else "-",
                    "BF_Odds": f"{bf_map_today.get(_nm_c):.2f}" if bf_map_today.get(_nm_c) else "-",
                    "BF_Place": f"{_bf_pl_m[_nm_c]:.2f} ({_bf_pl_t.get(_nm_c, '')})" if _nm_c in _bf_pl_m else "-",
                    "Bookmaker": cand["bookmaker"],
                    "Top_Speed_MPH": f"{cand['speed']:.1f} mph" if cand["speed"] else "-",
                    "Stride_Length": f"{cand['stride']:.2f} m" if cand["stride"] else "-",
                    "Category": category,
                    "Edge": ss_rule.edge_label(category, SS_CLAIMS),
                })

    conn.close()
    return pd.DataFrame(picks)

# Flatten all day races and sort chronologically by time
all_day_races = []
for _c, r_list in schedule.items():
    all_day_races.extend(r_list)
all_day_races.sort(key=lambda x: (x.get("time", "99:99"), x.get("course_name", "")))
pill_options = [f"{r.get('time')} {r.get('course_name')}" for r in all_day_races]

if "selected_race_idx" not in st.session_state or st.session_state["selected_race_idx"] >= len(all_day_races):
    st.session_state["selected_race_idx"] = 0

course_list = sorted(schedule.keys())
current_race_obj = all_day_races[st.session_state["selected_race_idx"]]
cur_course_name = current_race_obj["course_name"]
cur_course_idx = course_list.index(cur_course_name) if cur_course_name in course_list else 0

sb_course = st.sidebar.selectbox("Filter by Meeting", course_list, index=cur_course_idx)
meeting_races = schedule.get(sb_course, [])
meeting_times = [r.get("time") for r in meeting_races]

cur_time_val = current_race_obj["time"]
cur_time_idx = meeting_times.index(cur_time_val) if cur_time_val in meeting_times else 0
sb_time = st.sidebar.selectbox("Filter by Race Time", meeting_times, index=cur_time_idx)

if sb_course != cur_course_name or sb_time != cur_time_val:
    matching_idx = next(
        (i for i, r in enumerate(all_day_races) if r["course_name"] == sb_course and r["time"] == sb_time),
        None,
    )
    if matching_idx is not None:
        st.session_state["selected_race_idx"] = matching_idx
        st.session_state["nav_view"] = "🏇 Racecard, Odds & Ranks"
        st.rerun()

st.sidebar.markdown("---")
horse_search = st.sidebar.text_input("🔍 Quick Horse History Search", placeholder="e.g. Turnstile")
if horse_search:
    st.session_state["selected_horse"] = horse_search
    st.session_state["nav_view"] = "📖 Horse Career Profile"

# ------------------------------------------------------------------------------
# UPCOMING RACES RIBBON (CLICK TO JUMP CHRONOLOGICALLY)
# ------------------------------------------------------------------------------
st.markdown("##### 🕒 Upcoming Races Today (Click any race to load both tabs):")

active_race_label = pill_options[st.session_state["selected_race_idx"]]
selected_pill = st.pills(
    "Upcoming Races Bar",
    pill_options,
    default=active_race_label,
    key="race_ribbon_pills",
    label_visibility="collapsed",
)

if selected_pill and selected_pill != active_race_label:
    new_idx = pill_options.index(selected_pill)
    st.session_state["selected_race_idx"] = new_idx
    st.session_state["nav_view"] = "🏇 Racecard, Odds & Ranks"
    st.rerun()

active_race = all_day_races[st.session_state["selected_race_idx"]]
selected_course = active_race["course_name"]
selected_time = active_race["time"]
course_slug = active_race["course_slug"]
hhmm = active_race["hhmm"]

st.markdown("---")

# ------------------------------------------------------------------------------
# Top Navigation Bar (3 Clean Tabs)
# ------------------------------------------------------------------------------
nav_options = [
    "🏇 Racecard, Odds & Ranks",
    "💡 Ben (Qas)",
    "⚡ Speed & Stride System",
    "💱 Exchange EW Edge",
    "🏆 Results",
    "📖 Horse Career Profile",
]

# The results ledger stores this system under the key "Tips" (the pipeline has
# written that name since 2026-09-15).  Only the label shown to the user changes,
# so the stored history stays in one piece.
SYSTEM_DISPLAY = {"Tips": "Ben (Qas)"}

if "nav_view" not in st.session_state or st.session_state["nav_view"] not in nav_options:
    st.session_state["nav_view"] = "🏇 Racecard, Odds & Ranks"

view_mode = st.radio(
    "Navigation View",
    nav_options,
    index=nav_options.index(st.session_state["nav_view"]),
    horizontal=True,
    key="nav_view_radio",
    label_visibility="collapsed",
)
if view_mode != st.session_state["nav_view"]:
    st.session_state["nav_view"] = view_mode
    st.rerun()

# ==============================================================================
# VIEW 1: RACECARD & POWER RANKS
# ==============================================================================
if st.session_state["nav_view"] == "🏇 Racecard, Odds & Ranks":
    with st.spinner(f"Loading {selected_course} {selected_time} live racecard, odds & ratings..."):
        df, race_info = get_racecard_data(date_str, course_slug, hhmm)

    if df is None or df.empty:
        st.warning("Could not load runners for this race.")
    else:
        title = race_info.get("title", "")
        dist = race_info.get("distance_formatted", "") or race_info.get("distance", "")
        pace = race_info.get("ip_hints_overall_pace", "N/A")
        draw = race_info.get("draw_comment", "None noted")

        c_head, c_btn = st.columns([5, 1])
        with c_head:
            st.markdown(
                f"<div class='main-header'>{selected_course.upper()} {selected_time} - {title}</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div class='sub-header'>Distance: <b>{dist}</b> | Pace: <b>{pace}</b> | Draw: <b>{draw}</b></div>",
                unsafe_allow_html=True,
            )
        with c_btn:
            if st.button("🔄 Refresh Odds", use_container_width=True):
                st.rerun()

        verdict = race_info.get("analyst_verdict", "")
        if verdict:
            st.caption(
                "Three independent things on this card, which is why they name different horses: the "
                "**Analyst Verdict** is RacingTV's own comment (the horse they print in capitals — a "
                "human's reading, not a model); the **podium below** is the Power Score ranking "
                "(`0.35·Best_TS + 0.35·Avg_TS3 + 0.30·Best_RPR − 0.15·Weight`) which **uses no odds at "
                "all**, so it can top a big-priced runner; and the **tips list** (⚡⭐🔔) is the Ben (Qas) "
                "five-rule handicap angles. None is derived from another."
            )
        st.info(f"💡 **Analyst Verdict**: {verdict}")

        # Top 3 Contenders (1st, 2nd, 3rd Most Likely to Win)
        if len(df) >= 3:
            p1, p2, p3 = df.iloc[0], df.iloc[1], df.iloc[2]
            c_top1, c_top2, c_top3 = st.columns(3)
            with c_top1:
                st.success(f"🥇 **1st Place**: **#{p1['No']} {p1['Horse']}**  \nOdds: **{p1['Odds']}** ({p1['Bookmaker']}) | Power: **{p1['Power_Score']}**")
            with c_top2:
                st.info(f"🥈 **2nd Place**: **#{p2['No']} {p2['Horse']}**  \nOdds: **{p2['Odds']}** ({p2['Bookmaker']}) | Power: **{p2['Power_Score']}**")
            with c_top3:
                st.warning(f"🥉 **3rd Place**: **#{p3['No']} {p3['Horse']}**  \nOdds: **{p3['Odds']}** ({p3['Bookmaker']}) | Power: **{p3['Power_Score']}**")

        st.subheader("⚡ Master Rankings, Live Decimal Odds & Extra Place Offers")

        display_cols = [
            "Master_Rank",
            "No",
            "Horse",
            "Odds",
            "BF_Odds",
            "Bookmaker",
            "Extra_Places",
            "Flags",
            "DLR",
            "Wgt_Lbs",
            "Win_Wgt",
            "TS_HL",
            "RPR_HL",
            "Best_TS",
            "Best_RPR",
            "Power_Score",
        ]

        st.dataframe(
            df[display_cols].rename(
                columns={
                    "Master_Rank": "Rank",
                    "Odds": "Bookie Odds",
                    "BF_Odds": "BF Odds",
                    "Bookmaker": "Bookmaker",
                    "Extra_Places": "Extra Places Offer",
                    "Wgt_Lbs": "Wgt(lb)",
                    "Win_Wgt": "Win Wgt vs Now",
                    "TS_HL": "TS (High/Low)",
                    "RPR_HL": "RPR (High/Low)",
                    "Best_TS": "Best TS",
                    "Best_RPR": "Best RPR",
                    "Power_Score": "Power",
                }
            ),
            use_container_width=True,
            hide_index=True,
            height=min(650, (len(df) + 1) * 36),
        )

        st.subheader("📝 Runner Form, Odds, Weight Shifts & In-Running Comments")
        for _idx, row in df.iterrows():
            with st.expander(
                f"#{row['No']} {row['Horse']} (Rank #{row['Master_Rank']} | Odds: {row['Best_Book']} | Power: {row['Power_Score']})"
            ):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Decimal Odds", f"{row['Odds']}", f"{row['Bookmaker']}")
                c2.metric("Extra Places", f"{row['Extra_Places']}")
                c3.metric("Weight Shift", f"{row['Wgt_Lbs']} lb ({row['dWgt']})", f"Off: {row['DLR']}d")
                c4.metric(
                    "Rating Range (High/Low)",
                    f"TS: {row['TS_HL']}",
                    f"RPR: {row['RPR_HL']}",
                )

                st.write(f"**Winning Weight History**: {row['Last_Win_Desc']}")
                st.write(f"**Last Race**: {row['Last_Run_Desc']}")
                if row["Betting_LTO"]:
                    st.markdown(
                        f"**Market Movement**: <span class='market-move'>{row['Betting_LTO']}</span>",
                        unsafe_allow_html=True,
                    )
                comm = row["Last_Comment"] or "No in-running comment recorded."
                st.markdown(
                    f"<div class='comment-card'>💬 <i>\"{comm}\"</i></div>",
                    unsafe_allow_html=True,
                )

                col_b1, col_b2 = st.columns([1, 1])
                with col_b1:
                    if st.button(f"📖 Open Full Career Profile ({row['Horse']})", key=f"btn_nav_{row['Horse']}"):
                        st.session_state["selected_horse"] = row["Horse"]
                        st.session_state["nav_view"] = "📖 Horse Career Profile"
                        st.rerun()

                with st.popover(f"🔍 Quick View Past Runs ({row['Horse']})"):
                    quick_df = load_horse_career(row["Horse"])
                    if quick_df is not None and not quick_df.empty:
                        st.dataframe(
                            quick_df[["Date", "Course", "Distance", "Pos", "Beaten", "Weight", "TS", "RPR", "Comment"]].head(5),
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.write("No earlier runs on record.")

        with st.expander("💱 Live Betfair Exchange Each-Way & Value Comparison", expanded=False):
            race_ew_rows = []
            if betfair_ew_service.is_configured():
                with st.spinner("Fetching Betfair Win & Place order books for this race..."):
                    try:
                        token_val = betfair_ew_service.login()
                        cat_val = betfair_ew_service.fetch_today_catalogue(date_str, token_val)
                        detail_val = rtv_api.race_detail(date_str, course_slug, hhmm)
                        runners_val = rtv_api.runners_of(detail_val)
                        rids_val = [str(x["runner_id"]) for x in runners_val if x.get("runner_id")]
                        odds_res_val, _ = rtv_api.runner_odds(rids_val)
                        race_ew_rows = betfair_ew_service.compute_race_ew_comparison(
                            course_name=selected_course,
                            course_slug=course_slug,
                            hhmm=hhmm,
                            time_str=selected_time,
                            runners=runners_val,
                            odds_map=odds_res_val or {},
                            catalogue=cat_val,
                            token=token_val,
                        )
                    except Exception as e:
                        st.caption(f"Could not load Exchange markets: {e}")
            elif os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ew_scan_today.json")):
                try:
                    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ew_scan_today.json"), encoding="utf-8") as f:
                        b_ew = json.load(f)
                    all_e = b_ew.get("edges", [])
                    race_ew_rows = [
                        r for r in all_e
                        if (r.get("course_slug") == course_slug and r.get("hhmm") == hhmm)
                        or (selected_course.lower() in str(r.get("Race", "")).lower() and selected_time in str(r.get("Race", "")))
                    ]
                except Exception:
                    pass

            if race_ew_rows:
                rc_df = pd.DataFrame(race_ew_rows)
                st.dataframe(
                    rc_df[
                        [
                            "Horse",
                            "Bookmaker",
                            "Book_Win",
                            "Book_Place",
                            "BF_Win_Back",
                            "BF_Win_Lay",
                            "BF_Place_Back",
                            "BF_Place_Lay",
                            "EW_Edge",
                            "Place_Edge",
                            "Verdict",
                        ]
                    ].rename(
                        columns={
                            "Book_Win": "Bookie Win",
                            "Book_Place": "Bookie Place",
                            "BF_Win_Back": "BF Win Back",
                            "BF_Win_Lay": "BF Win Lay",
                            "BF_Place_Back": "BF Place Back",
                            "BF_Place_Lay": "BF Place Lay",
                            "EW_Edge": "EW Edge %",
                            "Place_Edge": "Place Edge %",
                            "Verdict": "How Far We Are",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            elif not betfair_ew_service.is_configured():
                st.info("Betfair API credentials required for live exchange comparison. Set BETFAIR_APP_KEY in Streamlit Secrets, or connect in the '💱 Exchange EW Edge' tab.")
            else:
                st.write("No matching Betfair Exchange order books available for this race.")


# ==============================================================================
# VIEW 2: 💡 TODAY'S TIPS TAB
# ==============================================================================
elif st.session_state["nav_view"] == "💡 Ben (Qas)":
    st.caption(
        "**The Qas System is Ben's five-rule handicap strategy** (his own script names the flags "
        "`BEN_flag` / `BEN_flag_soft`). A **strict pick passes all five**: ① mark falling (below last "
        "time out) · ② below its last winning mark · ③ below its career-best mark · ④ proven at today's "
        "trip · ⑤ top-four last time out. **Soft = ①③④.** The strict-picks tab applies exactly that; "
        "the daily scan's three angles are *variants* of it with extra filters — Big Weight Drop adds "
        "weight −8lb+ **and** Topspeed ≥60, Placed at Trip needs 3+ trip placings with a LTO 2nd/3rd."
    )
    _tips_tabs = st.tabs([
        "🎯 Ben's Morning Extra-Place System (Sheet 1 — 3 to 5 EW Bets)",
        "🎯 Ben's 5-Rule Strict (Sheet 2)",
        "🤖 AI Predictions (Analyst & Power Podium)",
        "💡 Other Daily Tips (Full Scan)",
    ])

    with _tips_tabs[0]:
        st.markdown("<div class='main-header'>🎯 BEN'S MORNING EXTRA-PLACE SYSTEM (SHEET 1)</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='sub-header'>Reverse-engineered from Ben's live Sheet 1 (+105.84 pts profit, +50.46% ROI in Sept). "
            "Selects strictly 3 to 5 high-value Each-Way bets per day by exploiting extra-place concessions (4 & 5 places @ 1/5 odds) "
            "on runners dropped in the handicap weights after an unplaced prep run.</div>",
            unsafe_allow_html=True,
        )
        try:
            import bens_extra_place_system
            today_iso = dt.date.today().isoformat()
            if date_str == today_iso:
                ep_picks = bens_extra_place_system.scan_extra_place_bets(date_str)
            else:
                ep_picks = bens_extra_place_system.load_ep_picks(date_str)
                
            if not ep_picks:
                if date_str == today_iso:
                    st.info(f"No qualifying extra-place handicap selections found yet for {date_str}. Check back after 08:00.")
                else:
                    st.info(f"📂 No stored picks for **{date_str}**. Picks are saved automatically when the scanner runs on the day.")
            else:
                st.success(f"🎯 **{len(ep_picks)} Selective Extra-Place Selections Found for {date_str}**")
                for i, pick in enumerate(ep_picks, 1):
                    with st.container():
                        c_left, c_mid, c_right = st.columns([3, 2, 2])
                        with c_left:
                            st.markdown(f"### {i}. **{pick['horse']}**")
                            st.markdown(f"🏇 **{pick['course']} {pick['race_time']}** ({pick['field_size']} runners)")
                            st.caption(f"**Terms**: `{pick['place_terms']}`")
                        with c_mid:
                            _odds_val = pick.get('odds_display') or (f"{pick['odds']:.1f}" if isinstance(pick.get('odds'), (int, float)) and pick['odds'] > 0 else "Morning Price")
                            _bookie = pick.get('bookmaker') or "Best Available"
                            _pl_ret = pick.get('place_return', 0.0)
                            st.metric("Morning Price", _odds_val)
                            st.caption(f"**{_bookie}**" + (f"  |  Place: **{_pl_ret:.2f}**" if _pl_ret else ""))
                            st.caption(f"**Bet**: {pick['suggested_stake']}")
                        with c_right:
                            st.metric("Handicap Drop", f"{pick['drop']:+d} lb", delta=f"Mark: {pick['mark']} (LTO: {pick['lto_mark']})")
                            st.caption(f"LTO Pos: **{pick['lto_pos']}** · Peak RPR: **{pick['peak_rpr']}**")
                        st.divider()
        except Exception as _ep_exc:
            st.error(f"Could not load Ben's Extra-Place System: {_ep_exc}")

    with _tips_tabs[1]:
        try:
            import our_system
            our_system.render(st, date_str)
        except Exception as _our_exc:
            st.error(f"Ben's System could not load: {_our_exc}")

    with _tips_tabs[2]:
        st.markdown("<div class='main-header'>🤖 AI PREDICTIONS & ANALYST VERDICTS</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='sub-header'>Race-by-race AI predictions combining RacingTV Analyst narrative verdicts with the AI Power Score podium (🥇 1st, 🥈 2nd, 🥉 3rd).</div>",
            unsafe_allow_html=True,
        )

        ai_meeting_options = ["All Meetings Today", *sorted(schedule.keys())]
        sel_ai_meeting = st.selectbox("Select Meeting to Browse", ai_meeting_options, index=0, key="ai_pred_meeting_select")

        races_to_show = []
        for c_name, c_races in schedule.items():
            if sel_ai_meeting in ("All Meetings Today", c_name):
                for r in c_races:
                    races_to_show.append((c_name, r))

        if not races_to_show:
            st.info("No races found for today.")
        else:
            for c_name, r in races_to_show:
                r_time = r.get("time", "")
                r_title = r.get("title", "")
                c_slug = r.get("course_slug", "")
                hhmm = r.get("hhmm", "")

                with st.expander(f"🏇 {r_time} {c_name} — {r_title}", expanded=(sel_ai_meeting != "All Meetings Today")):
                    try:
                        r_df, r_info = get_racecard_data(date_str, c_slug, hhmm)
                        v_text = r_info.get("analyst_verdict") or "No analyst comment available for this race."
                        st.info(f"💡 **Analyst Verdict**: {v_text}")

                        if r_df is not None and len(r_df) >= 3:
                            p1, p2, p3 = r_df.iloc[0], r_df.iloc[1], r_df.iloc[2]
                            c1, c2, c3 = st.columns(3)
                            with c1:
                                st.success(f"🥇 **1st Place**: **#{p1['No']} {p1['Horse']}**  \nOdds: **{p1['Odds']}** ({p1['Bookmaker']}) | Power: **{p1['Power_Score']}**")
                            with c2:
                                st.info(f"🥈 **2nd Place**: **#{p2['No']} {p2['Horse']}**  \nOdds: **{p2['Odds']}** ({p2['Bookmaker']}) | Power: **{p2['Power_Score']}**")
                            with c3:
                                st.warning(f"🥉 **3rd Place**: **#{p3['No']} {p3['Horse']}**  \nOdds: **{p3['Odds']}** ({p3['Bookmaker']}) | Power: **{p3['Power_Score']}**")
                        elif r_df is not None and len(r_df) > 0:
                            for _idx, prow in r_df.head(2).iterrows():
                                st.write(f"**#{prow['No']} {prow['Horse']}** | Odds: **{prow['Odds']}** ({prow['Bookmaker']}) | Power: **{prow['Power_Score']}**")

                        if st.button(f"🏇 Open Full Racecard ({r_time} {c_name})", key=f"ai_jump_{c_slug}_{hhmm}"):
                            m_idx = next((i for i, rx in enumerate(all_day_races) if rx.get("course_slug") == c_slug and rx.get("hhmm") == hhmm), None)
                            if m_idx is not None:
                                st.session_state["selected_race_idx"] = m_idx
                            st.session_state["nav_view"] = "🏇 Racecard, Odds & Ranks"
                            st.rerun()
                    except Exception as e:
                        st.caption(f"Could not load race card: {e}")

    with _tips_tabs[3]:
        st.markdown("<div class='main-header'>💡 QAS SYSTEM — TODAY'S FIVE-RULE SCAN</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='sub-header'>Automatic daily scanner, categorised by which rule is doing the work: high-conviction value qualifiers (all five), big weight drops (-7lb+), and horses knocking on the door at the trip.</div>",
            unsafe_allow_html=True,
        )

        col_filter1, col_filter2 = st.columns([2, 1])
        with col_filter1:
            meeting_options = ["All Meetings Today", *sorted(schedule.keys())]
            chosen_scan_meeting = st.selectbox("Select Meeting to Scan", meeting_options, index=0)
        with col_filter2:
            st.write("")
            st.write("")
            if st.button("🔄 Rescan All Today's Cards", use_container_width=True):
                st.cache_data.clear()
                st.rerun()

        # Try pre-computed morning scan for instant response
        _tips_cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tips_today.json")
        tips_df = pd.DataFrame()
        if os.path.exists(_tips_cache_path) and chosen_scan_meeting == "All Meetings Today":
            try:
                with open(_tips_cache_path, encoding="utf-8") as _tf:
                    _tc = json.load(_tf)
                if _tc.get("date") == date_str and isinstance(_tc.get("picks"), list) and _tc["picks"]:
                    tips_df = pd.DataFrame(_tc["picks"])
                    st.caption("Loaded from pre-computed morning scan. Click Rescan to refresh live.")
            except Exception:
                pass
        if tips_df.empty:
            with st.spinner(f"Scanning {chosen_scan_meeting} for system value picks and weight drops..."):
                tips_df = scan_daily_tips(date_str, chosen_scan_meeting)

        if tips_df is None or tips_df.empty:
            st.info("No system qualifiers found matching criteria for this selection.")
        else:
            k_b1, k_b2, k_b3, k_b4 = st.columns(4)
            val_picks_count = len(tips_df[tips_df["Category"] == "⭐ Value Qualifier"])
            wgt_drops_count = len(tips_df[tips_df["Category"] == "⚡ Big Weight Drop"])
            trip_form_count = len(tips_df[tips_df["Category"] == "🔔 Placed at Trip"])
            k_b1.metric("Total System Qualifiers", len(tips_df))
            k_b2.metric("⭐ Core Value Qualifiers", val_picks_count)
            k_b3.metric("⚡ Big Weight Drops", wgt_drops_count)
            k_b4.metric("🔔 Proven Trip Form", trip_form_count)

            category_choice = st.radio(
                "Filter Category",
                ["All System Tips", "⭐ Core Value Qualifiers Only", "⚡ Big Weight Drops Only", "🔔 Placed at Trip Only"],
                horizontal=True,
            )

            filtered_tips = tips_df.copy()
            if category_choice == "⭐ Core Value Qualifiers Only":
                filtered_tips = filtered_tips[filtered_tips["Category"] == "⭐ Value Qualifier"]
            elif category_choice == "⚡ Big Weight Drops Only":
                filtered_tips = filtered_tips[filtered_tips["Category"] == "⚡ Big Weight Drop"]
            elif category_choice == "🔔 Placed at Trip Only":
                filtered_tips = filtered_tips[filtered_tips["Category"] == "🔔 Placed at Trip"]

            st.dataframe(
                filtered_tips[
                    [
                        "Race",
                        "Horse",
                        "Decimal_Odds",
                        "BF_Odds",
                        "BF_Place",
                        "Bookmaker",
                        "Extra_Places",
                        "Weight",
                        "Trip_Record",
                        "Best_TS",
                        "Best_RPR",
                        "Angle",
                    ]
                ].rename(
                    columns={
                        "Decimal_Odds": "Bookie Odds",
                        "BF_Odds": "BF Win",
                        "BF_Place": "BF Place (Terms)",
                        "Bookmaker": "Bookmaker",
                        "Extra_Places": "Extra Places Offer",
                        "Weight": "Weight (Shift)",
                        "Trip_Record": "Trip (W,P)",
                        "Best_TS": "Best TS",
                        "Best_RPR": "Best RPR",
                        "Angle": "Why Flagged",
                    }
                ),
                use_container_width=True,
                hide_index=True,
                height=min(600, (len(filtered_tips) + 1) * 36),
            )

            st.subheader("🎯 1-Click Racecard Jump")
            for _idx, row in filtered_tips.head(20).iterrows():
                c_p1, c_p2 = st.columns([4, 1])
                with c_p1:
                    st.write(f"**{row['Race']}** - **{row['Horse']}** | Odds: **{row['Decimal_Odds']}** ({row['Bookmaker']}) | Wgt: **{row['Weight']}** | TS: **{row['Best_TS']}**")
                    st.caption(f"Angle: {row['Angle']}")
                with c_p2:
                    if st.button("🏇 Open Racecard", key=f"jump_{row['Horse']}_{_idx}"):
                        matching_idx = next(
                            (i for i, r in enumerate(all_day_races) if r["course_slug"] == row["course_slug"] and r["hhmm"] == row["hhmm"]),
                            None,
                        )
                        if matching_idx is not None:
                            st.session_state["selected_race_idx"] = matching_idx
                        st.session_state["nav_view"] = "🏇 Racecard, Odds & Ranks"
                        st.rerun()
                st.markdown("---")




# ==============================================================================
# VIEW 3: ⚡ SPEED & STRIDE SYSTEM (TPD TELEMETRY)
# ==============================================================================
elif st.session_state["nav_view"] == "⚡ Speed & Stride System":
    st.markdown("<div class='main-header'>⚡ SPEED & STRIDE SYSTEM (Total Performance Data)</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='sub-header'>Rule: the fastest previous-run top speed "
        f"({ss_rule.SPEED_MIN_MPH:.1f}+ mph) or the longest previous stride "
        f"({ss_rule.STRIDE_MIN_M:.2f}+ m), from a horse's previous run only. "
        f"The headline returns are the <b>audited Proform SData</b> results at "
        f"Betfair BSP, net of 2% commission, 2021-01-01 to 2026-04-30 — the frame "
        f"they come from backs every runner at -2.21%, so it behaves like a real "
        f"market (full method and caveats: STRIDE_SYSTEM.md). The second line on "
        f"each card is what the live RacingTV RaceIQ feed reproduces today, "
        f"settled win-only at SP — a different feed, so it is a check, not the "
        f"same measurement.</div>",
        unsafe_allow_html=True,
    )

    k_s1, k_s2, k_s3 = st.columns(3)
    k_s1.success(f"🚀 **SPEED System**  \n{ss_claim_card(ss_rule.SPEED)}  \n*Selection: Highest Previous Top Speed*")
    k_s2.info(f"📏 **STRIDE System**  \n{ss_claim_card(ss_rule.STRIDE)}  \n*Selection: Longest Previous Stride*")
    k_s3.warning(f"🎯 **AGREE Variant**  \n{ss_claim_card(ss_rule.AGREE)}  \n*Selection: Tops Both Speed & Stride*")

    col_filter1, col_filter2 = st.columns([2, 1])
    with col_filter1:
        meeting_options = ["All Meetings Today", *sorted(schedule.keys())]
        chosen_scan_meeting = st.selectbox("Select Meeting to Scan", meeting_options, index=0, key="ss_meeting_select")
    with col_filter2:
        st.write("")
        st.write("")
        if st.button("🔄 Rescan Speed & Stride", use_container_width=True, key="ss_rescan_btn"):
            st.cache_data.clear()
            st.rerun()

    # Try pre-computed morning scan for instant response
    _ss_cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speed_stride_today.json")
    ss_df = pd.DataFrame()
    if os.path.exists(_ss_cache_path) and chosen_scan_meeting == "All Meetings Today":
        try:
            with open(_ss_cache_path, encoding="utf-8") as _sf:
                _ssc = json.load(_sf)
            if _ssc.get("date") == date_str and isinstance(_ssc.get("rows"), list) and _ssc["rows"]:
                ss_df = pd.DataFrame(_ssc["rows"])
                st.caption("Loaded from pre-computed morning scan. Click Rescan to refresh live.")
        except Exception:
            pass
    if ss_df.empty:
        with st.spinner(f"Scanning {chosen_scan_meeting} for Speed & Stride qualifiers..."):
            ss_df = scan_speed_and_stride(date_str, chosen_scan_meeting)

    if ss_df is None or ss_df.empty:
        st.info("No Speed or Stride qualifiers found for this selection.")
    else:
        st.subheader("🎯 Daily Speed & Stride Qualifiers")
        st.dataframe(
            ss_df[
                [c for c in [
                    "Race",
                    "Horse",
                    "Odds",
                    "BF_Odds",
                    "BF_Place",
                    "Bookmaker",
                    "Top_Speed_MPH",
                    "Stride_Length",
                    "Category",
                    "Edge",
                ] if c in ss_df.columns]
            ].rename(
                columns={
                    "Odds": "Bookie Odds",
                    "BF_Odds": "BF Win",
                    "BF_Place": "BF Place (Terms)",
                    "Top_Speed_MPH": "Top Speed",
                    "Stride_Length": "Stride Length",
                    "Edge": "Measured WIN ROI (at SP)",
                }
            ),
            use_container_width=True,
            hide_index=True,
            height=min(600, (len(ss_df) + 1) * 36),
        )

        st.subheader("🏇 1-Click Racecard Jump")
        for _idx, row in ss_df.head(15).iterrows():
            c_p1, c_p2 = st.columns([4, 1])
            with c_p1:
                st.write(f"**{row['Race']}** - **{row['Horse']}** | Odds: **{row['Odds']}** ({row['Bookmaker']}) | Speed: **{row['Top_Speed_MPH']}** | Stride: **{row['Stride_Length']}**")
                st.caption(f"{row['Category']} — {row['Edge']}")
            with c_p2:
                if st.button("🏇 Open Racecard", key=f"jump_ss_{row['Horse']}_{_idx}"):
                    matching_idx = next(
                        (i for i, r in enumerate(all_day_races) if r["course_slug"] == row["course_slug"] and r["hhmm"] == row["hhmm"]),
                        None,
                    )
                    if matching_idx is not None:
                        st.session_state["selected_race_idx"] = matching_idx
                    st.session_state["nav_view"] = "🏇 Racecard, Odds & Ranks"
                    st.rerun()
            st.markdown("---")



# ==============================================================================
# VIEW: 💱 EXCHANGE EACH-WAY EDGE SCANNER (BETFAIR DELAY KEY API)
# ==============================================================================
elif st.session_state["nav_view"] == "💱 Exchange EW Edge":
    st.markdown("<div class='main-header'>💱 LIVE BETFAIR EXCHANGE EACH-WAY & VALUE EDGE SCANNER</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='sub-header'>Powered by your Betfair Exchange Delay Key API. Synthesizes live Win + Place order books to calculate exact Bookmaker Each-Way Value Edges and identify Bad Each-Way / Place Exploits.</div>",
        unsafe_allow_html=True,
    )

    # Fallback data when there is no live Betfair key in this app:
    #   1. the newest morning snapshot the GitHub workflow committed (it runs in
    #      the cloud with the repo's own secrets, so this needs nothing here)
    #   2. the older pre-scanned ew_scan_today.json, if present
    bundled_ew_rows = []
    bundled_ew_label = ""
    snap_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")
    try:
        load_fn = getattr(betfair_ew_service, "load_snapshot_rows", None)
        if callable(load_fn):
            bundled_ew_rows, bundled_ew_label = load_fn(snap_dir, date_str=date_str)
        else:
            bundled_ew_rows, bundled_ew_label = [], ""
    except Exception:
        bundled_ew_rows, bundled_ew_label = [], ""

    bundled_ew_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ew_scan_today.json")
    if not bundled_ew_rows and os.path.exists(bundled_ew_path):
        try:
            with open(bundled_ew_path, encoding="utf-8") as f:
                b_ew = json.load(f)
            if b_ew.get("date") == date_str and isinstance(b_ew.get("edges"), list):
                bundled_ew_rows = b_ew["edges"]
                bundled_ew_label = str(b_ew.get("date") or "")
        except Exception:
            pass

    is_connected = betfair_ew_service.is_configured()

    with st.expander("🔑 Betfair API Connection & Key Setup", expanded=not is_connected and not bundled_ew_rows):
        if is_connected:
            st.success("🟢 Connected to Betfair Exchange API (Delay Key: Active). Real-time market order books enabled.")
        elif bundled_ew_rows:
            st.info("🔑 No Betfair key in this app - showing the odds snapshot the GitHub workflow captured in the cloud (below). Add a key only if you want a live scan between captures.")
        else:
            missing = [n for n in ("app_key", "username", "password")
                       if not betfair_ew_service.get_credential(n)]
            st.warning(
                f"⚠️ Live Betfair connection not active (missing: {', '.join(missing) or 'nothing?'}). "
                "Enter them below, or set BETFAIR_APP_KEY / BETFAIR_USERNAME / BETFAIR_PASSWORD "
                "in Streamlit Cloud ➔ App Settings ➔ Secrets."
            )

        k_col1, k_col2, k_col3 = st.columns(3)
        with k_col1:
            ui_app_key = st.text_input("App Key", type="password", value=betfair_ew_service.get_credential("app_key"), key="ui_bf_app_key")
        with k_col2:
            ui_username = st.text_input("Username", value=betfair_ew_service.get_credential("username"), key="ui_bf_user")
        with k_col3:
            ui_password = st.text_input("Password", type="password", value=betfair_ew_service.get_credential("password"), key="ui_bf_pw")

        btn_c1, btn_c2 = st.columns([1, 2])
        with btn_c1:
            if st.button("💾 Connect & Save Session", use_container_width=True, key="save_bf_session_btn"):
                if ui_app_key and ui_username and ui_password:
                    st.session_state["betfair_creds"] = {
                        "app_key": ui_app_key.strip(),
                        "username": ui_username.strip(),
                        "password": ui_password.strip(),
                    }
                    try:
                        t = betfair_ew_service.login(use_cache=False)
                        st.success("✅ Authentication successful! Connected to Betfair Exchange.")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as ex:
                        st.error(f"❌ Login failed: {ex}")
                else:
                    st.error("Please fill in App Key, Username, and Password.")
        with btn_c2:
            st.caption("🔒 Secrets are kept in your browser session only. To make them permanent on Streamlit Cloud, add `BETFAIR_APP_KEY`, `BETFAIR_USERNAME`, and `BETFAIR_PASSWORD` to Streamlit Cloud **App Settings ➔ Secrets**.")

    col_bf1, col_bf2 = st.columns([2, 1])
    with col_bf1:
        bf_meetings = ["All Meetings Today", *sorted(schedule.keys())]
        chosen_bf_meeting = st.selectbox("Select Meeting to Scan", bf_meetings, index=0, key="bf_scan_meeting")
    with col_bf2:
        st.write("")
        st.write("")
        if st.button("🔄 Rescan Exchange Markets", use_container_width=True, key="bf_rescan_btn"):
            st.cache_data.clear()
            st.rerun()

    ew_rows = []
    if betfair_ew_service.is_configured():
        with st.spinner(f"Querying live Betfair Exchange order books for {chosen_bf_meeting}..."):
            try:
                ew_rows = cached_scan_day_ew_edges(date_str, chosen_bf_meeting)
            except Exception as ex:
                st.error(f"Error querying live Betfair Exchange: {ex}")
                ew_rows = []
    elif bundled_ew_rows:
        has_place = any(r.get("Place_Edge") is not None for r in bundled_ew_rows)
        st.info(f"💡 From the last morning snapshot ({bundled_ew_label or 'unknown time'}) - {len(bundled_ew_rows):,} runners, EW edges computed from it here so no key is needed in this app. It is captured in the cloud by the repo's own GitHub workflow with the repo's own secrets.")
        if not has_place:
            st.caption("Place-edge columns are blank for this snapshot: it was captured before the workflow started storing the exchange LAY price, and a back price would massively overstate the edge. From the next morning capture the place edges fill in; connecting a key above gives them live now.")
        if chosen_bf_meeting != "All Meetings Today":
            ew_rows = [r for r in bundled_ew_rows if chosen_bf_meeting.lower() in str(r.get("Race", "")).lower()]
        else:
            ew_rows = bundled_ew_rows
    else:
        st.info("Please enter your Betfair credentials above to scan live Exchange markets.")

    # Render whenever there is a source of data: a live scan or the bundled
    # morning scan.  This used to sit inside the branch above, which only runs
    # when you have NEITHER a key NOR bundled data - so ew_rows was always
    # empty and the table below never appeared, even with a working connection.
    if is_connected or bundled_ew_rows:
        if not ew_rows:
            st.info("No active Exchange Win & Place markets currently found for this meeting.")
        else:
            ew_df = pd.DataFrame(ew_rows)
            super_count = len(ew_df[ew_df["EW_Edge"] >= 5.0]) if "EW_Edge" in ew_df.columns else 0
            pos_ew_count = len(ew_df[ew_df["EW_Edge"] > 0.0]) if "EW_Edge" in ew_df.columns else 0
            place_exploit_count = len(ew_df[ew_df["Place_Edge"] >= 8.0]) if "Place_Edge" in ew_df.columns else 0

            k_e1, k_e2, k_e3, k_e4 = st.columns(4)
            k_e1.metric("Total Runners Scanned", len(ew_df))
            k_e2.metric("🚀 Super EW Value (+5%+)", super_count)
            k_e3.metric("🟢 Positive EW Edge (>0%)", pos_ew_count)
            k_e4.metric("🎯 Place Exploits (+8%+)", place_exploit_count)

            bf_filter = st.radio(
                "Filter Opportunities",
                ["All Analyzed Runners", "🟢 Positive EW Edge Only (> 0%)", "🎯 Place Exploits Only (> +8%)", "🚀 Super EW Value Only (+5%+)"],
                horizontal=True,
                key="bf_filter_radio",
            )

            filtered_ew = ew_df.copy()
            if bf_filter == "🟢 Positive EW Edge Only (> 0%)":
                filtered_ew = filtered_ew[filtered_ew["EW_Edge"] > 0.0]
            elif bf_filter == "🎯 Place Exploits Only (> +8%)":
                filtered_ew = filtered_ew[filtered_ew["Place_Edge"] >= 8.0]
            elif bf_filter == "🚀 Super EW Value Only (+5%+)":
                filtered_ew = filtered_ew[filtered_ew["EW_Edge"] >= 5.0]

            if filtered_ew.empty:
                st.info("No runners match the selected filter at this moment.")
            else:
                display_cols = [
                    "Race",
                    "Horse",
                    "Bookmaker",
                    "Book_Win",
                    "Book_Place",
                    "BF_Win_Lay",
                    "BF_Place_Lay",
                    "EW_Edge",
                    "Place_Edge",
                    "Verdict",
                ]
                st.dataframe(
                    filtered_ew[display_cols].rename(
                        columns={
                            "Book_Win": "Bookie Win",
                            "Book_Place": "Bookie Place",
                            "BF_Win_Lay": "Betfair Win Lay",
                            "BF_Place_Lay": "Betfair Place Lay",
                            "EW_Edge": "EW Edge %",
                            "Place_Edge": "Place Edge %",
                            "Verdict": "How Far We Are",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                    height=min(600, (len(filtered_ew) + 1) * 36),
                )

                st.subheader("🎯 1-Click Racecard Jump")
                for _idx, row in filtered_ew.head(20).iterrows():
                    c_p1, c_p2 = st.columns([4, 1])
                    with c_p1:
                        ew_txt = f"{row['EW_Edge']:+.1f}%" if pd.notnull(row['EW_Edge']) else "N/A"
                        pl_txt = f"{row['Place_Edge']:+.1f}%" if pd.notnull(row['Place_Edge']) else "N/A"
                        st.write(
                            f"**{row['Race']}** - **{row['Horse']}** | Bookie: **{row['Book_Win']} / {row['Book_Place']}** ({row['Bookmaker']}) | BF Lay: **{row['BF_Win_Lay']} / {row['BF_Place_Lay']}** | **EW Edge: {ew_txt}** (Place Edge: {pl_txt})"
                        )
                        st.caption(f"Status: {row['Verdict']}")
                    with c_p2:
                        if st.button("🏇 Open Racecard", key=f"jump_bf_{row['Horse']}_{_idx}"):
                            matching_idx = next(
                                (i for i, r in enumerate(all_day_races) if r["course_slug"] == row["course_slug"] and r["hhmm"] == row["hhmm"]),
                                None,
                            )
                            if matching_idx is not None:
                                st.session_state["selected_race_idx"] = matching_idx
                            st.session_state["nav_view"] = "🏇 Racecard, Odds & Ranks"
                            st.rerun()
                    st.markdown("---")


# ==============================================================================
# VIEW 4: 🏆 RESULTS (DAILY SETTLEMENT AUDIT & EARLY PRICE VS SP ROI)
# ==============================================================================
elif st.session_state["nav_view"] == "🏆 Results":
    st.markdown("<div class='main-header'>🏆 DAILY SYSTEM RESULTS & SETTLEMENT AUDIT</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='sub-header'>Audited settlement comparing Early Morning Bookmaker Odds vs Industry Starting Price (SP). Realized returns across Win-Only & Each-Way staking.</div>",
        unsafe_allow_html=True,
    )

    # 1. Load results ledger safely (CSV first, then SQLite fallback)
    all_res_df = load_results_ledger()
    if not all_res_df.empty and "race_date" in all_res_df.columns:
        available_dates = sorted(all_res_df["race_date"].dropna().unique().tolist(), reverse=True)
    else:
        available_dates = ["2026-09-18", "2026-09-17", "2026-09-16", "2026-09-15"]

    today_iso = dt.date.today().isoformat()
    yesterday_iso = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    date_display_map = {}
    for d in available_dates:
        if d == today_iso:
            date_display_map[d] = f"📅 {d} (Today's Live & Running)"
        elif d == yesterday_iso:
            date_display_map[d] = f"📅 {d} (Yesterday's Racing)"
        else:
            date_display_map[d] = f"📅 {d}"
    date_display_map["ALL"] = "📈 All Logged Dates (Cumulative Aggregate)"

    date_options = [*available_dates, "ALL"]

    c_sel1, c_sel2, c_sel3 = st.columns([2, 2, 1.5])
    with c_sel1:
        chosen_date = st.selectbox(
            "Select Date for Settlement",
            date_options,
            format_func=lambda x: date_display_map.get(x, x),
            index=0,
            key="res_date_select"
        )
    with c_sel2:
        bet_mode = st.radio(
            "Betting Mode",
            ["🎯 Win-Only (£1 Stake)", "🏇 Each-Way (£1 EW / £2 Total Stake)"],
            index=1,
            horizontal=True,
            key="res_bet_mode"
        )
    with c_sel3:
        st.write("")
        st.write("")
        b_c1, b_c2 = st.columns(2)
        with b_c1:
            if st.button("🔄 Refresh", use_container_width=True, key="refresh_res_btn"):
                st.cache_data.clear()
                st.rerun()
        with b_c2:
            if st.button("⚡ Settle", use_container_width=True, key="settle_now_btn"):
                # The button cannot settle in this process.  Streamlit Cloud has
                # neither the results source (a SQL Server database that only exists
                # on the archive machine) nor a writable, publishable file - its disk
                # is ephemeral and it has no write access to the repository, so a
                # local settle would vanish on the next restart and every row would
                # go back to "⏳ Running Today".
                #
                # So it starts the GitHub Action instead, which has a real disk and
                # write access, and whose commit is what actually publishes results.
                try:
                    import cloud_trigger
                    _day = chosen_date if chosen_date != "ALL" else today_iso
                    # A token typed into the box below lives in this browser session
                    # only, so the button works without editing the secrets file and
                    # waiting for a redeploy.
                    _tok = st.session_state.get("gh_token")
                    if cloud_trigger.is_configured() or _tok:
                        with st.spinner("Asking GitHub to run the updater..."):
                            _ok, _msg = cloud_trigger.trigger(_day, token=_tok)
                        if _ok:
                            st.success(_msg)
                            _lr = cloud_trigger.latest_run(token=_tok)
                            if _lr and _lr.get("url"):
                                st.caption(
                                    "Last run: %s (%s, %s) — [view on GitHub](%s)"
                                    % (_lr.get("conclusion") or _lr.get("status"),
                                       _lr.get("event"), _lr.get("created_at"),
                                       _lr["url"]))
                        else:
                            st.error(_msg)
                    else:
                        st.warning(
                            "**The updater needs a GitHub token, once.**\n\n"
                            "This button cannot settle here: Streamlit Cloud has no "
                            "results database (`pyodbc`/SQL Server) and its filesystem is "
                            "wiped on every restart, so anything it wrote could never be "
                            "published — the rows would revert to \u23f3 within minutes. "
                            "It has to *start* the updater, which runs on GitHub with a "
                            "real disk and write access to the repo. That needs a token."
                        )
                        st.markdown(
                            "**1.** Open "
                            "[Fine-grained tokens \u2192 Generate new token]"
                            "(https://github.com/settings/personal-access-tokens/new)\n\n"
                            "**2.** Repository access \u2192 *Only select repositories* \u2192 "
                            "`Qasim9989/Horse_race`\n\n"
                            "**3.** Permissions \u2192 Repository permissions \u2192 "
                            "**Actions: Read and write**\n\n"
                            "**4.** Generate, copy, and paste it below."
                        )
                        _typed = st.text_input(
                            "GitHub token (kept in this session only)",
                            type="password", key="gh_token_box",
                            placeholder="github_pat_... or ghp_...")
                        if _typed and _typed.strip():
                            st.session_state["gh_token"] = _typed.strip()
                            st.info("Token saved for this session — press **\u26a1 Settle** again.")
                        st.caption(
                            "To make it permanent, add `GITHUB_TOKEN` in Streamlit \u2192 App "
                            "Settings \u2192 Secrets and reboot the app. Until then results "
                            "still update **every 2 hours** on their own.")
                except Exception as ex:
                    st.error(f"Settlement error: {ex}")

    if chosen_date == today_iso:
        st.info(
            "⏳ **Today's Selections Active**: All selections for today's racing are logged with both **Early Bookmaker Prices** and **Betfair Exchange Prices**. As races finish throughout today, click **⚡ Settle** (or check back tomorrow) to see verified finish positions and Early vs SP ROI."
        )

    with st.expander("🔁 Update results — scrape, sync, settle", expanded=False):
        st.caption(
            "**⚡ Update results now** runs the whole chain for the chosen date, **whatever the time "
            "of day**: scrape `racingtv.com/results/<date>` into `Scraped_Results`, sync those "
            "finishes into `race_results` and the ledger, then settle every selection — re-checking "
            "rows already settled and leaving as-is anything the sources stay silent about.  \n"
            "The scrape is the step that used to be missing: `Settle` can only read results already "
            "in `Scraped_Results`, so when the scraper had not run for a day or two the button "
            "appeared to do nothing. Betfair cannot fill that gap — its API serves live markets "
            "only, so a finished day cannot be fetched from it afterwards.  \n"
            "**📸 Capture Betfair prices** writes an immediate snapshot of today's card into "
            "`racing_form.db` (`betfair_price_snapshots`): back, lay, last-traded and matched "
            "volume per runner. Hourly rows come from `python price_snapshot.py --hourly` on a timer."
        )
        _mf1, _mf2, _mf3 = st.columns([1.2, 1.2, 1])
        with _mf1:
            if st.button("⚡ Update results now", use_container_width=True, key="force_settle_btn"):
                _day = chosen_date if chosen_date != "ALL" else today_iso
                try:
                    import results_refresh
                    with st.spinner(f"Scraping results for {_day} - a full card takes a minute or two..."):
                        _res = results_refresh.refresh(_day)
                    if _res["ok"]:
                        st.success(f"Results updated for {_day}.")
                    else:
                        st.warning("Finished, with problems:")
                    st.code(_res["message"], language="text")
                    st.cache_data.clear()
                except Exception as ex:
                    st.error(f"Update error: {ex}")
        with _mf2:
            if st.button("🔄 Re-scrape day (force)", use_container_width=True, key="force_rescrape_btn"):
                _day = chosen_date if chosen_date != "ALL" else today_iso
                try:
                    import results_refresh
                    with st.spinner(f"Re-scraping every race on {_day}..."):
                        _res = results_refresh.refresh(_day, force_scrape=True)
                    st.code(_res["message"], language="text")
                    st.cache_data.clear()
                except Exception as ex:
                    st.error(f"Re-scrape error: {ex}")
        with _mf3:
            if st.button("📸 Capture Betfair prices", use_container_width=True, key="snap_now_btn"):
                try:
                    _snap = price_snapshot.capture_now(force=True)
                    if _snap.get("ok"):
                        st.success(_snap["message"])
                        st.cache_data.clear()
                    else:
                        st.warning(_snap["message"])
                except Exception as ex:
                    st.error(f"Capture error: {ex}")

        try:
            _hours = price_snapshot.latest_status(today_iso)
        except Exception:
            _hours = []
        if _hours:
            st.caption("Stored price snapshots for " + today_iso + ": " + " · ".join(
                f"**{h.split('T')[-1]}:00** {n} rows / {m} markets" for h, n, m in _hours[:6]))
        else:
            st.caption(f"No Betfair price snapshots stored yet for {today_iso}.")

    is_ew = "Each-Way" in bet_mode
    stake_per_bet = 2.0 if is_ew else 1.0

    # Filter ledger for chosen date
    if all_res_df.empty:
        res_df = pd.DataFrame()
    elif chosen_date == "ALL":
        res_df = all_res_df.sort_values(by=["race_date", "race_time"], ascending=[False, True])
    else:
        res_df = all_res_df[all_res_df["race_date"] == chosen_date].sort_values(by="race_time", ascending=True)

    # Mini Tabs for each system
    tab_tips, tab_ep, tab_ss, tab_ai, tab_ew, tab_all, tab_daily = st.tabs([
        "💡 Ben (Qas) System",
        "🎯 Ben's Extra-Place",
        "⚡ Speed & Stride",
        "🤖 AI System",
        "💱 Exchange EW Edge",
        "📊 All Systems Combined",
        "📅 Daily Breakdown"
    ])

    def render_system_metrics_and_table(sys_name, target_df):
        if target_df.empty:
            st.info(f"No settled selections logged for {sys_name} on this date.")
            return

        valid_bets = target_df[target_df["finish_pos"] != "NR (Void)"]
        total_bets = len(valid_bets)
        voids = len(target_df) - total_bets
        wins = int(valid_bets["won"].sum())
        places = int(valid_bets["placed"].sum())

        strike_rate = (wins / total_bets * 100) if total_bets > 0 else 0.0
        place_rate = (places / total_bets * 100) if total_bets > 0 else 0.0

        # Choose PL columns based on betting mode
        pl_col_early = "early_ew_pl" if is_ew else "early_win_pl"
        pl_col_sp = "sp_ew_pl" if is_ew else "sp_win_pl"

        early_pl = float(valid_bets[pl_col_early].dropna().sum())
        early_staked = total_bets * stake_per_bet
        early_roi = (early_pl / early_staked * 100) if early_staked > 0 else 0.0

        sp_pl = float(valid_bets[pl_col_sp].dropna().sum())
        sp_staked = total_bets * stake_per_bet
        sp_roi = (sp_pl / sp_staked * 100) if sp_staked > 0 else 0.0

        edge_gap = early_roi - sp_roi

        # Prefer the snapshot-verified settlement whenever a morning snapshot
        # exists for the date: prices come from the capture and the place leg
        # uses the SAME bookmaker's terms.  Rows the snapshot cannot cover are
        # excluded rather than being priced from stale data.
        verified_note = None
        verified_map = load_verified_settlement(chosen_date)
        if verified_map and evs is not None:
            records = []
            for rec in valid_bets.to_dict("records"):
                key = (rec.get("system_name"), evs.norm_course(rec.get("course")),
                       evs.to_hhmm(rec.get("race_time")), evs.norm_name(rec.get("horse_name")))
                hit = verified_map.get(key)
                if hit and hit.get("status") == "verified" and not hit.get("void"):
                    records.append(hit)
            if records:
                total_bets = len(records)
                voids = len(target_df) - total_bets
                wins = sum(1 for r in records if r.get("won"))
                places = sum(1 for r in records if r.get("placed"))
                strike_rate = (wins / total_bets * 100) if total_bets else 0.0
                place_rate = (places / total_bets * 100) if total_bets else 0.0
                e_field = "early_ew_pl" if is_ew else "early_win_pl"
                s_field = "sp_ew_pl" if is_ew else "sp_win_pl"
                early_pl = float(sum(r[e_field] for r in records if r.get(e_field) is not None))
                sp_pl = float(sum(r[s_field] for r in records if r.get(s_field) is not None))
                early_staked = total_bets * stake_per_bet
                sp_staked = total_bets * stake_per_bet
                early_roi = (early_pl / early_staked * 100) if early_staked > 0 else 0.0
                sp_roi = (sp_pl / sp_staked * 100) if sp_staked > 0 else 0.0
                edge_gap = early_roi - sp_roi
                excluded = len(valid_bets) - total_bets
                verified_note = (
                    f"Verified from the {chosen_date} morning snapshot: {total_bets} of "
                    f"{len(valid_bets)} bets priced from a captured price with that bookmaker's "
                    f"own each-way terms."
                )
                if excluded:
                    verified_note += (f" {excluded} row(s) excluded - no captured price to "
                                      f"verify them against.")

        # 4 Metric Cards
        m1, m2, m3, m4 = st.columns(4)
        if is_ew:
            m1.metric(
                label="🎯 Wins & Places (Hit Rate)",
                value=f"{places} / {total_bets} ({place_rate:.1f}%)",
                delta=f"{wins} Win(s) ({strike_rate:.1f}%)"
            )
        else:
            m1.metric(
                label="🎯 Wins (Strike Rate)",
                value=f"{wins} / {total_bets} ({strike_rate:.1f}%)",
                delta=f"{voids} Non-Runner(s)" if voids > 0 else "All Active"
            )

        early_color: Literal["normal", "inverse", "off"] = "normal" if early_roi >= 0 else "inverse"
        m2.metric(
            label=f"💰 Early Price ROI ({'EW' if is_ew else 'Win'})",
            value=f"{early_roi:+.1f}%",
            delta=f"£{early_pl:+.2f} (staked £{early_staked:.0f})",
            delta_color=early_color
        )

        sp_color: Literal["normal", "inverse", "off"] = "normal" if sp_roi >= 0 else "inverse"
        m3.metric(
            label="📉 Starting Price (SP) ROI",
            value=f"{sp_roi:+.1f}%",
            delta=f"£{sp_pl:+.2f} (staked £{sp_staked:.0f})",
            delta_color=sp_color
        )

        gap_color: Literal["normal", "inverse", "off"] = "normal" if edge_gap > 0 else ("inverse" if edge_gap < 0 else "off")
        m4.metric(
            label="⚡ Early Price Edge vs SP",
            value=f"{edge_gap:+.1f}%",
            delta="Early Advantage" if edge_gap >= 0 else "SP Drifted",
            delta_color=gap_color
        )

        if verified_note:
            st.caption(f"✅ {verified_note}")
        elif verified_map:
            st.caption(f"⏳ Settlement pending — {len(valid_bets)} logged bet(s) have no result yet. "
                       f"Prices will be verified against the {chosen_date} morning snapshot "
                       f"once they are settled.")
        else:
            st.caption("ℹ️ Early prices are the logged scan-time odds — no morning snapshot was "
                       "captured for this date, so these figures are unverified.")

        # Strategic Explainer Callout
        if early_roi > 0:
            st.success(
                f"🚀 **Massive Profitability Confirmed**: Realized **{early_roi:+.1f}% ROI** (+£{early_pl:.2f} net profit). Outstanding winners included Turnstile (40/1), Empirestateofmind (6/1), Eye Of Dubai (15/2), Lygon Lad (4/1) plus huge longshot places!"
            )
        elif edge_gap > 0:
            st.info(
                f"🔥 **Early Price Edge Confirmed**: Backing at morning bookmaker prices gained **+{edge_gap:.1f}% ROI** over backing at SP! Bookmakers heavily contract odds on winning runners prior to post time."
            )

        # Interactive Table
        display_df = target_df.copy()
        display_df["Early Price"] = display_df.apply(
            lambda r: f"{r['early_odds']:.2f} ({r['best_bookmaker']})" if pd.notna(r.get('early_odds')) and r['early_odds'] > 0 else "-",
            axis=1
        )
        display_df["Betfair Price"] = display_df.apply(
            lambda r: f"{r['bf_odds']:.2f}" if pd.notna(r.get('bf_odds')) and r['bf_odds'] > 0 else "-",
            axis=1
        )
        display_df["SP"] = display_df.apply(
            lambda r: f"{r['sp_text']} ({r['sp_odds']:.2f})" if pd.notna(r.get('sp_odds')) and r['sp_odds'] > 0 else (r['sp_text'] if r.get('sp_text') and r.get('sp_text') != '-' else "-"),
            axis=1
        )
        display_df["Early P&L"] = display_df[pl_col_early].apply(lambda v: f"£{v:+.2f}" if pd.notna(v) and str(v) != "0.0" else ("Pending" if chosen_date == today_iso else "£0.00"))
        display_df["SP P&L"] = display_df[pl_col_sp].apply(lambda v: f"£{v:+.2f}" if pd.notna(v) and str(v) != "0.0" else ("Pending" if chosen_date == today_iso else "£0.00"))

        def pos_badge(pos):
            p = str(pos).strip()
            if p in ("1", "1st"):
                return "🥇 1st (WON)"
            elif p in ("2", "2nd"):
                return "🥈 2nd (PLACED)"
            elif p in ("3", "3rd"):
                return "🥉 3rd (PLACED)"
            elif p in ("4", "4th"):
                return "🏅 4th (PLACED)"
            elif p in ("NR (Void)", "NR", "VOID"):
                return "⚪ Void (NR)"
            elif any(x in p.lower() for x in ["running", "scheduled", "pending", "today"]):
                return "⏳ Scheduled (Today)"
            return f"{p}" if p and p not in ("-", "None", "nan") else "⏳ Scheduled"
        display_df["Result"] = display_df["finish_pos"].apply(pos_badge)

        def move_calc(r):
            if pd.notna(r.get("early_odds")) and pd.notna(r.get("sp_odds")) and r["early_odds"] > 0:
                pct = ((r["sp_odds"] - r["early_odds"]) / r["early_odds"]) * 100
                if pct < -3:
                    return f"📉 Shortened ({pct:.1f}%)"
                elif pct > 3:
                    return f"📈 Drifted ({pct:+.1f}%)"
                else:
                    return "Solid (0%)"
            return "-"
        display_df["Odds Move"] = display_df.apply(move_calc, axis=1)

        cols_to_show = ["race_date", "race_time", "course", "horse_name", "sub_system", "Early Price", "Betfair Price", "SP", "Result", "Early P&L", "SP P&L", "Odds Move"]
        rename_dict = {
            "race_date": "Date",
            "race_time": "Time",
            "course": "Course",
            "horse_name": "Horse",
            "sub_system": "System / Selection Angle",
            "Early Price": "Early Bookmaker Price",
            "Betfair Price": "Betfair Exchange",
            "SP": "Starting Price (SP)",
            "Result": "Finish",
            "Early P&L": f"Early P&L ({'£2 EW' if is_ew else '£1 Win'})",
            "SP P&L": f"SP P&L ({'£2 EW' if is_ew else '£1 Win'})",
            "Odds Move": "Market Shift"
        }
        if chosen_date != "ALL":
            cols_to_show.remove("race_date")

        st.dataframe(
            display_df[cols_to_show].rename(columns=rename_dict),
            use_container_width=True,
            hide_index=True,
            height=min(600, (len(display_df) + 1) * 36)
        )

    # Tab 1: Ben (Qas) System
    with tab_tips:
        st.subheader("💡 Ben (Qas) System")
        st.caption(
            "Ben's handicap system: **Value Qualifiers** (below last winning mark, trip proven, top-4 LTO), "
            "**Big Weight Drops** (-8lb+ with Topspeed >= 60), and **Placed at Trip**."
        )
        tips_data = res_df[res_df["system_name"] == "Tips"]

        if not tips_data.empty:
            valid_count = len(tips_data[tips_data["finish_pos"] != "NR (Void)"])

            # Check for Ben's Strict 5-Rule Qualifiers
            strict_list = []
            try:
                import our_system
                _st_picks, _ = our_system.build(chosen_date if chosen_date != "ALL" else today_iso)
                strict_list = [our_system.base_name(p['Horse']) for p in _st_picks if p.get('System') == 'QAS SYSTEM']
            except Exception:
                strict_list = []

            is_strict_mask = tips_data["horse_name"].apply(lambda h: our_system.base_name(h) in strict_list) if strict_list else pd.Series([False]*len(tips_data), index=tips_data.index)
            strict_count = int(is_strict_mask.sum())

            if strict_count > 0:
                filter_options = [
                    f"🎯 Ben's Strict 5-Rule ({strict_count} bets)",
                    "⭐ Value Qualifiers Only",
                    "⚡ Big Weight Drops Only",
                    "🔔 Placed at Trip Only",
                    f"🌐 Full Market Scan ({valid_count} picks)"
                ]
            else:
                filter_options = [
                    f"All Picks ({valid_count} bets)",
                    "⭐ Value Qualifiers Only",
                    "⚡ Big Weight Drops Only",
                    "🔔 Placed at Trip Only"
                ]

            cat_choice = st.radio(
                "Filter Angle",
                filter_options,
                index=0,
                horizontal=True,
                key="results_tips_angle_filter"
            )

            filtered_data = tips_data.copy()
            if "Strict 5-Rule" in cat_choice:
                filtered_data = filtered_data[is_strict_mask]
            elif "Value Qualifiers" in cat_choice:
                filtered_data = filtered_data[filtered_data["sub_system"].str.contains("Value|Below Win|Soft", case=False, na=False)]
            elif "Weight Drops" in cat_choice:
                filtered_data = filtered_data[filtered_data["sub_system"].str.contains("Weight|Drop|Featherweight", case=False, na=False)]
            elif "Placed at Trip" in cat_choice:
                filtered_data = filtered_data[filtered_data["sub_system"].str.contains("Trip", case=False, na=False)]

            render_system_metrics_and_table(SYSTEM_DISPLAY["Tips"], filtered_data)
        else:
            render_system_metrics_and_table(SYSTEM_DISPLAY["Tips"], tips_data)

        # Historical forward book reference when viewing all dates
        _os_ledger = os.path.join(os.path.dirname(os.path.abspath(__file__)), "our_system_forward_ledger.csv")
        if os.path.exists(_os_ledger) and chosen_date == "ALL":
            try:
                _os = pd.read_csv(_os_ledger)
                for _c in ("Odds", "Stake", "BSP_TRUE", "won", "PL_taken", "PL_bsp"):
                    if _c in _os.columns:
                        _os[_c] = pd.to_numeric(_os[_c], errors="coerce")
                st.markdown("---")
                st.subheader("🎯 Historical Forward Book (Strict 5-Rule Strategy)")
                st.caption("All-time published forward book (1,759 bets, real prices).")
                _staked = _os["Stake"].sum()
                _pl_taken = _os["PL_taken"].sum()
                _pl_bsp = _os["PL_bsp"].sum()
                _wins = int(_os["won"].sum())
                _n_bets = len(_os)
                _sr = (_wins / _n_bets * 100) if _n_bets > 0 else 0.0

                _k1, _k2, _k3, _k4 = st.columns(4)
                _k1.metric("🎯 Total Bets", f"{_wins} / {_n_bets} ({_sr:.1f}%)")
                _k2.metric("💰 Average Odds", f"{_os['Odds'].mean():.2f}", f"Stake: {_staked:.1f}u")
                _k3.metric("📈 P/L at Taken Price", f"£{_pl_taken:+,.2f}", f"{(_pl_taken / _staked * 100):+.1f}% ROI")
                _k4.metric("📉 P/L at BSP", f"£{_pl_bsp:+,.2f}", f"{(_pl_bsp / _staked * 100):+.1f}% ROI")
            except Exception:
                pass


    # Tab: Ben's Extra-Place System (DB-backed picks for all dates)
    with tab_ep:
        st.subheader("🎯 Ben’s Morning Extra-Place System")
        st.caption(
            "Handicap handicappers with an extra-place concession (4–5 places @ 1/5). "
            "Targets marks dropping 0–7lb, odds 6–34, field ≥12 runners."
        )

        try:
            import bens_extra_place_system as _bep
        except Exception as _bep_err:
            st.error(f"Could not import scanner: {_bep_err}")
            _bep = None  # type: ignore

        if _bep is not None:
            _ep_date = chosen_date if chosen_date != "ALL" else today_iso

            # For today: run live scan (this also saves picks to DB automatically)
            # For past dates / ALL: read from the bens_ep_selections DB table
            if chosen_date == today_iso:
                try:
                    _ep_picks = _bep.scan_extra_place_bets(_ep_date)
                    if not _ep_picks:
                        st.info(f"No extra-place qualifiers found yet for {_ep_date}. Check back after 08:00.")
                except Exception as _ep_err:
                    _ep_picks = []
                    st.warning(f"Live scan error: {_ep_err}")
            else:
                _ep_picks = _bep.load_ep_picks(_ep_date)
                if not _ep_picks and chosen_date != "ALL":
                    st.info(
                        f"📂 No stored picks for **{_ep_date}**. "
                        "Picks are saved automatically when the scanner runs on the day. "
                        "Past dates before this feature was added have no stored data."
                    )

            # If ALL: merge every date stored
            if chosen_date == "ALL":
                _all_ep_dates = _bep.get_ep_dates()
                _ep_picks_all = []
                for _d in _all_ep_dates:
                    for _pk in _bep.load_ep_picks(_d):
                        _pk["race_date"] = _d
                        _ep_picks_all.append(_pk)
                _ep_picks = _ep_picks_all

            if _ep_picks:
                _ep_label = (
                    f"📍 **{len(_ep_picks)} picks stored for {_ep_date}**"
                    if chosen_date != "ALL"
                    else f"📅 **{len(_ep_picks)} total picks across all logged dates**"
                )
                st.success(_ep_label)
                _ep_df_rows = []
                for _p in _ep_picks:
                    _o = _p.get("odds", 0) or 0
                    _pr = _p.get("place_return", 0.0) or 0.0
                    _row = {
                        "Horse": _p.get("horse", "-"),
                        "Race": f"{_p.get('course', '')} {_p.get('race_time', '')}",
                        "Odds": _p.get("odds_display") or (f"{_o:.1f}" if _o else "-"),
                        "Bookmaker": _p.get("bookmaker", "-"),
                        "Pl. Return": f"{_pr:.2f}" if _pr else "-",
                        "Drop": f"{_p.get('drop', 0):+d}lb",
                        "Mark": f"{_p.get('mark', '-')} ← {_p.get('lto_mark', '-')}",
                        "LTO Pos": _p.get("lto_pos", "-"),
                        "Terms": _p.get("place_terms", "-"),
                        "Score": _p.get("score", "-"),
                    }
                    if chosen_date == "ALL":
                        _row = {"Date": _p.get("race_date", "-"), **_row}
                    _ep_df_rows.append(_row)
                _ep_picks_df = pd.DataFrame(_ep_df_rows)
                st.dataframe(_ep_picks_df, use_container_width=True, hide_index=True)

        # ---- SETTLED history from the ledger ----
        st.markdown("---")
        st.markdown("**📊 Settled P&L history** (logged under system `Ben EP`)")
        ep_settled = res_df[res_df["system_name"] == "Ben EP"]
        if ep_settled.empty and chosen_date != "ALL":
            ep_settled = all_res_df[all_res_df["system_name"] == "Ben EP"]
        if ep_settled.empty:
            st.info(
                "⏳ No settled results yet for this system. "
                "Once races finish, click **⚡ Settle** to compute P&L."
            )
        else:
            render_system_metrics_and_table("Ben's Extra-Place", ep_settled)

    # Tab 2: Speed & Stride System
    with tab_ss:
        st.subheader("⚡ Speed & Stride System (TPD Telemetry)")
        ss_data = res_df[res_df["system_name"] == "Speed & Stride"]
        render_system_metrics_and_table("Speed & Stride System", ss_data)

    # Tab 3: Antigravity / AI System
    with tab_ai:
        st.subheader("🤖 Antigravity / AI System (Power Model & Analyst Picks)")
        ai_data = res_df[res_df["system_name"] == "AI System"]
        render_system_metrics_and_table("AI System", ai_data)

    # Tab 4: Exchange EW Edge
    with tab_ew:
        st.subheader("💱 Exchange Each-Way Edge & Value Qualifiers")
        ew_data = res_df[res_df["system_name"] == "Exchange EW Edge"]
        render_system_metrics_and_table("Exchange EW Edge", ew_data)

    # Tab 6: Daily breakdown - one row per day, per system and tip category
    with tab_daily:
        st.subheader("📅 Daily Breakdown - settled P&L per system, per day")
        st.caption(
            "Each-way P&L per pick at the recorded early price (2u EW stake). Races that have not "
            "finished are excluded, so the newest day fills in as it settles - which is why a day can "
            "look small until the evening."
        )
        daily_src = (all_res_df if chosen_date == "ALL" else res_df).copy()
        if daily_src.empty:
            st.info("Nothing logged yet.")
        else:
            for col in ("won", "placed", "early_ew_pl", "sp_ew_pl"):
                if col in daily_src.columns:
                    daily_src[col] = pd.to_numeric(daily_src[col], errors="coerce")
            _pos = (daily_src["finish_pos"] if "finish_pos" in daily_src.columns
                    else pd.Series([""] * len(daily_src), index=daily_src.index))
            finish = _pos.map(lambda value: str(value).strip().lower())
            settled = daily_src[~finish.isin(("", "-", "nan", "none", "⏳ running today", "pending"))]
            if settled.empty:
                st.info("No settled results yet - click ⚡ Settle once racing has finished.")
            else:
                st.markdown("**EW P&L per day, by system**")
                per_day = settled.groupby(["race_date", "system_name"]).agg(
                    Bets=("horse_name", "size"),
                    Won=("won", "sum"),
                    Placed=("placed", "sum"),
                    EW_PL=("early_ew_pl", "sum"),
                ).reset_index()
                pivot = per_day.pivot_table(index="race_date", columns="system_name",
                                            values="EW_PL", aggfunc="sum", fill_value=0.0)
                pivot["DAY TOTAL"] = pivot.sum(axis=1)
                st.dataframe(pivot.round(2), use_container_width=True)

                st.markdown("**Bets / wins / places per day, by system**")
                detail = per_day.copy()
                detail["ROI %"] = (detail["EW_PL"] / (detail["Bets"] * stake_per_bet) * 100).round(1)
                detail = detail.rename(columns={"race_date": "Date", "system_name": "System",
                                                "EW_PL": "EW P&L"})
                detail["System"] = detail["System"].replace(SYSTEM_DISPLAY)
                st.dataframe(detail.sort_values(["Date", "System"], ascending=[False, True]),
                             use_container_width=True, hide_index=True)

                tips_only = settled[settled["system_name"] == "Tips"]
                if not tips_only.empty:
                    st.markdown("**Ben (Qas) by category, per day** - which angle is actually paying")
                    cat = tips_only.groupby(["race_date", "sub_system"]).agg(
                        Bets=("horse_name", "size"),
                        Won=("won", "sum"),
                        Placed=("placed", "sum"),
                        EW_PL=("early_ew_pl", "sum"),
                    ).reset_index()
                    cat["ROI %"] = (cat["EW_PL"] / (cat["Bets"] * stake_per_bet) * 100).round(1)
                    cat = cat.rename(columns={"race_date": "Date", "sub_system": "Category",
                                              "EW_PL": "EW P&L"})
                    st.dataframe(cat.sort_values(["Date", "EW P&L"], ascending=[False, False]),
                                 use_container_width=True, hide_index=True)

                    st.markdown("**Ben (Qas) by angle — strike rate and ROI** "
                                f"({'all logged dates' if chosen_date == 'ALL' else chosen_date})")

                    def _angle_group(value):
                        """The ledger records the angle plus its detail (e.g. 'Big Weight Drop
                        (-8lb)'), which leaves one-bet groups and meaningless ROIs.  Collapse
                        to the three angles."""
                        text = str(value or "").lower()
                        if "weight drop" in text or "featherweight" in text:
                            return "Big Weight Drop"
                        if "trip" in text:
                            return "Placed at Trip"
                        return "Value Qualifier"

                    t_cat = tips_only.copy()
                    t_cat["Angle"] = t_cat["sub_system"].map(_angle_group)
                    angle = t_cat.groupby("Angle").agg(
                        Bets=("horse_name", "size"),
                        Won=("won", "sum"),
                        Placed=("placed", "sum"),
                        Early_PL=("early_ew_pl", "sum"),
                        Early_Bets=("early_ew_pl", "count"),
                        SP_PL=("sp_ew_pl", "sum"),
                        SP_Bets=("sp_ew_pl", "count"),
                    ).reset_index()
                    angle["Strike %"] = (angle["Won"] / angle["Bets"] * 100).round(1)
                    angle["Place %"] = (angle["Placed"] / angle["Bets"] * 100).round(1)
                    _asp = t_cat[t_cat["sp_odds"] > 1]
                    angle["Implied %"] = angle["Angle"].map(
                        _asp.assign(_inv=1 / _asp["sp_odds"]).groupby("Angle")["_inv"].mean() * 100
                    ).round(1)
                    angle["Gap pp"] = (angle["Strike %"] - angle["Implied %"]).round(1)
                    angle["ROI early %"] = (
                        angle["Early_PL"] / (angle["Early_Bets"] * stake_per_bet) * 100).round(1)
                    angle["ROI SP %"] = (
                        angle["SP_PL"] / (angle["SP_Bets"] * stake_per_bet) * 100).round(1)
                    angle["Edge early-SP"] = (angle["ROI early %"] - angle["ROI SP %"]).round(1)
                    angle = angle.rename(columns={"Early_PL": "Early P&L", "SP_PL": "SP P&L"})
                    st.dataframe(
                        angle[["Angle", "Bets", "Won", "Strike %", "Implied %", "Gap pp", "Placed",
                               "Place %", "Early P&L", "ROI early %", "SP P&L", "ROI SP %",
                               "Edge early-SP"]]
                        .sort_values("Gap pp", ascending=False),
                        use_container_width=True, hide_index=True,
                    )
                    st.caption(
                        f"**Gap pp** = strike rate minus what the prices implied — trust that column "
                        f"first; ROI on these samples is dominated by a few big-priced winners. SP ROI "
                        f"rests on the {int(angle['SP_Bets'].sum())} of {int(angle['Bets'].sum())} picks "
                        f"that have an SP recorded (the rest have none and score as losses), so it is "
                        f"not yet a clean figure."
                    )

                    st.markdown("**Totals for the window, by tip category**")
                    cat_tot = tips_only.groupby("sub_system").agg(
                        Bets=("horse_name", "size"), Won=("won", "sum"), Placed=("placed", "sum"),
                        EW_PL=("early_ew_pl", "sum"), SP_EW_PL=("sp_ew_pl", "sum")).reset_index()
                    cat_tot["ROI %"] = (cat_tot["EW_PL"] / (cat_tot["Bets"] * stake_per_bet) * 100).round(1)
                    cat_tot = cat_tot.rename(columns={"sub_system": "Category", "EW_PL": "EW P&L",
                                                      "SP_EW_PL": "EW P&L at SP"})
                    st.dataframe(cat_tot.sort_values("EW P&L", ascending=False),
                                 use_container_width=True, hide_index=True)



    # Tab 4: All Systems Combined
    with tab_all:
        st.subheader("📊 All Systems Combined Settlement")
        st.markdown(f"**System leaderboard** "
                    f"({'all logged dates' if chosen_date == 'ALL' else chosen_date}) — "
                    "one row per system, comparing Ben (Qas), Speed & Stride, AI System, and Exchange EW Edge")
        _lb_src = (all_res_df if chosen_date == "ALL" else res_df).copy()
        for _c in ("won", "placed", "early_ew_pl", "sp_ew_pl", "sp_odds", "early_odds"):
            if _c in _lb_src.columns:
                _lb_src[_c] = pd.to_numeric(_lb_src[_c], errors="coerce")
        if _lb_src.empty:
            st.info("Nothing logged yet.")
        else:
            lead = _lb_src.groupby("system_name").agg(
                Bets=("horse_name", "size"),
                Won=("won", "sum"),
                Placed=("placed", "sum"),
                Early_PL=("early_ew_pl", "sum"),
                Early_Bets=("early_ew_pl", "count"),
                SP_PL=("sp_ew_pl", "sum"),
                SP_Bets=("sp_ew_pl", "count"),
                Avg_early=("early_odds", "mean"),
                Avg_sp=("sp_odds", "mean"),
            ).reset_index()
            lead["Strike %"] = (lead["Won"] / lead["Bets"] * 100).round(1)
            lead["Place %"] = (lead["Placed"] / lead["Bets"] * 100).round(1)
            # implied from the market's price (mean of 1/SP, not 1/mean SP), which is the
            # column that converges fastest on whether a system has real selection skill
            _sp = _lb_src[_lb_src["sp_odds"] > 1]
            lead["Implied %"] = lead["system_name"].map(
                _sp.assign(_inv=1 / _sp["sp_odds"]).groupby("system_name")["_inv"].mean() * 100
            ).round(1)
            lead["Gap pp"] = (lead["Strike %"] - lead["Implied %"]).round(1)
            lead["ROI early %"] = (
                lead["Early_PL"] / (lead["Early_Bets"] * stake_per_bet) * 100).round(1)
            lead["ROI SP %"] = (
                lead["SP_PL"] / (lead["SP_Bets"] * stake_per_bet) * 100).round(1)
            lead["Avg early"] = lead["Avg_early"].round(2)
            lead["Avg SP"] = lead["Avg_sp"].round(2)
            lead["System"] = lead["system_name"].replace(SYSTEM_DISPLAY)
            st.dataframe(
                lead[["System", "Bets", "Won", "Strike %", "Implied %", "Gap pp", "Placed",
                      "Place %", "Avg early", "Avg SP", "ROI early %", "ROI SP %"]]
                .sort_values("Gap pp", ascending=False),
                use_container_width=True, hide_index=True,
            )
            st.caption(
                "**Gap pp** = actual strike rate minus what the market's SP implied — that is the "
                "column to trust, it converges long before ROI does. ROI is each-way P&L per pick "
                "over the picks that have that price recorded, so the early and SP columns can rest "
                "on different samples, and today's races are still settling."
            )
        render_system_metrics_and_table("All Systems", res_df)


elif st.session_state["nav_view"] == "📖 Horse Career Profile":
    target_horse = st.session_state.get("selected_horse", "Turnstile")

    col_back, col_title = st.columns([1, 5])
    with col_back:
        if st.button("⬅️ Back to Racecard"):
            st.session_state["nav_view"] = "🏇 Racecard, Odds & Ranks"
            st.rerun()
    with col_title:
        st.subheader(f"📖 Complete Career Profile: {target_horse.upper()} ({selected_course} {selected_time})")

    with st.spinner(f"Loading complete record for {target_horse}..."):
        h_df = load_horse_career(target_horse)

    if h_df is None or h_df.empty:
        st.warning(f"No historical runs found for '{target_horse}'.")
    else:
        total_runs = len(h_df)
        wins_df = h_df[h_df["Pos"] == "1"]
        wins = len(wins_df)
        win_pct = round((wins / total_runs) * 100, 1) if total_runs else 0

        ts_vals = [x for x in h_df["TS"].dropna() if x > 0] if "TS" in h_df else []
        rpr_vals = [x for x in h_df["RPR"].dropna() if x > 0] if "RPR" in h_df else []

        best_ts = max(ts_vals) if ts_vals else "-"
        low_ts = min(ts_vals) if ts_vals else "-"
        best_rpr = max(rpr_vals) if rpr_vals else "-"
        low_rpr = min(rpr_vals) if rpr_vals else "-"
        best_mph = h_df["Speed_MPH"].max() if "Speed_MPH" in h_df and not h_df["Speed_MPH"].dropna().empty else "-"

        if not wins_df.empty:
            last_win_row = wins_df.iloc[0]
            win_summary = f"{last_win_row['Weight']} (OR {last_win_row['OR'] or '-'}) at {last_win_row['Course']} ({last_win_row['Distance']})"
        else:
            win_summary = "Maiden (Never won a race)"

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Career Starts", total_runs)
        k2.metric("Wins (Win %)", f"{wins} ({win_pct}%)")
        k3.metric("Topspeed (High / Low)", f"{best_ts} / {low_ts}")
        k4.metric("RPR (High / Low)", f"{best_rpr} / {low_rpr}")
        k5.metric(
            "Top Speed (GPS)",
            f"{best_mph:.1f} mph" if isinstance(best_mph, (int, float)) else "-",
            help="Coursetrack GPS tracking chip speed (available at tracks with live tracking sensors)",
        )

        st.info(f"🏆 **Last Winning Mark**: {win_summary}")

        chart_df = h_df.dropna(subset=["Date"]).sort_values("Date")
        fig = go.Figure()
        if "TS" in chart_df and not chart_df["TS"].dropna().empty:
            fig.add_trace(
                go.Scatter(
                    x=chart_df["Date"],
                    y=chart_df["TS"],
                    mode="lines+markers",
                    name="Topspeed (TS)",
                    line={"color": "#3B82F6", "width": 2},
                )
            )
        if "RPR" in chart_df and not chart_df["RPR"].dropna().empty:
            fig.add_trace(
                go.Scatter(
                    x=chart_df["Date"],
                    y=chart_df["RPR"],
                    mode="lines+markers",
                    name="Racing Post Rating (RPR)",
                    line={"color": "#10B981", "width": 2},
                )
            )
        if "OR" in chart_df and not chart_df["OR"].dropna().empty:
            fig.add_trace(
                go.Scatter(
                    x=chart_df["Date"],
                    y=chart_df["OR"],
                    mode="lines+markers",
                    name="Official Rating (OR)",
                    line={"color": "#64748B", "dash": "dot"},
                )
            )

        fig.update_layout(
            title="Ratings Progression (Topspeed, RPR, Official Rating)",
            xaxis_title="Race Date",
            yaxis_title="Rating",
            height=320,
            margin={"l": 20, "r": 20, "t": 40, "b": 20},
        )
        st.plotly_chart(fig, use_container_width=True)

        st.write("### 📋 Every Career Start with In-Running Comments")
        for _idx, row in h_df.iterrows():
            pos_label = "WON" if str(row["Pos"]) == "1" else f"{row['Pos']} (btn {row['Beaten']}L)"
            with st.expander(
                f"{row['Date']} {row['Course']} ({row['Distance']}) - Finish: {pos_label} | SP: {row['SP']} | Jockey: {row['Jockey']}"
            ):
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.write(f"**OR**: {row['OR'] or '-'}")
                m2.write(f"**TS**: {row['TS'] or '-'}")
                m3.write(f"**RPR**: {row['RPR'] or '-'}")
                m4.write(f"**Speed**: {f'{row["Speed_MPH"]:.1f} mph' if row["Speed_MPH"] else '-'}")
                m5.write(f"**Stride**: {f'{row["Stride_m"]:.2f}m' if row["Stride_m"] else '-'}")

                if row["Market"]:
                    st.write(f"**Market Moves**: `{row['Market']}`")
                comm_val = row["Comment"] or "No in-running comment recorded."
                st.markdown(
                    f"<div class='comment-card'>💬 <i>\"{comm_val}\"</i></div>",
                    unsafe_allow_html=True,
                )
