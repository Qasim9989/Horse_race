"""
FULL AUTOMATED DAILY PIPELINE
==============================
1. Updates SCRAPED_PRODB with latest finished races & sectionals from RacingTV.
2. Scrapes today's live racecards from RacingTV.
3. Cross-references form (DSLR, stride decay, discipline, front-runner pace).
4. Generates today's LAY Sheet (database + formatted Excel report).
5. Generates today's B2L Sheet (back/EW/back-to-lay value finder at 20+ odds).
6. Pre-computes the Streamlit Cloud selection caches (BF odds, Tips, Speed & Stride).
7. Publishes those caches to GitHub so Streamlit Cloud redeploys automatically.
"""

import datetime
import os
import subprocess
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


PROJECT_DIR = r"E:\Test\racing-form-system"
SCRIPTS_DIR = os.path.join(PROJECT_DIR, "scripts")

def run_step(step_name, cmd_args):
    print("\n" + "=" * 70)
    print(f"  [PIPELINE] STEP: {step_name}")
    print("=" * 70)
    res = subprocess.run(cmd_args, cwd=PROJECT_DIR)
    if res.returncode != 0:
        print(f"[WARN] Warning: {step_name} exited with code {res.returncode}")
    else:
        print(f"[OK] {step_name} finished successfully.")

