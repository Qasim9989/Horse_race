import pyodbc
import pandas as pd

CONN_STR = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=(localdb)\MSSQLLocalDB;"
    r"Database=RACINGTV_2023_2026;"
    r"Trusted_Connection=yes;"
)

def audit_monthly():
    conn = pyodbc.connect(CONN_STR)
    
    sql_results = """
    SELECT 
      FORMAT(RaceDate, 'yyyy-MM') as YearMonth,
      COUNT(DISTINCT RaceDate) as Days_Scraped,
      COUNT(DISTINCT CONCAT(RaceDate, CourseName, RaceTime)) as Races_Scraped,
      COUNT(*) as Runner_Results
    FROM dbo.Scraped_Results
    GROUP BY FORMAT(RaceDate, 'yyyy-MM')
    ORDER BY YearMonth ASC;
    """
    
    sql_raceiq = """
    SELECT 
      FORMAT(RaceDate, 'yyyy-MM') as YearMonth,
      COUNT(*) as RaceIQ_Ranks
    FROM dbo.Scraped_RaceIQ_Ranks
    GROUP BY FORMAT(RaceDate, 'yyyy-MM')
    ORDER BY YearMonth ASC;
    """
    
    df_res = pd.read_sql(sql_results, conn)
    df_rq = pd.read_sql(sql_raceiq, conn)
    conn.close()
    
    print("="*85)
    print("  MONTH-BY-MONTH DATA SCRAPING AUDIT REPORT: [RACINGTV_2023_2026]")
    print("="*85)
    
    if df_res.empty:
        print("Scraper is currently processing the initial dates...")
    else:
        merged = pd.merge(df_res, df_rq, on='YearMonth', how='left').fillna(0)
        merged['RaceIQ_Ranks'] = merged['RaceIQ_Ranks'].astype(int)
        merged['Status'] = merged['Runner_Results'].apply(lambda x: 'OK' if x > 500 else 'Partial')
        print(merged.to_string(index=False))
        
    print("="*85)

if __name__ == "__main__":
    audit_monthly()
