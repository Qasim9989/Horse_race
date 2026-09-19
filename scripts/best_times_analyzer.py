# ==============================================================================
# BEST TIMES & HORSE FORM ANALYZER (HR BEST TIMES REPLACEMENT & ENHANCEMENT)
# ==============================================================================
# Combines:
# 1. Racing Post Historical Data:
#    - Official Ratings (OR), Topspeed (TS), Racing Post Ratings (RPR)
#    - Best TS, 3-run Average TS, Best RPR
#    - Last Time Out (LTO) Finish Pos, Beaten Distance, Weight, OR, TS, RPR
#    - Days Since Last Run (Off / DLR)
#    - Course & Distance Winner Flags (C, D, CD, BF)
#    - Net Weight, Jockey Claims, Weight Shifts (dWgt)
#    - Full In-Running Race Comments & LTO Market Betting Movements
# 2. Racing TV Coursetrack GPS Telemetry:
#    - Top Speed (MPH), Average Speed (MPH), Speed Field Ranks
#    - Stride Length (m), Average Stride (m), Stride Field Ranks
#    - 0-20 MPH Stall Break Acceleration (sec)
#    - Finishing Speed Percentage (FSP %)
# 3. Master Power & Speed Composite Rank
# ==============================================================================

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import sqlite3
import sys

import pandas as pd
import pyodbc

sys.path.insert(0, os.path.dirname(__file__))
import rtv_api


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


def analyze_race(date_str, course, race_time_hhmm, excel_writer=None):
    course_clean = course.lower().strip()
    hhmm = race_time_hhmm.replace(":", "").strip()

    d = rtv_api.race_detail(date_str, course_clean, hhmm)
    if not d or "race" not in d:
        print(f"[!] Unable to load racecard for {course.upper()} {race_time_hhmm} on {date_str}")
        return None

    runners = rtv_api.runners_of(d)
    race_info = d.get("race", {})
    race_title = race_info.get("title", "")
    distance_text = race_info.get("distance_formatted", "") or race_info.get("distance", "")
    pace_forecast = race_info.get("ip_hints_overall_pace", "N/A")
    draw_bias = race_info.get("draw_comment", "None noted")
    analyst_verdict = race_info.get("analyst_verdict", "")

    print(f"\n{'=' * 110}")
    print(f" {course.upper()} {race_time_hhmm} - {race_title} ({distance_text}) | Date: {date_str}")
    print(f" Pace Forecast: {pace_forecast} | Draw Bias: {draw_bias}")
    if analyst_verdict:
        print(f" Verdict: {analyst_verdict[:140]}...")
    print(f"{'=' * 110}")

    conn_rp = sqlite3.connect(r"D:\RacingPost_Horse\racingpost_master.db")
    cur_rp = conn_rp.cursor()

    conn_rtv = pyodbc.connect(
        r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;Database=RACINGTV_2023_2026;Trusted_Connection=yes;"
    )
    cur_rtv = conn_rtv.cursor()

    # Load Live Bookmaker & Betfair Odds
    book_odds_map, bf_odds_map = get_live_odds_and_places(date_str, course_clean, race_time_hhmm)

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

        # Odds & Extra Places (Decimal Format)
        quotes = book_odds_map.get(clean_h, [])
        best_book_str = "-"
        extra_places_str = "-"
        if quotes:
            best_q = max(quotes, key=lambda x: x["dec"])
            best_book_str = f"{best_q['dec']:.2f} ({best_q['book']})"
            max_pl = max((q["places"] for q in quotes if q["places"]), default=0)
            if max_pl >= 4:
                pl_books = [q["book"] for q in quotes if q["places"] == max_pl]
                extra_places_str = f"{max_pl}pl ({', '.join(pl_books[:2])})"

        bf = bf_odds_map.get(clean_h, {})
        bf_back = bf.get("back")
        bf_lay = bf.get("lay")
        betfair_str = (
            f"{bf_back:.2f}/{bf_lay:.2f}"
            if bf_back and bf_lay
            else (f"{bf_back:.2f}" if bf_back else "-")
        )

        # 1. Weight & Jockey Claim
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

        # 2. Racing Post Past Form & Last Run Comments (Exact Match)
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

            target_course_clean = course_clean
            for idx, row in enumerate(rp_rows):
                pos = str(row[3] or "")
                c_name = str(row[1] or "").lower().strip()
                dist_str = str(row[2] or "").lower().replace(" ", "")
                odds_str = str(row[10] or "")

                if pos == "1":
                    if target_course_clean in c_name or c_name in target_course_clean:
                        has_won_course = True
                    # Check distance match
                    if str(distance_text).lower().replace(" ", "")[:2] in dist_str:
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

        # 3. Racing TV Coursetrack Telemetry (Exact Match)
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
        print("[!] No runners found.")
        return None

    # Calculate field ranks
    df["R_BestTS"] = df["Best_TS"].rank(ascending=False, method="min").astype(int)
    df["R_AvgTS"] = df["Avg_TS3"].rank(ascending=False, method="min").astype(int)
    df["R_RPR"] = df["Best_RPR"].rank(ascending=False, method="min").astype(int)

    # Master Power Score
    df["Power_Score"] = (
        (df["Best_TS"] * 0.35)
        + (df["Avg_TS3"] * 0.35)
        + (df["Best_RPR"] * 0.30)
        - (df["Wgt_Lbs"] * 0.15)
    ).round(1)
    df["Master_Rank"] = df["Power_Score"].rank(ascending=False, method="min").astype(int)

    df_sorted = df.sort_values("Master_Rank").reset_index(drop=True)

    summary_cols = [
        "Master_Rank",
        "No",
        "Horse",
        "Best_Book",
        "Betfair",
        "Extra_Places",
        "Flags",
        "DLR",
        "Wgt_Lbs",
        "Claim",
        "dWgt",
        "Best_TS",
        "Avg_TS3",
        "Best_RPR",
        "Power_Score",
    ]

    print("\n--- MASTER POWER & SPEED RANKINGS ---")
    print(df_sorted[summary_cols].to_string(index=False))

    print(f"\n{'=' * 110}")
    print(" LAST RACE IN-RUNNING COMMENTS & MARKET MOVES")
    print(f"{'=' * 110}")
    for _idx, row in df_sorted.iterrows():
        print(
            f"[{row['Master_Rank']:02d}] #{row['No']:02d} {row['Horse'].upper()} ({row['Flags']}) | Off: {row['DLR']} days | Wgt: {row['Wgt_Lbs']}lb (Claim: {row['Claim']}, dWgt: {row['dWgt']})"
        )
        print(f"     Last Run: {row['Last_Run_Desc']}")
        if row["Betting_LTO"]:
            print(f"     Market Move LTO: {row['Betting_LTO']}")
        print(f"     Comment: \"{row['Last_Comment'] or 'No in-running comment recorded.'}\"")
        print(f"{'-' * 110}")

    if excel_writer:
        sheet_name = f"{course_clean[:8]}_{hhmm}"
        df_sorted.to_excel(excel_writer, sheet_name=sheet_name, index=False)

    return df_sorted


