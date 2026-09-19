import sys
import subprocess
import calendar

def launch_12_separate_months(year=2023):
    print("="*85)
    print(f"  LAUNCHING 12 SEPARATE INDEPENDENT MONTH PROCESSES FOR YEAR {year}")
    print("="*85)
    
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
        
        print(f"  --> Launching Month {month:02d} process: {start_str} to {end_str}...")
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
        
    print("\n12 separate month processes launched successfully in independent consoles!")
    print("="*85)

if __name__ == "__main__":
    y = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    launch_12_separate_months(y)