def main():
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "today"
    target_date = datetime.date.today()
    if target in ["tomorrow", "tmr", "next"]:
        target_date += datetime.timedelta(days=1)

    target_str = target_date.strftime("%Y-%m-%d")
    print("=" * 75)
    print("  RACING FORM SYSTEM - MASTER AUTOMATED DAILY PIPELINE")
    print(f"  Target: {target.upper()} ({target_str}) | Executed: {datetime.datetime.now().strftime('%H:%M:%S')}")
    print("=" * 75)

    # Step 1: Update Database with newly finished races (Racing TV)
    run_step(
        "1. Daily Racing TV Database Scraper & Updater",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "racingtv_db_updater.py")]
    )

    # Step 1b: Update Racing Post SQLite database with newly finished races
    rp_script = os.path.join(r"D:\pyton codes", "racing_post_results_checkpoint_scraper.py")
    if os.path.exists(rp_script):
        current_month = target_date.strftime("%Y-%m")
        run_step(
            f"1b. Daily Racing Post Database Scraper & Updater ({current_month})",
            [
                sys.executable,
                "-u",
                rp_script,
                "--month",
                current_month,
                "--repair",
                "--workers",
                "2",
                "--wait-for-site",
            ],
        )

    # Step 1c: Refresh the RaceIQ v2 telemetry (top speed, stride, FSP).  This
    # table is the source of truth for the cloud DB - the v1 table only carries
    # a usable stride.  Re-scraping the last few days is idempotent (delete +
    # insert per race) and repairs any race RacingTV published late.
    v2_dates = []
    for back in range(3, -1, -1):
        d = target_date - datetime.timedelta(days=back)
        if d >= datetime.date.today():
            continue        # sectionals only exist once the race has been run
        v2_dates.append(d.strftime("%Y-%m-%d"))
    if v2_dates:
        run_step(
            f"1c. RaceIQ v2 telemetry refresh ({', '.join(v2_dates)})",
            [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "raceiq_scrape_v2.py"),
             "--workers", "4"]
            + [arg for d in v2_dates for arg in ("--date", d)]
        )

    # Step 2: Backfill real Betfair BSP for the target day
    run_step("2. Backfill real Betfair BSP",
             [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "betfair_bsp_backfill.py"),
              target_str, target_str])

    # Step 3: today's racecard (trip, OR, weights) - the five-rule inputs
    run_step(
        f"3. Today's racecard + five-rule inputs ({target.upper()})",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "bens_racecard.py"), target_str]
    )

    # Step 4: Capture every bookmaker's live price for the target day.
    # Must run BEFORE racing to be useful; running it repeatedly through the
    # day builds the morning -> off-time price history in PRODB.dbo.BookOdds.
    run_step(
        f"4. Book Odds Snapshot - 12 bookmakers ({target.upper()})",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "book_odds.py"),
         "snapshot", target_str]
    )

    # Step 4b: pull in the odds that the GitHub workflows captured - morning,
    # hourly, the T-15..T-1 run-in and BSP - and load them into
    # PRODB.dbo.SnapshotOdds.  The cloud cannot write to LocalDB, so this is the
    # crossing point.  It reads the fetched ref instead of pulling, so a dirty
    # racing_form.db (which this pipeline itself keeps modifying) cannot block it.
    run_step(
        "4b. Import cloud odds snapshots (GitHub -> SQL)",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "import_snapshots_to_sql.py"),
         "--from-git", "--since-days", "30"]
    )

    # Step 5: rebuild the auto price log + report for the target day.  The SP
    # imported in step 2 belongs to the PREVIOUS day (Betfair ships a day's
    # prices in the next day's file), so the price/BSP columns fill in as the
    # days go by; margins and best-price share are available immediately.
    run_step(
        f"5. Price log + book-vs-market report ({target.upper()})",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "book_odds.py"),
         "pricelog", target_str]
    )
    run_step(
        f"6. Book vs Betfair report ({target.upper()})",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "book_odds.py"),
         "report", target_str, "--book", "BEST"]
    )

    run_step(
        f"7. Today's selections - full five-rule system ({target.upper()})",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "selection_today.py"),
         target_str]
    )

    # Step 8: Generate HR Best Times & In-Running Comments Excel report
    run_step(
        f"8. Best Times, Speed & Form Analysis ({target.upper()})",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "best_times_analyzer.py"),
         "--day", target_str, "--excel"]
    )

    # Step 9: Settle previous results into cloud_app/racing_form.db.
    # This syncs RaceIQ telemetry (v2 authoritative, v1 stride fallback) and the
    # results rows.  The system_results_ledger table itself is owned by
    # settle_daily_results.settle_ledger(), which the app calls per date.
    run_step(
        "9. Sync RaceIQ telemetry & results into cloud DB",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "sync_results_ledger.py")]
    )

    # Step 9b: Settle yesterday's picks - results land overnight, so the ledger
    # rows logged yesterday as "Running Today" get their finish positions and P/L.
    yesterday_str = (target_date - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    run_step(
        f"9b. Settle the settlement ledger for {yesterday_str}",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "settle_daily_results.py"),
         "--date", yesterday_str]
    )

    # Step 9c: Refresh the daily system report - one row per day per system and
    # per tip category, so performance is read by day rather than by impression.
    run_step(
        f"9c. Daily system report ({target.upper()})",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "daily_system_report.py"),
         "--days", "30"]
    )

    # Step 10: Pre-compute the Streamlit Cloud selection caches (Betfair win +
    # place odds, Tips picks, Speed & Stride qualifiers).  Runs after the
    # racecard/book-odds steps so the cloud DB holds today's form for the
    # value / weight-drop / stride filters.
    run_step(
        f"10. Pre-compute cloud selection caches ({target.upper()})",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "build_all_selections_cache.py")]
    )

    # Step 10b: Log today's tab picks (Tips, Speed & Stride) into the settlement
    # ledger, so every pick the tabs show is tracked and settled.  Until this
    # existed the tabs showed 168 tips while the ledger held 3.
    run_step(
        f"10b. Log today's tab picks into the settlement ledger ({target.upper()})",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "log_todays_selections.py"),
         "--date", target_str]
    )

    # Step 10c: Log the top two Power_Score cards per race at the early book
    # price - the forward test of "back the first two cards", settled daily.
    run_step(
        f"10c. Log today's top-2 power cards ({target.upper()})",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "log_power_top2.py"),
         "--date", target_str]
    )

    # Step 11: Publish the refreshed caches to GitHub so Streamlit Cloud
    # redeploys itself with today's picks (no manual morning routine needed).
    run_step(
        "11. Publish cloud caches to GitHub",
        [sys.executable, "-u", os.path.join(SCRIPTS_DIR, "publish_cloud_caches.py")]
    )

    print("\n" + "=" * 75)
    print("  ALL DAILY TASKS COMPLETED!")
    print("  Racing TV Database:     RACINGTV_2023_2026.dbo.Scraped_Results")
    print("  Racing Post Database:   D:\\RacingPost_Horse\\racingpost_master.db")
    print("  Book Odds Snapshot:     PRODB.dbo.BookOdds")
    print("  Betfair SP (real):      PRODB.dbo.BFSP")
    print(f"  Today's selections:     reports\\selections_{target_str}.csv")
    print(f"  Best Times Excel:       reports\\best_times_{target_str}.xlsx")
    print("  Cloud caches:           cloud_app\\bf_odds_today.json, tips_today.json,")
    print("                          speed_stride_today.json (published to GitHub)")
    print("=" * 75)

if __name__ == "__main__":
    main()
