import sys
import subprocess
import calendar
import datetime

def launch_year(year):
    print("="*85)
    print(f"  LAUNCHING 12 PARALLEL MONTHLY WORKERS FOR YEAR {year}")
    print("="*85)
    
    processes = []
    for month in range(1, 13):
        _, last_day = calendar.monthrange(year, month)
        start_str = f"{year}-{month:02d}-01"
        end_str = f"{year}-{month:02d}-{last_day:02d}"
        
        cmd = [
            sys.executable,
            r"E:\Test\racing-form-system\scripts\racingtv_db_updater.py",
            start_str,
            end_str
        ]
        
        print(f"  [Worker {month:02d}] Scraping {start_str} to {end_str}...")
        p = subprocess.Popen(cmd)
        processes.append((month, p))
        
    print("\nAll 12 monthly workers running concurrently!")
    print("="*85)

if __name__ == "__main__":
    target_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    launch_year(target_year)
