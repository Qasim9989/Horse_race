import calendar
import sys
import subprocess

def run_year(year=2025, concurrency=6):
    month_ranges = []
    for m in range(1, 13):
        _, last_d = calendar.monthrange(year, m)
        start_s = f"{year}-{m:02d}-01"
        end_s = f"{year}-{m:02d}-{last_d:02d}"
        month_ranges.append((start_s, end_s))
        
    print("="*85)
    print(f"  CONCURRENT YEAR SCRAPER FOR {year} ({len(month_ranges)} Months)")
    print("="*85)

    for i in range(0, len(month_ranges), concurrency):
        batch = month_ranges[i:i+concurrency]
        print(f"\n[+] Launching {year} Batch {i//concurrency + 1}:")
        procs = []
        for s_d, e_d in batch:
            print(f"   • Scraping {s_d} to {e_d}...")
            cmd = [sys.executable, r"E:\Test\racing-form-system\scripts\racingtv_db_updater.py", s_d, e_d]
            p = subprocess.Popen(cmd)
            procs.append(p)
            
        print(f"   Waiting for {year} batch to complete...")
        for p in procs:
            p.wait()
            
    print(f"\n=== YEAR {year} COMPLETED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_year(2025, concurrency=6)
