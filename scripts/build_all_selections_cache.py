import datetime as dt
import json
import os
import re
import sqlite3
import sys
from typing import Any

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOUD_DIR = os.path.join(PROJECT_DIR, "cloud_app")

sys.path.insert(0, CLOUD_DIR)
import betfair_ew_service as ew
import rtv_api

# Reuse the telemetry junk-filter bands defined by the settlement sync, so the
# cloud cache and the ledger always agree on what counts as a usable reading.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# The selection rule (thresholds, categories) is shared with the app and the
# ledger - see cloud_app/speed_stride_rule.py.
import speed_stride_rule as ss_rule
from sync_results_ledger import SPEED_MAX, SPEED_MIN, STRIDE_MAX, STRIDE_MIN

# Measured returns per category, published by scripts/backtest_speed_and_stride.py
# so the cache labels the categories with figures a measurement produced.
SS_CLAIMS = ss_rule.load_claims()

today = dt.date.today().isoformat()
print(f"Building complete selections cache for {today}...")

# 1. Fetch Betfair WIN and PLACE markets
tok = ew.login()
cat = ew.fetch_today_catalogue(today, tok)

win_mkts = [m for m in cat if m.get("description", {}).get("marketType") == "WIN"]
place_mkts = [m for m in cat if m.get("description", {}).get("marketType") == "PLACE"]

all_m_ids = [m["marketId"] for m in win_mkts] + [m["marketId"] for m in place_mkts]
print(f"Fetching {len(all_m_ids)} market books (WIN + PLACE)...")
books = ew.fetch_market_books(all_m_ids, token=tok)
book_by_id = {b["marketId"]: b for b in books}

bf_win_map = {}
for m in win_mkts:
    b = book_by_id.get(m["marketId"])
    if not b:
        continue
    r_names = {str(r["selectionId"]): ew.normalize_name(r["runnerName"]) for r in m.get("runners", [])}
    for r in b.get("runners", []):
        name = r_names.get(str(r.get("selectionId")))
        if not name:
            continue
        ex = r.get("ex", {})
        lays = ex.get("availableToLay", [])
        backs = ex.get("availableToBack", [])
        p = lays[0]["price"] if lays else (backs[0]["price"] if backs else None)
        if p:
            bf_win_map[name] = round(float(p), 2)

bf_place_map = {}
bf_place_terms_map = {}
for m in place_mkts:
    b = book_by_id.get(m["marketId"])
    if not b:
        continue
    num_winners = b.get("numberOfWinners", 3)
    r_names = {str(r["selectionId"]): ew.normalize_name(r["runnerName"]) for r in m.get("runners", [])}
    for r in b.get("runners", []):
        name = r_names.get(str(r.get("selectionId")))
        if not name:
            continue
        ex = r.get("ex", {})
        backs = ex.get("availableToBack", [])
        lays = ex.get("availableToLay", [])
        p = backs[0]["price"] if backs else (lays[0]["price"] if lays else None)
        if p:
            bf_place_map[name] = round(float(p), 2)
            bf_place_terms_map[name] = f"{num_winners} Pl"

# Save updated bf_odds_today.json
out_bf_path = os.path.join(CLOUD_DIR, "bf_odds_today.json")
if not bf_win_map or not bf_place_map:
    print(f"[WARN] Betfair returned only {len(bf_win_map)} win / {len(bf_place_map)} place prices.")
    print(f"[WARN] Keeping the existing {out_bf_path} untouched so the app keeps working.")
else:
    with open(out_bf_path, "w", encoding="utf-8") as f:
        json.dump({
            "date": today,
            "odds": bf_win_map,  # backward compatibility
            "win": bf_win_map,
            "place": bf_place_map,
            "place_terms": bf_place_terms_map,
        }, f, indent=2)
    print(f"Saved {len(bf_win_map)} win odds & {len(bf_place_map)} place odds to {out_bf_path}")

# Helper for formatted BF place string
def get_bf_place_str(clean_name: str) -> str:
    p = bf_place_map.get(clean_name)
    terms = bf_place_terms_map.get(clean_name)
    if p and terms:
        return f"{p:.2f} ({terms})"
    elif p:
        return f"{p:.2f}"
    return "-"

# 2. Build Tips Picks
print("Scanning Tips Selections...")
db_path = os.path.join(CLOUD_DIR, "racing_form.db")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

