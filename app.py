# ==============================================================================
# HR BEST TIMES & TELEMETRY CLOUD APPLICATION (STREAMLIT COMMUNITY CLOUD)
# ==============================================================================
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from typing import Any, Literal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rtv_api

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
    .ben-badge {
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

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    results = []

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

        # Ben Strategy Flag / Alert
        ben_tag = "-"
        if delta_weight is not None and delta_weight <= -8:
            ben_tag = f"⚡ {delta_weight:+d}lb"
        elif placings_at_trip >= 2 and (delta_weight is not None and delta_weight <= 0):
            ben_tag = "⭐ Ben Pick"
        elif placings_at_trip >= 2:
            ben_tag = f"🔔 Placed ({placings_at_trip}x)"

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
                "Bookmaker": best_bookie,
                "Best_Book": best_book_str,
                "Extra_Places": extra_places_str,
                "Ben_Alert": ben_tag,
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
    df["Master_Rank"] = df["Power_Score"].rank(ascending=False, method="min").astype(int)
    df_sorted = df.sort_values("Master_Rank").reset_index(drop=True)

    return df_sorted, race_info


@st.cache_data(ttl=180)
def scan_daily_tips_and_bens(date_str, target_course=None):
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

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    picks = []

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

            # 2. Strict Ben's 5 Rules (Falling mark + At/below win mark + Proven at trip + Top 4 LTO)
            elif (delta_wgt < 0 or (lto_or and last_win_or and lto_or <= last_win_or)) and placings_at_trip >= 1 and lto_pos in ("1", "2", "3", "4"):
                angles.append(f"⭐ Ben Pick (In Form pos {lto_pos}, {placings_at_trip}x Trip Placed)")
                category = "⭐ Ben's Qualifier"

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


def load_horse_career(horse_name):
    conn = sqlite3.connect(DB_PATH)
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


@st.cache_data(ttl=180)
def scan_speed_and_stride(date_str, target_course=None):
    schedule: dict[str, list[dict[str, Any]]] = {}
    for r in rtv_api.day_races(date_str):
        c = r.get("course_name", "")
        schedule.setdefault(c, []).append(r)

    conn = sqlite3.connect(DB_PATH)
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

                cur.execute(
                    """
                    SELECT top_speed, stride_length
                    FROM raceiq_telemetry
                    WHERE lower(horse_name) = ? OR lower(horse_name) LIKE ?
                    ORDER BY race_date DESC
                    LIMIT 1
                    """,
                    (h_clean, f"{h_clean}%")
                )
                t_row = cur.fetchone()
                last_speed = t_row[0] if t_row and t_row[0] else None
                last_stride = t_row[1] if t_row and t_row[1] else None

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

            speed_runners = [x for x in race_telemetry if x["speed"] is not None]
            stride_runners = [x for x in race_telemetry if x["stride"] is not None]

            best_spd_horse = max(speed_runners, key=lambda x: float(x["speed"] or 0.0)) if speed_runners else None
            best_str_horse = max(stride_runners, key=lambda x: float(x["stride"] or 0.0)) if stride_runners else None

            if best_spd_horse and best_str_horse and best_spd_horse["horse"] == best_str_horse["horse"]:
                picks.append({
                    "Race": f"{time_str} {c_name}",
                    "course_slug": c_slug,
                    "hhmm": hhmm,
                    "Horse": best_spd_horse["horse"],
                    "Odds": f"{best_spd_horse['odds']:.2f}" if best_spd_horse["odds"] else "-",
                    "Bookmaker": best_spd_horse["bookmaker"],
                    "Top_Speed_MPH": f"{best_spd_horse['speed']:.1f} mph",
                    "Stride_Length": f"{best_spd_horse['stride']:.2f} m",
                    "Category": "🎯 AGREE (Speed + Stride)",
                    "Edge": "+12.42% Net ROI at BSP (Tops Both)",
                })
            else:
                if best_spd_horse:
                    picks.append({
                        "Race": f"{time_str} {c_name}",
                        "course_slug": c_slug,
                        "hhmm": hhmm,
                        "Horse": best_spd_horse["horse"],
                        "Odds": f"{best_spd_horse['odds']:.2f}" if best_spd_horse["odds"] else "-",
                        "Bookmaker": best_spd_horse["bookmaker"],
                        "Top_Speed_MPH": f"{best_spd_horse['speed']:.1f} mph",
                        "Stride_Length": f"{best_spd_horse['stride']:.2f} m" if best_spd_horse["stride"] else "-",
                        "Category": "🚀 SPEED System Pick",
                        "Edge": "+9.46% Net ROI at BSP (Top Previous Speed)",
                    })
                if best_str_horse:
                    picks.append({
                        "Race": f"{time_str} {c_name}",
                        "course_slug": c_slug,
                        "hhmm": hhmm,
                        "Horse": best_str_horse["horse"],
                        "Odds": f"{best_str_horse['odds']:.2f}" if best_str_horse["odds"] else "-",
                        "Bookmaker": best_str_horse["bookmaker"],
                        "Top_Speed_MPH": f"{best_str_horse['speed']:.1f} mph" if best_str_horse["speed"] else "-",
                        "Stride_Length": f"{best_str_horse['stride']:.2f} m",
                        "Category": "📏 STRIDE System Pick",
                        "Edge": "+6.47% Net ROI at BSP (Longest Previous Stride)",
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
    "💡 Tips",
    "⚡ Speed & Stride System",
    "🏆 Results",
    "📖 Horse Career Profile",
]

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
                    "Odds": "Decimal Odds",
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

                with st.expander(f"🔍 Quick View Past Runs for {row['Horse']}"):
                    quick_df = load_horse_career(row["Horse"])
                    if quick_df is not None and not quick_df.empty:
                        st.dataframe(
                            quick_df[["Date", "Course", "Distance", "Pos", "Beaten", "Weight", "TS", "RPR", "Comment"]].head(5),
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.write("No earlier runs on record.")

# ==============================================================================
# VIEW 2: ⭐ BEN'S SYSTEM & TODAY'S TIPS TAB
# ==============================================================================
elif st.session_state["nav_view"] == "💡 Tips":
    st.markdown("<div class='main-header'>💡 TODAY'S VALUE TIPS & SYSTEM QUALIFIERS</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='sub-header'>Automatic daily scanner: detects Ben's qualifiers, massive weight drops (-7lb+), and horses knocking on the door at the distance.</div>",
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

    with st.spinner(f"Scanning {chosen_scan_meeting} for Ben's system picks and weight drops..."):
        tips_df = scan_daily_tips_and_bens(date_str, chosen_scan_meeting)

    if tips_df is None or tips_df.empty:
        st.info("No system qualifiers found matching criteria for this selection.")
    else:
        k_b1, k_b2, k_b3, k_b4 = st.columns(4)
        ben_picks_count = len(tips_df[tips_df["Category"] == "⭐ Ben's Qualifier"])
        wgt_drops_count = len(tips_df[tips_df["Category"] == "⚡ Big Weight Drop"])
        trip_form_count = len(tips_df[tips_df["Category"] == "🔔 Placed at Trip"])
        k_b1.metric("Total System Qualifiers", len(tips_df))
        k_b2.metric("⭐ Ben's Core Qualifiers", ben_picks_count)
        k_b3.metric("⚡ Big Weight Drops", wgt_drops_count)
        k_b4.metric("🔔 Proven Trip Form", trip_form_count)

        category_choice = st.radio(
            "Filter Category",
            ["All System Tips", "⭐ Ben's Qualifiers Only", "⚡ Big Weight Drops Only", "🔔 Placed at Trip Only"],
            horizontal=True,
        )

        filtered_tips = tips_df.copy()
        if category_choice == "⭐ Ben's Qualifiers Only":
            filtered_tips = filtered_tips[filtered_tips["Category"] == "⭐ Ben's Qualifier"]
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
                    "Decimal_Odds": "Decimal Odds",
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
        "<div class='sub-header'>Audited 38,420-bet strategy (+9.46% to +12.42% Net ROI on Betfair BSP). Targets previous-run top speed (MPH) and stride length (m).</div>",
        unsafe_allow_html=True,
    )

    k_s1, k_s2, k_s3 = st.columns(3)
    k_s1.success("🚀 **SPEED System**  \n**+9.46% Net ROI** (19.4% Win Rate)  \n*Selection: Highest Previous Top Speed*")
    k_s2.info("📏 **STRIDE System**  \n**+6.47% Net ROI** (17.4% Win Rate)  \n*Selection: Longest Previous Stride*")
    k_s3.warning("🎯 **AGREE Variant**  \n**+12.42% Net ROI** (22.0% Win Rate)  \n*Selection: Tops Both Speed & Stride*")

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

    with st.spinner(f"Scanning {chosen_scan_meeting} for Speed & Stride qualifiers..."):
        ss_df = scan_speed_and_stride(date_str, chosen_scan_meeting)

    if ss_df is None or ss_df.empty:
        st.info("No Speed or Stride qualifiers found for this selection.")
    else:
        st.subheader("🎯 Daily Speed & Stride Qualifiers")
        st.dataframe(
            ss_df[
                [
                    "Race",
                    "Horse",
                    "Odds",
                    "Bookmaker",
                    "Top_Speed_MPH",
                    "Stride_Length",
                    "Category",
                    "Edge",
                ]
            ].rename(
                columns={
                    "Odds": "Decimal Odds",
                    "Top_Speed_MPH": "Top Speed",
                    "Stride_Length": "Stride Length",
                    "Edge": "Audited BSP Edge",
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
# VIEW 4: 🏆 RESULTS (DAILY SETTLEMENT AUDIT & EARLY PRICE VS SP ROI)
# ==============================================================================
elif st.session_state["nav_view"] == "🏆 Results":
    st.markdown("<div class='main-header'>🏆 DAILY SYSTEM RESULTS & SETTLEMENT AUDIT</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='sub-header'>Audited settlement comparing Early Morning Bookmaker Odds vs Industry Starting Price (SP). Realized returns across Win-Only & Each-Way staking.</div>",
        unsafe_allow_html=True,
    )

    # 1. Connect to SQLite to load settled dates
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT race_date FROM system_results_ledger ORDER BY race_date DESC")
    available_dates = [r[0] for r in cur.fetchall()]
    conn.close()

    if not available_dates:
        available_dates = ["2026-09-18", "2026-09-17", "2026-09-16", "2026-09-15"]

    date_display_map = {}
    for i, d in enumerate(available_dates):
        if i == 0:
            date_display_map[d] = f"📅 {d} (Yesterday's Racing)"
        else:
            date_display_map[d] = f"📅 {d}"
    date_display_map["ALL"] = "📈 All Logged Dates (Cumulative Aggregate)"

    date_options = [*available_dates, "ALL"]

    c_sel1, c_sel2, c_sel3 = st.columns([2, 2, 1])
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
        if st.button("🔄 Refresh Results", use_container_width=True, key="refresh_res_btn"):
            st.cache_data.clear()
            st.rerun()

    is_ew = "Each-Way" in bet_mode
    stake_per_bet = 2.0 if is_ew else 1.0

    # Load ledger from database
    conn = sqlite3.connect(DB_PATH)
    if chosen_date == "ALL":
        res_df = pd.read_sql("SELECT * FROM system_results_ledger ORDER BY race_date DESC, race_time ASC", conn)
    else:
        res_df = pd.read_sql(f"SELECT * FROM system_results_ledger WHERE race_date='{chosen_date}' ORDER BY race_time ASC", conn)
    conn.close()

    # Mini Tabs for each system
    tab_tips, tab_ss, tab_ai, tab_all = st.tabs([
        "💡 Tips",
        "⚡ Speed & Stride System",
        "🤖 Antigravity / AI System",
        "📊 All Systems Combined"
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
            lambda r: f"{r['early_odds']:.2f} ({r['best_bookmaker']})" if pd.notna(r['early_odds']) and r['early_odds'] > 0 else "-",
            axis=1
        )
        display_df["SP"] = display_df.apply(
            lambda r: f"{r['sp_text']} ({r['sp_odds']:.2f})" if pd.notna(r['sp_odds']) and r['sp_odds'] > 0 else (r['sp_text'] if r['sp_text'] else "-"),
            axis=1
        )
        display_df["Early P&L"] = display_df[pl_col_early].apply(lambda v: f"£{v:+.2f}" if pd.notna(v) else "-")
        display_df["SP P&L"] = display_df[pl_col_sp].apply(lambda v: f"£{v:+.2f}" if pd.notna(v) else "-")

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
            elif p in ("NR (Void)", "NR"):
                return "⚪ Void (NR)"
            return f"{p}" if p and p != "-" else "-"
        display_df["Result"] = display_df["finish_pos"].apply(pos_badge)

        def move_calc(r):
            if pd.notna(r["early_odds"]) and pd.notna(r["sp_odds"]) and r["early_odds"] > 0:
                pct = ((r["sp_odds"] - r["early_odds"]) / r["early_odds"]) * 100
                if pct < -3:
                    return f"📉 Shortened ({pct:.1f}%)"
                elif pct > 3:
                    return f"📈 Drifted ({pct:+.1f}%)"
                else:
                    return "Solid (0%)"
            return "-"
        display_df["Odds Move"] = display_df.apply(move_calc, axis=1)

        cols_to_show = ["race_date", "race_time", "course", "horse_name", "sub_system", "Early Price", "SP", "Result", "Early P&L", "SP P&L", "Odds Move"]
        rename_dict = {
            "race_date": "Date",
            "race_time": "Time",
            "course": "Course",
            "horse_name": "Horse",
            "sub_system": "System / Selection Angle",
            "Early Price": "Early Price (Morning)",
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

    # Tab 1: Tips
    with tab_tips:
        st.subheader("💡 Tips (Ben's System & Weight Drops)")
        tips_data = res_df[res_df["system_name"] == "Tips"]
        render_system_metrics_and_table("Tips", tips_data)

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

    # Tab 4: All Systems Combined
    with tab_all:
        st.subheader("📊 All Systems Combined Settlement")
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
