import sys
import subprocess
import calendar
import datetime

DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000

def launch_persistent():
    print("="*85)
    print("  LAUNCHING PERMANENT PERSISTENT BACKGROUND SCRAPERS (2023 - 2026)")
    print("  (Configured with DETACHED_PROCESS so Windows keeps them running 24/7)")
    print("="*85)

    today = datetime.date.today()
    years = [2023, 2024, 2025, 2026]
    
    count = 0
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
            
            subprocess.Popen(
                cmd,
                creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
                close_fds=True
            )
            count += 1
            print(f"  [+] Started Persistent Worker {yr}-{m:02d}: {start_s} to {end_s}")
            
    print("\n" + "="*85)
    print(f"  SUCCESS: {count} PERSISTENT MONTH WORKERS LAUNCHED IN DETACHED WINDOWS PROCESSES!")
    print("="*85)

if __name__ == "__main__":
    launch_persistent()
