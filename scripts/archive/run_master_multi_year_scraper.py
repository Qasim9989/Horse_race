import sys
import subprocess
import calendar
import datetime

def launch_all_years(start_year=2023, end_year=2026):
    print("="*85)
    print(f"  LAUNCHING MULTI-YEAR CONCURRENT SCRAPERS ({start_year} TO {end_year})")
    print("="*85)
    
    today = datetime.date.today()
    processes = []
    
    for year in range(start_year, end_year + 1):
        max_month = today.month if year == today.year else 12
        for month in range(1, max_month + 1):
            _, last_day = calendar.monthrange(year, month)
            start_str = f"{year}-{month:02d}-01"
            
            # If current month, cap at today
            if year == today.year and month == today.month:
                end_str = today.strftime("%Y-%m-%d")
            else:
                end_str = f"{year}-{month:02d}-{last_day:02d}"
                
            cmd = [
                sys.executable,
                r"E:\Test\racing-form-system\scripts\racingtv_db_updater.py",
                start_str,
                end_str
            ]
            
            print(f"  [Worker {year}-{month:02d}] Scraping {start_str} to {end_str}...")
            p = subprocess.Popen(cmd)
            processes.append((f"{year}-{month:02d}", p))
            
    print("\nAll monthly workers across 2023, 2024, 2025, and 2026 running concurrently!")
    print("="*85)

if __name__ == "__main__":
    launch_all_years(2023, 2026)
