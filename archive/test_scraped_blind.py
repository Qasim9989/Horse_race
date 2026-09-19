import pandas as pd
import pyodbc
import warnings

warnings.filterwarnings("ignore")

def connection_string(database):
    return (
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=(localdb)\\MSSQLLocalDB;"
        f"Database={database};"
        "Trusted_Connection=yes;"
    )

def connect(database):
    return pyodbc.connect(connection_string(database), timeout=10)

def clean_alphanumeric(text):
    if not text or pd.isna(text):
        return ""
    return "".join(c for c in str(text).upper() if c.isalnum())

def load_data():
    print("Loading RacingTV data...")
    sql_rtv = """
    SELECT
        CAST(RaceDate AS DATE) AS RaceDate,
        RaceTime,
        CourseName,
        RaceTitle,
        HorseName,
        PosNo
    FROM dbo.Scraped_Results
    WHERE RaceTitle LIKE '%Handicap%'
      AND RaceDate >= '2023-01-01'
    """
    with connect("RACINGTV_2023_2026") as conn:
        rtv = pd.read_sql(sql_rtv, conn)
        
    print("Loading Proform data...")
    # Get exact Proform data restricted to the same time period and 1.50-6.00 range
    sql_pro = """
    SELECT
        CAST(RH.RH_DateTime AS DATE) AS RaceDate,
        FORMAT(RH.RH_DateTime, 'HHmm') AS RaceTime,
        C.C_Name AS CourseName,
        H.H_Name_No_Anything AS HorseName,
        RH.RH_RNo AS ProformRNo,
        HIR.HIR_HNo AS ProformHNo,
        HIR.HIR_PositionNo AS ProformPositionNo,
        HIR.HIR_BSP AS ProformBSP
    FROM dbo.NEW_RH RH
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
    JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
    JOIN dbo.NEW_C C ON C.C_ID = RH.RH_CNo
    WHERE CAST(RH.RH_DateTime AS DATE) >= '2023-01-01'
      AND HIR.HIR_BSP >= 1.01
    """
    with connect("PRODB") as conn:
        pro = pd.read_sql(sql_pro, conn)
        
    # Standardize match keys (Course, Time, Horse)
    print("Applying standardization keys...")
    rtv['CleanCourse'] = rtv['CourseName'].apply(clean_alphanumeric)
    rtv['CleanTime'] = rtv['RaceTime'].apply(clean_alphanumeric)
    rtv['CleanHorse'] = rtv['HorseName'].apply(clean_alphanumeric)
    
    pro['CleanCourse'] = pro['CourseName'].apply(clean_alphanumeric)
    pro['CleanTime'] = pro['RaceTime'].apply(clean_alphanumeric)
    pro['CleanHorse'] = pro['HorseName'].apply(clean_alphanumeric)
    
    # Dates
    rtv['RaceDate'] = pd.to_datetime(rtv['RaceDate']).dt.date
    pro['RaceDate'] = pd.to_datetime(pro['RaceDate']).dt.date
    
    return rtv, pro

def main():
    rtv, pro = load_data()
    
    # Merge strictly on Date + Course + Time + Horse
    print("Merging datasets on Date + Course + Time + Horse...")
    merged = pd.merge(
        rtv, 
        pro, 
        on=['RaceDate', 'CleanCourse', 'CleanTime', 'CleanHorse'], 
        how='inner',
        suffixes=('_rtv', '_pro')
    )
    
    print(f"Total strict matched handicap runs: {len(merged)}")
    
    # Filter to 1.50 - 6.00 BSP
    qualifiers = merged[(merged['ProformBSP'] >= 1.50) & (merged['ProformBSP'] <= 6.00)].copy()
    print(f"Total qualifiers (BSP 1.50-6.00): {len(qualifiers)}")
    
    # Settle bets
    # Fixed liability of £100
    liability = 100.0
    
    def calculate_pl(row):
        # Position 1 means the horse won (so the lay bet loses)
        is_winner = str(row['ProformPositionNo']) == '1' or str(row['PosNo']).startswith('1')
        if is_winner:
            return -liability
        else:
            # We lay £(liability / (BSP - 1)), and win that stake back minus 5% commission
            stake = liability / (row['ProformBSP'] - 1)
            return stake * 0.95
            
    qualifiers['PnL'] = qualifiers.apply(calculate_pl, axis=1)
    qualifiers['IsWinner'] = qualifiers['PnL'] < 0
    
    total_bets = len(qualifiers)
    if total_bets == 0:
        print("No qualifiers found!")
        return

    total_pl = qualifiers['PnL'].sum()
    total_liability = total_bets * liability
    roi = (total_pl / total_liability) * 100 if total_liability > 0 else 0
    wins = qualifiers['IsWinner'].sum()
    win_strike_rate = (wins / total_bets) * 100 if total_bets > 0 else 0
    
    print("\n--- RESULTS FOR STRICT MATCHED BLIND LAY SYSTEM ---")
    print(f"Total Bets: {total_bets:,}")
    print(f"Wins (Horse won, lay lost): {wins:,}")
    print(f"Horse Win Strike Rate: {win_strike_rate:.2f}%")
    print(f"Total P&L (£100 fixed liability): £{total_pl:,.2f}")
    print(f"ROI on Liability: {roi:.2f}%")
    
    # Group by year
    qualifiers['Year'] = pd.to_datetime(qualifiers['RaceDate']).dt.year
    yearly = qualifiers.groupby('Year').agg(
        Bets=('CleanHorse', 'count'),
        PnL=('PnL', 'sum')
    )
    yearly['ROI'] = (yearly['PnL'] / (yearly['Bets'] * liability)) * 100
    print("\nYearly Breakdown:")
    print(yearly.to_string())

if __name__ == "__main__":
    main()
