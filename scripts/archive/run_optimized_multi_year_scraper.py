import sys
import subprocess
import calendar
import datetime

def launch_optimized():
    print("="*85)
    print("  OPTIMIZED LIGHTWEIGHT MULTI-YEAR SCRAPER (2023 - 2026)")
    print("  Uses Smooth Memory Management to Prevent Laptop Slowdown / Playwright Freezes")
    print("="*85)

    today = datetime.date.today()
    years = [2023, 2024, 2025, 2026]
    
    processes = []
    for yr in years:
        max_m = today.month if yr == today.year else 12
        for m in range(1, max_m + 1):
            _, last_d = calendar.monthrange(yr, m)
            start_s = f"{yr}-{m:02d}-01"
            if yr == today.year and m == today.month:
                end_s = today.strftime("%Y-%m-%d")
            else:
                end_s = f"{yr}-{m:02d}-{last_d:02d}"
                
            cmd = [
                sys.executable,
                r"E:\Test\racing-form-system\scripts\racingtv_db_updater.py",
                start_s,
                end_s
            ]
            
            p = subprocess.Popen(cmd)
            processes.append((f"{yr}-{m:02d}", p))

if __name__ == "__main__":
    launch_optimized()
