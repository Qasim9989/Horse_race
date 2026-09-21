"""
DAILY SYSTEM RESULTS & SETTLEMENT ENGINE
=========================================
Checks finished races on Betfair and Racing TV / LocalDB, updates finish positions,
Starting Prices (SP) and Betfair Starting Prices (BSP), and settles both Win-Only
and Each-Way P&L across all systems.

Usage:
  python scripts/settle_daily_results.py
  python scripts/settle_daily_results.py --date 2026-09-19
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sqlite3
import sys
from typing import Any

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, "cloud_app", "racing_form.db")
CSV_PATH = os.path.join(PROJECT_DIR, "cloud_app", "results_ledger.csv")

sys.path.insert(0, os.path.join(PROJECT_DIR, "cloud_app"))
import betfair_ew_service as ew


def strip_country(name: str) -> str:
    s = re.sub(r"\([^)]*\)", "", str(name))
    s = re.sub(r"[^a-zA-Z0-9\s]", "", s)
    return s.strip().lower()


def parse_sp(sp_val: Any) -> float | None:
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


PRICE_MIN = 1.00
PRICE_MAX = 1000.0          # Betfair's ladder caps at 1000, so a captured 1000 means "no offer"
DIVERGENCE_MAX = 0.50       # scraped SP vs Betfair BSP, relative


def clean_price(value: Any) -> float | None:
    """Return a usable decimal price, or None when the feed gave nonsense.

    Guards the 1000-style outlier and non-numeric text before anything is
    written to the ledger, so no verdict can be built on a bad print.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not (PRICE_MIN < price < PRICE_MAX):
        return None
    return round(price, 2)


def has_diverged(sp_odds: Any, bsp: Any) -> bool:
    """True when the industry SP and Betfair BSP disagree enough to distrust either."""
    sp, bf = clean_price(sp_odds), clean_price(bsp)
    if sp is None or bf is None:
        return False
    return abs(bf - sp) / ((bf + sp) / 2.0) > DIVERGENCE_MAX


def fetch_scraped_results(date_str: str) -> dict[str, dict[str, Any]]:
    """Query localdb Scraped_Results for finished race outcomes."""
    results: dict[str, dict[str, Any]] = {}
    try:
        import pyodbc
        conn_str = r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;Database=RACINGTV_2023_2026;Trusted_Connection=yes;"
        cn = pyodbc.connect(conn_str, timeout=3)
        cur = cn.cursor()
        cur.execute("""
            SELECT HorseName, CourseName, PosNo, SP
            FROM Scraped_Results
            WHERE RaceDate = ?
        """, (date_str,))
        for r in cur.fetchall():
            h_c = strip_country(str(r[0]))
            crs = str(r[1] or "").lower()
            pos = str(r[2] or "").replace(".0", "").strip()
            sp_txt = str(r[3] or "").strip()
            sp_dec = parse_sp(sp_txt)
            results[f"{h_c}_{crs[:4]}"] = {
                "pos": pos,
                "sp_text": sp_txt,
                "sp_odds": sp_dec,
            }
        cn.close()
    except Exception:
        pass
    return results


def fetch_betfair_settled(date_str: str, token: str | None = None) -> dict[str, dict[str, Any]]:
    """Query Betfair Exchange closed markets for winners, placers, and BSP."""
    bf_results: dict[str, dict[str, Any]] = {}
    if not ew.is_configured():
        return bf_results

    try:
        tok = token or ew.login()
        cat = ew.fetch_today_catalogue(date_str, tok)
        closed_mkts = [m for m in cat if m.get("description", {}).get("marketType") in ("WIN", "PLACE")]
        if not closed_mkts:
            return bf_results

        books = ew.fetch_market_books([m["marketId"] for m in closed_mkts], token=tok)
        for b in books:
            if b.get("status") != "CLOSED":
                continue
            meta = next((m for m in closed_mkts if m["marketId"] == b.get("marketId")), None)
            if not meta:
                continue
            m_type = meta.get("description", {}).get("marketType")
            r_names = {str(r["selectionId"]): strip_country(r["runnerName"]) for r in meta.get("runners", [])}
            for r in b.get("runners", []):
                sid = str(r.get("selectionId"))
                h_c = r_names.get(sid)
                if not h_c:
                    continue
                st = r.get("status")
                bsp = r.get("bsp")
                entry = bf_results.setdefault(h_c, {})
                if m_type == "WIN":
                    entry["win_status"] = st
                    bsp_clean = clean_price(bsp)
                    if bsp_clean:
                        entry["bsp"] = bsp_clean
                elif m_type == "PLACE":
                    entry["place_status"] = st
    except Exception as ex:
        print(f"Notice: Betfair closed market query: {ex}")

    return bf_results


