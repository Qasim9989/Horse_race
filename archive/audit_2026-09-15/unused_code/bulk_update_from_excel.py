import pandas as pd
import pyodbc
import datetime

def clean_name(s):
    if not isinstance(s, str):
        return ""
    import re
    s = re.sub(r'\(.*?\)', '', str(s))
    s = s.replace(" ", "").replace("'", "").replace(".", "").replace("-", "")
    return s.lower()

def main():
    conn = pyodbc.connect('Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\\MSSQLLocalDB;Database=SCRAPED_PRODB;Trusted_Connection=yes;')
    
    print("Loading Excel file...")
    df_excel = pd.read_excel(r'E:\Test\racing-form-system\may-aug26.xlsx', usecols=['Date of Race', 'Horse', 'Betfair SP'])
    df_excel['RaceDate'] = pd.to_datetime(df_excel['Date of Race']).dt.date
    df_excel['CleanHorse'] = df_excel['Horse'].apply(clean_name)
    df_excel['BSP'] = pd.to_numeric(df_excel['Betfair SP'], errors='coerce')
    df_excel = df_excel.dropna(subset=['BSP', 'RaceDate', 'CleanHorse'])
    
    print("Loading DB...")
    df_db = pd.read_sql("SELECT RaceDate, CourseName, RaceTime, HorseName FROM Scraped_Results WHERE RaceDate >= '2026-05-01'", conn)
    df_db['RaceDate_Clean'] = pd.to_datetime(df_db['RaceDate']).dt.date
    df_db['CleanHorse'] = df_db['HorseName'].apply(clean_name)
    
    print("Merging...")
    merged = pd.merge(df_db, df_excel[['RaceDate', 'CleanHorse', 'BSP']], left_on=['RaceDate_Clean', 'CleanHorse'], right_on=['RaceDate', 'CleanHorse'], how='inner')
    
    # Drop duplicates in case a horse ran twice in one day
    merged = merged.drop_duplicates(subset=['RaceDate_Clean', 'CourseName', 'RaceTime', 'HorseName'])
    
    print("Updating database instantly using fast_executemany...")
    update_data = [
        (row['BSP'], row['RaceDate_Clean'].strftime("%Y-%m-%d"), row['CourseName'], row['RaceTime'], row['HorseName'])
        for _, row in merged.iterrows()
    ]
    
    cursor = conn.cursor()
    cursor.fast_executemany = True
    cursor.executemany("UPDATE Scraped_Results SET BSP = ? WHERE RaceDate = ? AND CourseName = ? AND RaceTime = ? AND HorseName = ?", update_data)
    
    conn.commit()
    conn.close()
    print(f"Successfully backfilled {len(update_data)} BSPs!")

if __name__ == "__main__":
    main()