def show_horse_history(horse_name):
    print(f"\n{'=' * 110}")
    print(f" COMPLETE CAREER HISTORY & TELEMETRY: {horse_name.upper()}")
    print(f"{'=' * 110}")

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
        print(f"[!] No career runs found for {horse_name}")
        return

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

    print(f"Found {len(rp_rows)} career starts:\n")
    for idx, r in enumerate(rp_rows, 1):
        d_str, course, dist, pos, btn, wgt, or_val, ts, rpr, jock, sp, comm_raw = r
        c_clean = re.sub(r"[^a-zA-Z]", "", str(course).lower())
        key = f"{d_str}_{c_clean[:4]}"
        tel = rtv_map.get(key, {})

        comm_text, mkt_text = parse_comment_text(comm_raw)
        btn_str = f"btn {btn}L" if btn and str(pos) != "1" else ("WON" if str(pos) == "1" else "")

        speed_str = f"{tel['speed']:.1f}mph" if tel.get("speed") else "-"
        stride_str = f"{tel['stride']:.2f}m" if tel.get("stride") else "-"
        fsp_str = f"{tel['fsp']:.1f}%" if tel.get("fsp") else "-"

        print(
            f"[{idx:02d}] {d_str} {course} ({dist}) - Pos: {pos} {btn_str} | Wgt: {wgt}lb | SP: {sp} | Jockey: {jock}"
        )
        print(
            f"     Ratings: OR {or_val or '-'} | TS {ts or '-'} | RPR {rpr or '-'}  ||  Telemetry: TopSpeed {speed_str} | Stride {stride_str} | FSP {fsp_str}"
        )
        if mkt_text:
            print(f"     Market Move: {mkt_text}")
        print(f"     Comment: \"{comm_text or 'No comment recorded.'}\"")
        print("-" * 110)


def main():
    parser = argparse.ArgumentParser(description="Best Times & Horse Form Analyzer")
    parser.add_argument(
        "--race", nargs=2, metavar=("TIME", "COURSE"), help="Analyze single race (e.g. --race 1540 ayr)"
    )
    parser.add_argument(
        "--day",
        default=None,
        help="Analyze all races for a date (e.g. --day today or --day 2026-09-18)",
    )
    parser.add_argument(
        "--horse", help="Show complete career history and comments for a horse"
    )
    parser.add_argument("--excel", action="store_true", help="Export racecards to Excel")
    args = parser.parse_args()

    if args.horse:
        show_horse_history(args.horse)
        return

    date_str = args.day
    if date_str == "today" or (date_str is None and not args.race):
        date_str = dt.date.today().strftime("%Y-%m-%d")

    if args.race:
        r_time, r_course = args.race
        r_date = date_str if date_str else dt.date.today().strftime("%Y-%m-%d")
        analyze_race(r_date, r_course, r_time)
        return

    if date_str:
        print(f"Fetching all races for {date_str}...")
        races = rtv_api.day_races(date_str)
        if not races:
            print(f"[!] No races found for {date_str}")
            return

        excel_writer = None
        if args.excel:
            out_dir = r"E:\Test\racing-form-system\reports"
            os.makedirs(out_dir, exist_ok=True)
            excel_path = os.path.join(out_dir, f"best_times_{date_str}.xlsx")
            excel_writer = pd.ExcelWriter(excel_path, engine="openpyxl")
            print(f"[+] Generating Excel report: {excel_path}")

        print(f"Loaded {len(races)} races across UK & Ireland.")
        for r in races:
            c = r.get("course_slug") or r.get("course_name", "")
            t = r.get("hhmm") or str(r.get("time", "")).replace(":", "")
            analyze_race(date_str, c, t, excel_writer)

        if excel_writer:
            excel_writer.close()
            print("\n[+] All racecards saved successfully to Excel.")


if __name__ == "__main__":
    main()
