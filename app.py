# ==============================================================================
# HR BEST TIMES & TELEMETRY CLOUD APPLICATION (STREAMLIT COMMUNITY CLOUD)
# ==============================================================================
import datetime as dt
import json
import os
import re
import sqlite3
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rtv_api

# ------------------------------------------------------------------------------
# Page Setup & Styling
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="HR Best Times & Telemetry",
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
    grouped = {}
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
    odds_map = {}
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
        best_book_str = "-"
        extra_places_str = "-"
        if valid_quotes:
            best_q = max(valid_quotes, key=lambda x: x["decimal"])
            best_book_str = f"{best_q['decimal']:.2f} ({best_q['bookmaker_name']})"
            max_pl = max((q.get("places") for q in valid_quotes if q.get("places")), default=0)
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

            if lto_wgt and net_wgt:
                try:
                    delta_weight = net_wgt - int(lto_wgt)
                except Exception:
                    pass

            target_course_clean = course_slug.lower().strip()
            dist_text = race_info.get("distance_formatted", "") or race_info.get("distance", "")

            for idx, row in enumerate(rp_rows):
                pos = str(row[3] or "")
                c_name = str(row[1] or "").lower().strip()
                dist_str = str(row[2] or "").lower().replace(" ", "")
                odds_str = str(row[10] or "")

                if pos == "1":
                    win_rows.append(row)
                    if target_course_clean in c_name or c_name in target_course_clean:
                        has_won_course = True
                    if str(dist_text).lower().replace(" ", "")[:2] in dist_str:
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
                "Best_Book": best_book_str,
                "Extra_Places": extra_places_str,
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

# Flatten all day races and sort chronologically by time
all_day_races = []
for c, r_list in schedule.items():
    all_day_races.extend(r_list)
all_day_races.sort(key=lambda x: (x.get("time", "99:99"), x.get("course_name", "")))
pill_options = [f"{r.get('time')} {r.get('course_name')}" for r in all_day_races]

# Maintain active race selection across ribbon and sidebar
if "selected_race_idx" not in st.session_state or st.session_state["selected_race_idx"] >= len(all_day_races):
    st.session_state["selected_race_idx"] = 0

# Sidebar Selectbox Filter (Synchronized)
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

# Check if sidebar selection changed
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
horse_search = st.sidebar.text_input("🔍 Quick Horse History Search", placeholder="e.g. Oakford")
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

# Get selected race metadata
active_race = all_day_races[st.session_state["selected_race_idx"]]
selected_course = active_race["course_name"]
selected_time = active_race["time"]
course_slug = active_race["course_slug"]
hhmm = active_race["hhmm"]

st.markdown("---")

# ------------------------------------------------------------------------------
# Top Navigation Bar
# ------------------------------------------------------------------------------
if "nav_view" not in st.session_state:
    st.session_state["nav_view"] = "🏇 Racecard, Odds & Ranks"

nav_options = ["🏇 Racecard, Odds & Ranks", "📖 Horse Career Profile"]
view_mode = st.radio(
    "Navigation View",
    nav_options,
    index=nav_options.index(st.session_state["nav_view"]) if st.session_state["nav_view"] in nav_options else 0,
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
        verdict = race_info.get("analyst_verdict", "")

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

        if verdict:
            st.info(f"💡 **Analyst Verdict**: {verdict}")

        st.subheader("⚡ Master Rankings, Live Decimal Odds & Extra Place Offers")

        display_cols = [
            "Master_Rank",
            "No",
            "Horse",
            "Best_Book",
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
                    "Best_Book": "Best Decimal Odds",
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
                c1.metric("Live Market Odds", f"{row['Best_Book']}")
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

                # Inline Previous Runs Quick-View
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
# VIEW 2: HORSE CAREER PROFILE
# ==============================================================================
elif st.session_state["nav_view"] == "📖 Horse Career Profile":
    target_horse = st.session_state.get("selected_horse", "Oakford")
    
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

        # Last winning weight description
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
