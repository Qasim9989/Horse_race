"""
BETFAIR OFFICIAL DAILY BSP AUTO-IMPORTER
=========================================
Downloads the official daily Betfair Starting Price (BSP) CSV feeds directly from Betfair
(promo.betfair.com) with ZERO login, zero paywalls, and 100% official exchange precision.

Updates SCRAPED_PRODB.dbo.Scraped_Results with:
- Official Win BSP
- Official In-Running Min / Max Odds (ipmin / ipmax)
"""

import sys
import os
import re
import datetime
import pyodbc
import pandas as pd
import urllib.request
import warnings
warnings.filterwarnings('ignore')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

CONN_S = r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;Database=RACINGTV_2023_2026;Trusted_Connection=yes;"

def clean_horse_name(name):
    if not name: return ""
    name = re.sub(r"^\d+\.\s*", "", str(name))
    name = re.sub(r"\s*\([A-Z]{2,4}\)$", "", str(name), flags=re.IGNORECASE).strip()
    return "".join(c for c in name.lower() if c.isalnum())

def import_betfair_bsp_for_date(target_date: datetime.date):
    # Betfair date format in URL is DDMMYYYY (e.g. 18082026 for 18-Aug-2026)
    # The file for a day's racing is usually named with the next calendar date or current date
    # We try both DDMMYYYY for target_date and target_date + 1 day
    
    date_strs = [
        target_date.strftime("%d%m%Y"),
        (target_date + datetime.timedelta(days=1)).strftime("%d%m%Y")
    ]
    
    df_bsp = None
    success_url = None
    
    for d_str in date_strs:
        url = f"https://promo.betfair.com/betfairsp/prices/dwbfpricesukwin{d_str}.csv"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                df = pd.read_csv(resp)
                if not df.empty and 'bsp' in df.columns:
                    # Filter for rows matching our target date
                    # event_dt format: "18-08-2026 14:45"
                    t_str_check = target_date.strftime("%d-%m-%Y")
                    matching = df[df['event_dt'].astype(str).str.contains(t_str_check)]
                    if not matching.empty:
                        df_bsp = matching
                        success_url = url
                        break
                    elif df_bsp is None:
                        df_bsp = df
                        success_url = url
        except Exception as e:
            continue
            
    if df_bsp is None or df_bsp.empty:
        print(f"⚠ Could not fetch Betfair BSP feed for date {target_date.strftime('%Y-%m-%d')}")
        return 0

    print(f"✓ Loaded {len(df_bsp)} official Betfair BSP records from {success_url}")
    df_bsp['clean_horse'] = df_bsp['selection_name'].apply(clean_horse_name)

    # Update database
    conn = pyodbc.connect(CONN_S)
    cur = conn.cursor()

    # Ensure BSP and in-play columns exist
    try:
        cur.execute("IF COL_LENGTH('dbo.Scraped_Results', 'BSP') IS NULL ALTER TABLE dbo.Scraped_Results ADD BSP FLOAT NULL")
        cur.execute("IF COL_LENGTH('dbo.Scraped_Results', 'IPMin') IS NULL ALTER TABLE dbo.Scraped_Results ADD IPMin FLOAT NULL")
        cur.execute("IF COL_LENGTH('dbo.Scraped_Results', 'IPMax') IS NULL ALTER TABLE dbo.Scraped_Results ADD IPMax FLOAT NULL")
        conn.commit()
    except:
        pass

    target_date_str = target_date.strftime("%Y-%m-%d")
    updated = 0

    for _, row in df_bsp.iterrows():
        h_clean = row['clean_horse']
        bsp_val = float(row['bsp']) if pd.notna(row['bsp']) else None
        ipmin_val = float(row['ipmin']) if pd.notna(row['ipmin']) else None
        ipmax_val = float(row['ipmax']) if pd.notna(row['ipmax']) else None

        if bsp_val and h_clean:
            cur.execute("""
            UPDATE dbo.Scraped_Results
            SET BSP = ?, IPMin = ?, IPMax = ?
            WHERE RaceDate = ? AND LOWER(REPLACE(REPLACE(HorseName, ' ', ''), '''', '')) LIKE ?
            """, (bsp_val, ipmin_val, ipmax_val, target_date_str, f"%{h_clean}%"))
            updated += cur.rowcount

    conn.commit()
    conn.close()
    print(f"✓ Updated {updated} horses in SCRAPED_PRODB.dbo.Scraped_Results with official Betfair BSP & in-running odds.")
    return updated

if __name__ == "__main__":
    t_date = datetime.date.today() - datetime.timedelta(days=1)
    if len(sys.argv) > 1:
        try:
            t_date = datetime.datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except:
            pass
    print("=" * 70)
    print(f"  BETFAIR OFFICIAL BSP IMPORTER FOR: {t_date}")
    print("=" * 70)
    import_betfair_bsp_for_date(t_date)
