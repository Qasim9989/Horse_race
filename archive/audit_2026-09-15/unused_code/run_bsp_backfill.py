import sys
import datetime
import time

sys.path.append(r'E:\Test\racing-form-system\scripts')
from betfair_daily_bsp_importer import import_betfair_bsp_for_date

def main():
    start_date = datetime.date(2026, 6, 1)
    end_date = datetime.date(2026, 8, 22)
    
    current_date = start_date
    while current_date <= end_date:
        print(f"Backfilling {current_date}...")
        try:
            import_betfair_bsp_for_date(current_date)
        except Exception as e:
            print(f"Failed for {current_date}: {e}")
        current_date += datetime.timedelta(days=1)
        time.sleep(0.5)

if __name__ == "__main__":
    main()