day_races = rtv_api.day_races(today)
day_races.sort(key=lambda x: (x.get("time", ""), x.get("course_name", "")))

tips_picks = []
for r in day_races:
    time_str = r.get("time", "")
    c_name = r.get("course_name", "")
    c_slug = r.get("course_slug", "")
    hhmm = r.get("hhmm", "")

    try:
        d = rtv_api.race_detail(today, c_slug, hhmm)
    except Exception:
        continue
    if not d or "race" not in d:
        continue

    race_info = d.get("race", {})
    runners = rtv_api.runners_of(d)
    dist_text = str(race_info.get("distance_formatted", "") or race_info.get("distance", "")).lower().replace(" ", "")

    odds_map: dict = {}
    try:
        odds_res, _ = rtv_api.runner_odds([x["runner_id"] for x in runners])
        odds_map = odds_res or {}
    except Exception:
        pass

    for run in runners:
        if run.get("status") == "scratched":
            continue
        h_name = str(run.get("horse_name", "")).strip()
        h_c_val = ew.normalize_name(h_name)
        bf_odds_val = bf_win_map.get(h_c_val)
        bf_place_str = get_bf_place_str(h_c_val)

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

        if delta_wgt <= -8 and best_ts >= 60:
            angles.append(f"⚡ Featherweight Drop ({delta_wgt:+d} lb, Peak TS {best_ts})")
            category = "⚡ Big Weight Drop"
        elif (delta_wgt < 0 or (lto_or and last_win_or and lto_or <= last_win_or)) and placings_at_trip >= 1 and lto_pos in ("1", "2", "3", "4"):
            angles.append(f"⭐ Value Pick (In Form pos {lto_pos}, {placings_at_trip}x Trip Placed)")
            category = "⭐ Value Qualifier"
        elif placings_at_trip >= 3 and lto_pos in ("2", "3") and best_ts >= 60:
            angles.append(f"🔔 Knocking on Door (Pos {lto_pos} LTO, {placings_at_trip}x Trip Placed)")
            category = "🔔 Placed at Trip"

        if angles:
            tips_picks.append({
                "Category": category,
                "Race": f"{time_str} {c_name}",
                "Horse": h_name,
                "Decimal_Odds": f"{best_decimal:.2f}" if best_decimal else "-",
                "BF_Odds": f"{bf_odds_val:.2f}" if bf_odds_val else "-",
                "BF_Place": bf_place_str,
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
            })

tips_out_path = os.path.join(CLOUD_DIR, "tips_today.json")
with open(tips_out_path, "w", encoding="utf-8") as f:
    json.dump({"date": today, "picks": tips_picks}, f, indent=2)
print(f"Saved {len(tips_picks)} Tips picks to {tips_out_path}")

