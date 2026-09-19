# ==============================================================================
# HR BEST TIMES & FORM ANALYZER - INTERACTIVE SOFTWARE (STREAMLIT APP)
# ==============================================================================
import contextlib
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys

import pandas as pd
import plotly.graph_objects as go
import pyodbc
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rtv_api

# ------------------------------------------------------------------------------
# Page Setup & Styling
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="HR Best Times & Telemetry Software",
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
        margin-bottom: 16px;
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
    .extra-places {
        background: #DCFCE7;
        color: #166534;
        padding: 2px 6px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 12px;
    }
    .betfair-badge {
        background: #FEF08A;
        color: #854D0E;
        padding: 2px 6px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 12px;
    }
</style>
""",
    unsafe_allow_html=True,
)


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


def get_live_odds_and_places(date_str, course_clean, time_str):
    """Retrieve bookmaker quotes, extra places, and live Betfair exchange prices."""
    book_map: dict[str, list[dict]] = {}
    bf_map: dict[str, dict] = {}
    t_fmt = str(time_str).strip()
    if ":" not in t_fmt and len(t_fmt) == 4:
        t_fmt = f"{t_fmt[:2]}:{t_fmt[2:]}"
    t_sql = f"{t_fmt[:5]}%"

    with contextlib.suppress(Exception):
        conn_pro = pyodbc.connect(
            r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;"
        )
        cur_pro = conn_pro.cursor()

        # 1. Bookmaker Odds & Extra Place Offers
        cur_pro.execute(
            """
            SELECT HorseClean, BookmakerName, PriceFractional, PriceDecimal, EWPlaces, EWDenominator
            FROM dbo.BookOdds
            WHERE RaceDate = ? AND CourseClean = ? AND RaceTime LIKE ?
        """,
            (date_str, course_clean, t_sql),
        )
        for hc, b, frac, dec, pl, den in cur_pro.fetchall():
            book_map.setdefault(hc, []).append(
                {"book": b, "frac": frac, "dec": dec, "places": pl, "den": den}
            )

        # 2. Betfair Exchange Live Prices
        cur_pro.execute(
            """
            SELECT HorseClean, Back1, Lay1, TradedVolume
            FROM dbo.BetfairLive
            WHERE RaceDate = ? AND VenueClean = ?
            AND SnapshotAt = (SELECT MAX(SnapshotAt) FROM dbo.BetfairLive WHERE RaceDate = ?)
        """,
            (date_str, course_clean, date_str),
        )
        for hc, back, lay, vol in cur_pro.fetchall():
            bf_map[hc] = {"back": back, "lay": lay, "vol": vol}

        conn_pro.close()

    return book_map, bf_map


def get_racecard_data(date_str, course_slug, hhmm, time_str):
    d = rtv_api.race_detail(date_str, course_slug, hhmm)
    if not d or "race" not in d:
        return None, None

    runners = rtv_api.runners_of(d)
    race_info = d.get("race", {})

    conn_rp = sqlite3.connect(r"D:\RacingPost_Horse\racingpost_master.db")
    cur_rp = conn_rp.cursor()

    conn_rtv = pyodbc.connect(
        r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;Database=RACINGTV_2023_2026;Trusted_Connection=yes;"
    )
    cur_rtv = conn_rtv.cursor()

    # Load Live Bookmaker & Betfair Odds
    book_odds_map, bf_odds_map = get_live_odds_and_places(date_str, course_slug, time_str)

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
        clean_h = rtv_api.clean_name(h_name)

        # 1. Weight & Claim
        claim = 0
        wgt_lbs = 0
        if "-" in str(weight_st):
            with contextlib.suppress(Exception):
                st_part, lb_part = str(weight_st).split("-")
                wgt_lbs = int(st_part) * 14 + int(lb_part)

        c_match = re.search(r"\((\d+)\)", jockey)
        if c_match:
            claim = int(c_match.group(1))
        net_wgt = wgt_lbs - claim if wgt_lbs else 0

        # 2. Live Bookmaker & Betfair Odds (in Decimal format)
        quotes = book_odds_map.get(clean_h, [])
        best_book_str = "-"
        best_book_dec = 0.0
        extra_places_str = "-"
        if quotes:
            best_q = max(quotes, key=lambda x: x["dec"])
            best_book_dec = round(best_q["dec"], 2)
            best_book_str = f"{best_q['dec']:.2f} ({best_q['book']})"
            max_pl = max((q["places"] for q in quotes if q["places"]), default=0)
            if max_pl >= 4:
                pl_books = [q["book"] for q in quotes if q["places"] == max_pl]
                extra_places_str = f"{max_pl} Places ({', '.join(pl_books[:2])})"

        bf = bf_odds_map.get(clean_h, {})
        bf_back = bf.get("back")
        bf_lay = bf.get("lay")
        betfair_str = (
            f"{bf_back:.2f} / {bf_lay:.2f}"
            if bf_back and bf_lay
            else (f"{bf_back:.2f}" if bf_back else "-")
        )

        edge_str = "-"
        if best_book_dec > 1.0 and bf_back:
            diff_pct = ((bf_back - best_book_dec) / best_book_dec) * 100
            edge_str = f"{diff_pct:+.0f}%"

        # 3. Exact horse match in Racing Post History
        cur_rp.execute(
            """
            SELECT race_date, meeting, distance, finish_pos, beaten_distance, weight_lbs,
                   official_rating, topspeed, rpr, comment, sp_odds
            FROM race_results
            WHERE horse_name = ? OR horse_name LIKE ?
            ORDER BY race_date DESC
        """,
            (h_name, f"{h_name} (%"),
        )
        rp_rows = cur_rp.fetchall()

        ts_list = []
        rpr_list = []
        last_comment = ""
        betting_movements = ""
        last_run_desc = "No prior runs"
        delta_weight = None
        has_won_course = False
        has_won_dist = False
        was_beaten_fav = False

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
                with contextlib.suppress(Exception):
                    delta_weight = net_wgt - int(lto_wgt)

            target_course_clean = course_slug.lower().strip()
            dist_text = race_info.get("distance_formatted", "") or race_info.get("distance", "")

            for idx, row in enumerate(rp_rows):
                pos = str(row[3] or "")
                c_name = str(row[1] or "").lower().strip()
                dist_str = str(row[2] or "").lower().replace(" ", "")
                odds_str = str(row[10] or "")

                if pos == "1":
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

                with contextlib.suppress(Exception):
                    if row[7] and int(row[7]) > 0:
                        ts_list.append(int(row[7]))
                with contextlib.suppress(Exception):
                    if row[8] and int(row[8]) > 0:
                        rpr_list.append(int(row[8]))

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

        best_ts = max(ts_list) if ts_list else None
        avg_ts_3 = round(sum(ts_list[:3]) / len(ts_list[:3]), 1) if ts_list else None
        best_rpr = max(rpr_list) if rpr_list else None

        # 4. Coursetrack GPS Telemetry
        cur_rtv.execute(
            """
            SELECT StrideLength, TopSpeed, FinishingSpeedPct
            FROM dbo.Scraped_RaceIQ
            WHERE (HorseName = ? OR HorseName LIKE ?) AND StrideLength IS NOT NULL
        """,
            (h_name, f"{h_name} (%"),
        )
        rtv_rows = cur_rtv.fetchall()

        strides = [float(x[0]) for x in rtv_rows if x[0]]
        speeds = [float(x[1]) for x in rtv_rows if x[1]]
        fsps = [float(x[2]) for x in rtv_rows if x[2]]

        cur_rtv.execute(
            """
            SELECT Value
            FROM dbo.Scraped_RaceIQ_Ranks
            WHERE (HorseName = ? OR HorseName LIKE ?) AND Metric = '0-20MPH' AND Value IS NOT NULL
        """,
            (h_name, f"{h_name} (%"),
        )
        break_rows = cur_rtv.fetchall()
        breaks = []
        for bx in break_rows:
            with contextlib.suppress(Exception):
                breaks.append(float(re.sub(r"[^\d.]", "", str(bx[0]))))

        best_speed = round(max(speeds), 2) if speeds else None
        avg_speed = round(sum(speeds) / len(speeds), 2) if speeds else None
        best_stride = round(max(strides), 2) if strides else None
        avg_stride = round(sum(strides) / len(strides), 2) if strides else None
        avg_fsp = round(sum(fsps) / len(fsps), 1) if fsps else None
        avg_break = round(sum(breaks), 2) if breaks else None

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
                "Best_Book": best_book_str,
                "Betfair": betfair_str,
                "Edge": edge_str,
                "Extra_Places": extra_places_str,
                "Best_TS": best_ts or 0,
                "Avg_TS3": avg_ts_3 or 0,
                "Best_RPR": best_rpr or 0,
                "Best_MPH": best_speed or 0,
                "Avg_MPH": avg_speed or 0,
                "Best_Stride": best_stride or 0,
                "Avg_Stride": avg_stride or 0,
                "FSP_Pct": avg_fsp or 0,
                "Break_Sec": avg_break or 0,
                "Last_Run_Desc": last_run_desc,
                "Last_Comment": last_comment,
                "Betting_LTO": betting_movements,
            }
        )

    conn_rp.close()
    conn_rtv.close()

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
    conn_rp = sqlite3.connect(r"D:\RacingPost_Horse\racingpost_master.db")
    cur_rp = conn_rp.cursor()
    cur_rp.execute(
        """
        SELECT race_date, meeting, distance, finish_pos, beaten_distance, weight_lbs,
               official_rating, topspeed, rpr, jockey, sp_odds, comment
        FROM race_results
        WHERE horse_name = ? OR horse_name LIKE ?
        ORDER BY race_date DESC
    """,
        (horse_name, f"{horse_name} (%"),
    )
    rp_rows = cur_rp.fetchall()
    conn_rp.close()

    if not rp_rows:
        return None

    conn_rtv = pyodbc.connect(
        r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;Database=RACINGTV_2023_2026;Trusted_Connection=yes;"
    )
    cur_rtv = conn_rtv.cursor()
    cur_rtv.execute(
        """
        SELECT RaceDate, CourseName, StrideLength, TopSpeed, FinishingSpeedPct
        FROM dbo.Scraped_RaceIQ
        WHERE (HorseName = ? OR HorseName LIKE ?) AND StrideLength IS NOT NULL
    """,
        (horse_name, f"{horse_name} (%"),
    )

    rtv_map = {}
    for r in cur_rtv.fetchall():
        d_str = str(r[0])
        c_name = re.sub(r"[^a-zA-Z]", "", str(r[1]).lower())
        key = f"{d_str}_{c_name[:4]}"
        rtv_map[key] = {
            "stride": float(r[2]) if r[2] else None,
            "speed": float(r[3]) if r[3] else None,
            "fsp": float(r[4]) if r[4] else None,
        }
    conn_rtv.close()

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
# Sidebar Navigation
# ------------------------------------------------------------------------------
st.sidebar.title("⚡ HR Best Times & Form")
st.sidebar.caption("Proform + Racing Post + Bookmakers + Betfair Exchange")

selected_date = st.sidebar.date_input("Select Racing Date", dt.date.today())
date_str = selected_date.strftime("%Y-%m-%d")

schedule = load_day_schedule(date_str)
if not schedule:
    st.sidebar.warning(f"No race meetings found for {date_str}.")
    st.stop()

course_list = sorted(schedule.keys())
selected_course = st.sidebar.selectbox("Select Meeting", course_list)

race_list = schedule[selected_course]
race_times = [r.get("time") for r in race_list]
selected_time = st.sidebar.selectbox("Select Race Time", race_times)

# Find matching race metadata
race_item = next(r for r in race_list if r.get("time") == selected_time)
course_slug = race_item.get("course_slug")
hhmm = race_item.get("hhmm")

st.sidebar.markdown("---")
# Live Snapshot Button
if st.sidebar.button("🔄 Refresh Live Bookmaker & Betfair Odds"):
    with st.spinner("Fetching latest live exchange & bookmaker prices..."):
        subprocess.run(
            [sys.executable, "-u", os.path.join(os.path.dirname(__file__), "betfair_api.py"), "snapshot", date_str],
            capture_output=True,
            check=False,
        )
        st.sidebar.success("Updated live odds!")
        st.rerun()

st.sidebar.markdown("---")
horse_search = st.sidebar.text_input("🔍 Quick Horse History Search", placeholder="e.g. Boston Dan")
if horse_search:
    st.session_state["selected_horse"] = horse_search

# ------------------------------------------------------------------------------
# Main Window: Tabs
# ------------------------------------------------------------------------------
tab1, tab2 = st.tabs(["🏇 Racecard, Odds & Speed Ranks", "📖 Horse Career History & Telemetry"])

# TAB 1: RACECARD
with tab1:
    with st.spinner("Analyzing live racecard, odds, extra places, and telemetry..."):
        df, race_info = get_racecard_data(date_str, course_slug, hhmm, selected_time)

    if df is None or df.empty:
        st.warning("Could not load runners for this race.")
    else:
        title = race_info.get("title", "")
        dist = race_info.get("distance_formatted", "") or race_info.get("distance", "")
        pace = race_info.get("ip_hints_overall_pace", "N/A")
        draw = race_info.get("draw_comment", "None noted")
        verdict = race_info.get("analyst_verdict", "")

        st.markdown(
            f"<div class='main-header'>{selected_course.upper()} {selected_time} - {title}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div class='sub-header'>Distance: <b>{dist}</b> | Pace Forecast: <b>{pace}</b> | Draw Bias: <b>{draw}</b></div>",
            unsafe_allow_html=True,
        )

        if verdict:
            st.info(f"💡 **Analyst Verdict**: {verdict}")

        # Summary Table
        st.subheader("⚡ Master Rankings, Live Odds & Extra Place Offers")

        display_cols = [
            "Master_Rank",
            "No",
            "Horse",
            "Best_Book",
            "Betfair",
            "Edge",
            "Extra_Places",
            "Flags",
            "DLR",
            "Wgt_Lbs",
            "Claim",
            "dWgt",
            "Best_TS",
            "Avg_TS3",
            "Best_RPR",
            "Best_MPH",
            "Best_Stride",
            "Power_Score",
        ]

        st.dataframe(
            df[display_cols].rename(
                columns={
                    "Master_Rank": "Rank",
                    "Best_Book": "Book Decimal (Best)",
                    "Betfair": "Betfair (Back/Lay)",
                    "Edge": "BF Edge",
                    "Extra_Places": "Extra Places Offer",
                    "Wgt_Lbs": "Wgt(lb)",
                    "dWgt": "Δ Wgt",
                    "Avg_TS3": "Avg TS",
                    "Best_MPH": "Top MPH",
                    "Best_Stride": "Stride(m)",
                    "Power_Score": "Power",
                }
            ),
            use_container_width=True,
            hide_index=True,
            height=min(600, (len(df) + 1) * 35),
        )

        # In-Running Comments, Odds & LTO Details
        st.subheader("📝 Runner Form, Odds & In-Running Comments")
        for _idx, row in df.iterrows():
            with st.expander(
                f"#{row['No']} {row['Horse']} (Rank #{row['Master_Rank']} | Book: {row['Best_Book']} | Betfair: {row['Betfair']} | Power: {row['Power_Score']})"
            ):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Live Market", f"{row['Best_Book']}", f"Betfair: {row['Betfair']}")
                c2.metric("Extra Places", f"{row['Extra_Places']}")
                c3.metric("Weight & Off", f"{row['Wgt_Lbs']} lb ({row['dWgt']})", f"Off: {row['DLR']}d")
                c4.metric(
                    "Ratings & Speed",
                    f"TS: {row['Best_TS']} (Avg: {row['Avg_TS3']})",
                    f"{row['Best_MPH']} mph" if row["Best_MPH"] else "RPR: " + str(row["Best_RPR"]),
                )

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

                if st.button(f"View Full Career History for {row['Horse']}", key=f"btn_{row['Horse']}"):
                    st.session_state["selected_horse"] = row["Horse"]
                    st.rerun()

# TAB 2: HORSE CAREER HISTORY
with tab2:
    target_horse = st.session_state.get("selected_horse", "Boston Dan")
    st.subheader(f"📖 Complete Career Profile: {target_horse.upper()}")

    with st.spinner(f"Loading complete record for {target_horse}..."):
        h_df = load_horse_career(target_horse)

    if h_df is None or h_df.empty:
        st.warning(f"No historical runs found for '{target_horse}'.")
    else:
        total_runs = len(h_df)
        wins = len(h_df[h_df["Pos"] == "1"])
        win_pct = round((wins / total_runs) * 100, 1) if total_runs else 0
        best_ts = h_df["TS"].max() if "TS" in h_df and not h_df["TS"].dropna().empty else "-"
        best_rpr = h_df["RPR"].max() if "RPR" in h_df and not h_df["RPR"].dropna().empty else "-"
        best_mph = h_df["Speed_MPH"].max() if "Speed_MPH" in h_df and not h_df["Speed_MPH"].dropna().empty else "-"

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Career Starts", total_runs)
        k2.metric("Wins (Win %)", f"{wins} ({win_pct}%)")
        k3.metric("Best Topspeed (TS)", best_ts)
        k4.metric("Best RPR", best_rpr)
        k5.metric(
            "Top Speed (MPH)",
            f"{best_mph:.1f} mph" if isinstance(best_mph, (int, float)) else "-",
        )

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
            title="Ratings & Speed Progression Over Time",
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
                m4.write(f"**Speed**: {f'{row['Speed_MPH']:.1f} mph' if row['Speed_MPH'] else '-'}")
                m5.write(f"**Stride**: {f'{row['Stride_m']:.2f}m' if row['Stride_m'] else '-'}")

                if row["Market"]:
                    st.write(f"**Market Moves**: `{row['Market']}`")
                comm_val = row["Comment"] or "No in-running comment recorded."
                st.markdown(
                    f"<div class='comment-card'>💬 <i>\"{comm_val}\"</i></div>",
                    unsafe_allow_html=True,
                )
