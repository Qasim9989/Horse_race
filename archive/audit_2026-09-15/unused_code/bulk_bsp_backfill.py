import sys
import os
import datetime
import pyodbc
import pandas as pd
import urllib.request
import io
import warnings
warnings.filterwarnings('ignore')

def clean_name(s):
    if not isinstance(s, str):
        return ""
    # Remove country codes like (IRE)
    import re
    s = re.sub(r'\(.*?\)', '', str(s))
    # Remove spaces and quotes
    s = s.replace(" ", "").replace("'", "").replace(".", "").replace("-", "")
    return s.lower()

def main():
    conn = pyodbc.connect('Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\\MSSQLLocalDB;Database=SCRAPED_PRODB;Trusted_Connection=yes;')
    
    start_date = datetime.date(2026, 6, 1)
    end_date = datetime.date(2026, 8, 22)
    
    all_bsp = []
    curr = start_date
    while curr <= end_date:
        fetch_date = curr + datetime.timedelta(days=1)
        url = f"https://promo.betfair.com/betfairsp/prices/dwbfpricesukwin{fetch_date.strftime('%d%m%Y')}.csv"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                csv_data = response.read().decode('utf-8')
                df = pd.read_csv(io.StringIO(csv_data))
                df['RaceDate'] = curr
                all_bsp.append(df)
            print(f"Fetched {curr}")
        except Exception as e:
            pass
        curr += datetime.timedelta(days=1)
        
    if not all_bsp:
        print("No BSP data fetched.")
        return
        
    df_bsp = pd.concat(all_bsp, ignore_index=True)
    dates = df_bsp['RaceDate'].copy()
    df_bsp.columns = [c.upper() for c in df_bsp.columns]
    df_bsp['RaceDate'] = dates
    
    df_bsp['CleanHorse'] = df_bsp['SELECTION_NAME'].astype(str).str.replace(r'\(.*?\)', '', regex=True)
    df_bsp['CleanHorse'] = df_bsp['CleanHorse'].str.replace(r'[^a-zA-Z]', '', regex=True).str.lower()
    
    df_bsp['BSP'] = pd.to_numeric(df_bsp['BSP'], errors='coerce')
    df_bsp = df_bsp.dropna(subset=['BSP'])
    
    # Load Scraped_Results
    df_db = pd.read_sql(f"SELECT RaceDate, CourseName, RaceTime, HorseName FROM Scraped_Results WHERE RaceDate >= '2026-06-01'", conn)
    
    df_db['CleanHorse'] = df_db['HorseName'].astype(str).str.replace(r'\(.*?\)', '', regex=True)
    df_db['CleanHorse'] = df_db['CleanHorse'].str.replace(r'[^a-zA-Z]', '', regex=True).str.lower()
    
    df_db['RaceDate'] = pd.to_datetime(df_db['RaceDate']).dt.date
    
    print("BSP head:")
    print(df_bsp[['RaceDate', 'CleanHorse', 'BSP']].head(2))
    print("DB head:")
    print(df_db[['RaceDate', 'CleanHorse']].head(2))
    
    # Merge
    merged = pd.merge(df_db, df_bsp[['RaceDate', 'CleanHorse', 'BSP']], on=['RaceDate', 'CleanHorse'], how='inner')
    print(f"Matched {len(merged)} horses for June-Aug!")
    
    # Bulk update
    cursor = conn.cursor()
    count = 0
    for _, row in merged.iterrows():
        cursor.execute("UPDATE Scraped_Results SET BSP = ? WHERE RaceDate = ? AND CourseName = ? AND RaceTime = ? AND HorseName = ?",
                       (row['BSP'], row['RaceDate'].strftime("%Y-%m-%d"), row['CourseName'], row['RaceTime'], row['HorseName']))
        count += 1
        if count % 1000 == 0:
            print(f"Updated {count}...")
    
    conn.commit()
    conn.close()
    print("Done!")

if __name__ == "__main__":
    main()
