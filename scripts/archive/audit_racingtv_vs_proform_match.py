import pyodbc
import pandas as pd
import re

def clean_name(s):
    if not s or pd.isna(s):
        return ""
    s = str(s).upper()
    s = re.sub(r"\([^)]*\)", "", s) # remove country codes like (IRE), (FR), (GB)
    s = re.sub(r"[^A-Z0-9]", "", s) # keep alphanumeric only
    return s.strip()

def clean_course(c):
    if not c or pd.isna(c):
        return ""
    c = str(c).upper()
    c = re.sub(r"[^A-Z0-9]", "", c)
    return c.strip()

def run_match_audit():
    print("="*85)
    print("  EXACT MATCH AUDIT: RACING TV SCRAPED DATA vs PROFORM EXCEL MASTER (profroms.csv)")
    print("="*85)

    conn_rtv = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\\MSSQLLocalDB;Database=RACINGTV_2023_2026;Trusted_Connection=yes;")
    sql_rtv = """
    SELECT 
        RaceDate,
        CourseName,
        RaceTime,
        HorseName,
        PosNo,
        SP
    FROM dbo.Scraped_Results
    """
    df_rtv = pd.read_sql(sql_rtv, conn_rtv)
    conn_rtv.close()
    
    print(f"  • Total Scraped Racing TV Rows Loaded: {len(df_rtv):,}")

    csv_path = r"E:\Profromdbs\profroms.csv"
    print(f"  • Reading Proform Master CSV: {csv_path}...")
    
    # Read relevant columns from profroms.csv
    cols_to_read = ['Date', 'Course', 'Horse', 'Pos', 'BSP', 'ASLRK', 'Pace']
    df_pro = pd.read_csv(csv_path, usecols=lambda c: c in cols_to_read or c in ['RaceDate', 'CourseName', 'HorseName'], low_memory=False)
    
    # Standardize column names
    col_map = {'Date': 'RaceDate', 'Course': 'CourseName', 'Horse': 'HorseName', 'Pos': 'PosNo'}
    df_pro.rename(columns=col_map, inplace=True)

    print(f"  • Total Proform Master Rows Loaded: {len(df_pro):,}")

    # Standardize Keys
    df_rtv['Date_Str'] = pd.to_datetime(df_rtv['RaceDate']).dt.strftime('%Y-%m-%d')
    df_rtv['Clean_Course'] = df_rtv['CourseName'].apply(clean_course)
    df_rtv['Clean_Horse'] = df_rtv['HorseName'].apply(clean_name)

    df_pro['Date_Str'] = pd.to_datetime(df_pro['Date']).dt.strftime('%Y-%m-%d')
    df_pro['Clean_Course'] = df_pro['Course'].apply(clean_course)
    df_pro['Clean_Horse'] = df_pro['Horse'].apply(clean_name)

    # Perform Inner Join on Date + Course + HorseName
    merged = pd.merge(
        df_rtv,
        df_pro,
        on=['Date_Str', 'Clean_Course', 'Clean_Horse'],
        how='inner',
        suffixes=('_RTV', '_PRO')
    )

    total_scraped = len(df_rtv)
    total_matched = len(merged)
    match_pct = (total_matched / total_scraped * 100) if total_scraped > 0 else 0

    print("\n" + "="*85)
    print(f"  EXACT MATCH AUDIT RESULTS:")
    print("="*85)
    print(f"  • Scraped Racing TV Runners Analyzed:  {total_scraped:,}")
    print(f"  • Successfully Matched with Proform:    {total_matched:,}")
    print(f"  • OVERALL MATCH ACCURACY RATE:        {match_pct:.2f}%")
    print("="*85)

    if not merged.empty:
        print("\n  SAMPLE MATCHED ROWS (First 5):")
        cols_show = [c for c in ['Date_Str', 'Clean_Course', 'HorseName_RTV', 'PosNo_RTV', 'PosNo_PRO', 'BSP'] if c in merged.columns]
        print(merged[cols_show].head().to_string(index=False))
        print("="*85)

if __name__ == "__main__":
    run_match_audit()