def settle_ledger(date_str: str, force: bool = False) -> int:
    if not os.path.exists(CSV_PATH):
        print(f"Error: {CSV_PATH} not found.")
        return 0

    df = pd.read_csv(CSV_PATH)
    
    # --- AUTO-INJECT BEN EP PICKS ---
    try:
        con = sqlite3.connect(DB_PATH)
        ep_picks = pd.read_sql_query("SELECT * FROM bens_ep_selections WHERE race_date=?", con, params=(date_str,))
        con.close()
        
        if not ep_picks.empty:
            new_rows = []
            for _, r in ep_picks.iterrows():
                # Check if already in df
                mask = (df["race_date"] == date_str) & (df["system_name"] == "Ben EP") & (df["horse_name"] == r["horse"])
                if not mask.any():
                    new_rows.append({
                        "race_date": date_str,
                        "system_name": "Ben EP",
                        "sub_system": "Extra Place System",
                        "course": r["course"],
                        "race_time": r["race_time"],
                        "horse_name": r["horse"],
                        "early_odds": r["odds"],
                        "best_bookmaker": r.get("bookmaker", "Oddschecker"),
                        "sp_odds": None,
                        "sp_text": "-",
                        "finish_pos": "⏳ Running Today",
                        "won": 0,
                        "placed": 0,
                        "places_paid": int(r.get("extra_places", 4)),
                        "early_win_pl": None,
                        "sp_win_pl": None,
                        "early_ew_pl": None,
                        "sp_ew_pl": None,
                        "bf_odds": None,
                        "early_place_odds": r.get("place_return"),
                        "bf_place_odds": None
                    })
            if new_rows:
                df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
                df.to_csv(CSV_PATH, index=False)
                print(f"Injected {len(new_rows)} new Ben EP picks into the ledger for {date_str}.")
    except Exception as e:
        print(f"Notice: Failed to inject Ben EP picks: {e}")
    # --------------------------------
    day_mask = df["race_date"] == date_str
    if not day_mask.any():
        print(f"No selections found in ledger for {date_str}.")
        return 0

    print(f"Settling selections for {date_str} ({day_mask.sum()} total rows)...")

    # Fetch outcomes from available sources
    scraped = fetch_scraped_results(date_str)
    bf_settled = fetch_betfair_settled(date_str)

    settled_count = 0
    for idx in df[day_mask].index:
        row = df.loc[idx]
        cur_pos = str(row.get("finish_pos") or "")
        # Skip already-finalised rows unless the caller wants everything re-scraped. Both
        # sources are re-read on every run, and a row whose scrape comes back empty is left
        # as it was, so forcing cannot wipe a settled result.
        if not force and cur_pos not in ("⏳ Running Today", "Pending", "-", "nan", "None", ""):
            continue

        h_name = str(row["horse_name"])
        h_c = strip_country(h_name)
        crs = str(row.get("course") or "").lower()
        key = f"{h_c}_{crs[:4]}"

        res_info = scraped.get(key)
        bf_info = bf_settled.get(h_c)
        bf_bsp = clean_price(bf_info.get("bsp")) if bf_info else None

        if not res_info and not bf_info:
            continue

        pos_str = ""
        sp_dec = None
        sp_txt = "-"

        if res_info:
            pos_str = res_info["pos"]
            sp_txt = res_info["sp_text"]
            sp_dec = clean_price(res_info["sp_odds"])

        # Check Betfair settlement if pos not found yet
        if not pos_str and bf_info:
            if bf_info.get("win_status") == "WINNER":
                pos_str = "1st"
            elif bf_info.get("place_status") == "WINNER":
                pos_str = "Placed"
            elif bf_info.get("win_status") == "LOSER":
                pos_str = "Unplaced"

            if bf_bsp and not sp_dec:
                sp_dec = bf_bsp
                sp_txt = f"{bf_bsp:.2f} (BSP)"

        if not pos_str:
            continue

        # Evaluate Won and Placed
        places_paid = int(row.get("places_paid") or 3)
        fraction = 0.25 if places_paid >= 4 else 0.20

        is_nr = pos_str.upper() in ("NR", "NON-RUNNER", "VOID") or "void" in pos_str.lower()
        if is_nr:
            f_pos = "NR (Void)"
            won = 0
            placed = 0
            e_win_pl = 0.0
            sp_win_pl = 0.0
            e_ew_pl = 0.0
            sp_ew_pl = 0.0
        else:
            f_pos = pos_str
            won = 1 if str(pos_str) in ("1", "1st") else 0

            # Placed logic
            is_numeric_pos = str(pos_str).replace("st", "").replace("nd", "").replace("rd", "").replace("th", "").isdigit()
            if won == 1 or pos_str == "Placed":
                placed = 1
            elif is_numeric_pos:
                placed = 1 if int(str(pos_str).replace("st", "").replace("nd", "").replace("rd", "").replace("th", "")) <= places_paid else 0
            else:
                placed = 0

            # Early Win P&L
            e_odds = clean_price(row.get("early_odds")) or sp_dec or 1.0
            e_pl_odds = clean_price(row.get("early_place_odds")) or round(1.0 + (e_odds - 1.0) * fraction, 2)

            # SP odds fallback
            s_odds = sp_dec if sp_dec and sp_dec > 1.0 else e_odds
            s_pl_odds = round(1.0 + (s_odds - 1.0) * fraction, 2)

            # Win-only P&L (£1 stake)
            e_win_pl = round(e_odds - 1.0, 2) if won == 1 else -1.0
            sp_win_pl = round(s_odds - 1.0, 2) if won == 1 else -1.0

            # Each-Way P&L (£1 win + £1 place = £2 stake)
            if won == 1:
                e_ew_pl = round((e_odds - 1.0) + (e_pl_odds - 1.0), 2)
                sp_ew_pl = round((s_odds - 1.0) + (s_pl_odds - 1.0), 2)
            elif placed == 1:
                e_ew_pl = round((e_pl_odds - 1.0) - 1.0, 2)
                sp_ew_pl = round((s_pl_odds - 1.0) - 1.0, 2)
            else:
                e_ew_pl = -2.0
                sp_ew_pl = -2.0

        # Update dataframe
        df.at[idx, "finish_pos"] = f_pos
        df.at[idx, "won"] = won
        df.at[idx, "placed"] = placed
        df.at[idx, "sp_odds"] = sp_dec
        df.at[idx, "sp_text"] = sp_txt
        df.at[idx, "price_flag"] = "sp/bsp divergent" if has_diverged(sp_dec, bf_bsp) else ""
        df.at[idx, "early_win_pl"] = e_win_pl
        df.at[idx, "sp_win_pl"] = sp_win_pl
        df.at[idx, "early_ew_pl"] = e_ew_pl
        df.at[idx, "sp_ew_pl"] = sp_ew_pl
        settled_count += 1

    # Range-check every price before it is written, so an outlier cannot reach a verdict.
    for column in ("early_odds", "early_place_odds", "bf_odds", "bf_place_odds", "sp_odds"):
        if column in df.columns:
            before = pd.to_numeric(df[column], errors="coerce").notna().sum()
            df[column] = df[column].map(clean_price)
            dropped = before - df[column].notna().sum()
            if dropped:
                print(f"  price check: dropped {dropped} out-of-range value(s) in {column}")

    # Save CSV
    df.to_csv(CSV_PATH, index=False)
    # Save SQLite
    conn = sqlite3.connect(DB_PATH)
    df.to_sql("system_results_ledger", conn, if_exists="replace", index=False)
    conn.close()

    print(f"Settlement complete: updated {settled_count} runner outcomes for {date_str}.")
    return settled_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily System Results & Settlement Engine")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="YYYY-MM-DD")
    args = parser.parse_args()
    settle_ledger(args.date)


if __name__ == "__main__":
    main()
