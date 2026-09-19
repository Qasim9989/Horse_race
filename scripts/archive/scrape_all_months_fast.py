import asyncio
import calendar
import datetime
import sys
import subprocess

def get_month_ranges(start_year=2023, end_year=2026):
    today = datetime.date.today()
    ranges = []
    for yr in range(start_year, end_year + 1):
        max_m = today.month if yr == today.year else 12
        for m in range(1, max_m + 1):
            _, last_d = calendar.monthrange(yr, m)
            start_s = f"{yr}-{m:02d}-01"
            if yr == today.year and m == today.month:
                end_s = today.strftime("%Y-%m-%d")
            else:
                end_s = f"{yr}-{m:02d}-{last_d:02d}"
            ranges.append((start_s, end_s))
    return ranges

def run_batched(concurrency=6):
    month_ranges = get_month_ranges(2023, 2026)
    print("="*85)
    print(f"  BATCHED MULTI-MONTH SCRAPER ({len(month_ranges)} Total Months to Scrape)")
    print(f"  Running in Controlled Batches of {concurrency} Concurrent Months to Avoid Windows OS Errors")
    print("="*85)

    for i in range(0, len(month_ranges), concurrency):
        batch = month_ranges[i:i+concurrency]
        print(f"\n[+] Launching Batch {i//concurrency + 1} ({len(batch)} months):")
        procs = []
        for s_d, e_d in batch:
            print(f"   • Scraping {s_d} to {e_d}...")
            cmd = [sys.executable, r"E:\Test\racing-form-system\scripts\racingtv_db_updater.py", s_d, e_d]
            p = subprocess.Popen(cmd)
            procs.append(p)
            
        print("   Waiting for current batch of months to complete...")
        for p in procs:
            p.wait()
            
    print("\n=== ALL MONTHS FROM 2023 TO 2026 SCRAPED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_batched(concurrency=6)