# 3. Build Speed & Stride Picks
print("Scanning Speed & Stride Selections...")
ss_picks = []
for r in day_races:
    c_slug = r.get("course_slug", "")
    hhmm = r.get("hhmm", "")
    time_str = r.get("time", "")
    c_name = r.get("course_name", "")

    try:
        d = rtv_api.race_detail(today, c_slug, hhmm)
    except Exception:
        continue
    if not d or "race" not in d:
        continue
    runners = rtv_api.runners_of(d)
    if not runners:
        continue

    odds_map = {}
    try:
        odds_res, _ = rtv_api.runner_odds([x["runner_id"] for x in runners])
        odds_map = odds_res or {}
    except Exception:
        pass

    race_telemetry: list[dict[str, Any]] = []
    for run in runners:
        if run.get("status") == "scratched":
            continue
        h_name = str(run.get("horse_name", "")).strip()
        # Telemetry is stored without the country suffix and SQLite's "=" is
        # case-sensitive, so match on the bare name with NOCASE + LIKE.
        h_stub = re.sub(r"\s*\([^)]*\)\s*$", "", h_name).strip()
        h_clean = ew.normalize_name(h_name)
        rid = run.get("runner_id")

        # Latest usable speed and stride can come from different runs: v2 rows
        # carry both, but the older v1 rows only ever held a stride.  Look each
        # metric up on its own so a recent stride-only row cannot shadow the
        # horse's speed from an earlier run (and vice versa).
        cur.execute(
            """
            SELECT top_speed
            FROM raceiq_telemetry
            WHERE (horse_name = ? COLLATE NOCASE OR horse_name LIKE ?)
              AND top_speed BETWEEN ? AND ?
            ORDER BY race_date DESC LIMIT 1
        """,
            (h_stub, f"{h_stub}%", SPEED_MIN, SPEED_MAX),
        )
        row_speed = cur.fetchone()
        ts_val = float(row_speed[0]) if row_speed else 0.0

        cur.execute(
            """
            SELECT stride_length
            FROM raceiq_telemetry
            WHERE (horse_name = ? COLLATE NOCASE OR horse_name LIKE ?)
              AND stride_length BETWEEN ? AND ?
            ORDER BY race_date DESC LIMIT 1
        """,
            (h_stub, f"{h_stub}%", STRIDE_MIN, STRIDE_MAX),
        )
        row_stride = cur.fetchone()
        str_val = float(row_stride[0]) if row_stride else 0.0

        cur.execute(
            """
            SELECT fsp_pct
            FROM raceiq_telemetry
            WHERE (horse_name = ? COLLATE NOCASE OR horse_name LIKE ?) AND fsp_pct IS NOT NULL
            ORDER BY race_date DESC LIMIT 1
        """,
            (h_stub, f"{h_stub}%"),
        )
        row_fsp = cur.fetchone()
        cad_val = float(row_fsp[0]) if row_fsp else 0.0

        quotes = odds_map.get(rid, [])
        valid_quotes = [q for q in quotes if q.get("decimal") and q["decimal"] > 1.0]
        best_decimal = None
        best_bookie = "-"
        extra_places = "-"
        if valid_quotes:
            best_q = max(valid_quotes, key=lambda x: x["decimal"])
            best_decimal = round(float(best_q["decimal"]), 2)
            best_bookie = str(best_q["bookmaker_name"])
            pl_counts = [int(q["places"]) for q in valid_quotes if q.get("places")]
            max_pl = max(pl_counts) if pl_counts else 0
            if max_pl >= 4:
                pl_books = [q["bookmaker_name"] for q in valid_quotes if q.get("places") == max_pl]
                extra_places = f"{max_pl} Pl ({', '.join(pl_books[:2])})"

        bf_odds_val = bf_win_map.get(h_clean)
        bf_place_str = get_bf_place_str(h_clean)

        race_telemetry.append({
            "Horse": h_name,
            "h_clean": h_clean,
            "best_decimal": best_decimal,
            "best_bookie": best_bookie,
            "extra_places": extra_places,
            "bf_odds_val": bf_odds_val,
            "bf_place_str": bf_place_str,
            "ts_val": ts_val,
            "cad_val": cad_val,
            "str_val": str_val,
        })

    if not race_telemetry:
        continue

    # The rule itself lives in speed_stride_rule.py - one implementation shared
    # with the live scanner in app.py and the settlement ledger.
    row_by_horse = {ss_rule.norm_horse(x["Horse"]): x for x in race_telemetry}
    for horse, category in ss_rule.evaluate(
        (x["Horse"], x["ts_val"] or None, x["str_val"] or None) for x in race_telemetry
    ):
        cand = row_by_horse.get(ss_rule.norm_horse(horse))
        if cand is None:
            continue
        ss_picks.append({
            "Race": f"{time_str} {c_name}",
            "Horse": cand["Horse"],
            "Odds": f"{cand['best_decimal']:.2f}" if cand["best_decimal"] else "-",
            "BF_Odds": f"{cand['bf_odds_val']:.2f}" if cand["bf_odds_val"] else "-",
            "BF_Place": cand["bf_place_str"],
            "Bookmaker": cand["best_bookie"],
            "Extra_Places": cand["extra_places"],
            "Top_Speed_MPH": f"{cand['ts_val']:.1f} mph" if cand["ts_val"] else "-",
            "Stride_Length": f"{cand['str_val']:.2f} m" if cand["str_val"] else "-",
            "Category": category,
            "Edge": ss_rule.edge_label(category, SS_CLAIMS),
            "course_slug": c_slug,
            "hhmm": hhmm,
            "raw_odds": cand["best_decimal"] or 999.0,
        })

ss_out_path = os.path.join(CLOUD_DIR, "speed_stride_today.json")
with open(ss_out_path, "w", encoding="utf-8") as f:
    json.dump({"date": today, "rows": ss_picks}, f, indent=2)
print(f"Saved {len(ss_picks)} Speed & Stride selections to {ss_out_path}")

conn.close()
print("All selections pre-computed and saved successfully!")
