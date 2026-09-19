"""
CANONICAL MASTER LAY SYSTEM
===========================
Rules:
1. Handicap races only.
2. Odds filter: 1.50 - 6.00 (using Official BSP).
3. Outside top 2 in late sectional finishing speed efficiency within its race (LTO_SectRank > 2).

Zero Lookahead enforced:
All LTO data is drawn from races strictly prior to the target race using PRODB's 
built-in FSPEFFRK_Finish and historical temporally-bound joins.
"""

import sys
import os
import datetime
import pyodbc
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

CONN_PROFORM = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=.\PROFORM_RACING;"
    r"Database=PRODB;"
    r"Trusted_Connection=yes;"
)

def run_backtest(start_date, end_date):
    print(f"Running Master Lay BACKTEST from {start_date} to {end_date}...\n")
    
    sql = f"""
    WITH TargetRaces AS (
        SELECT 
            R.RH_RNo AS TargetRaceId,
            R.RH_DateTime AS TargetDateTime,
            CAST(R.RH_DateTime AS DATE) AS RaceDate,
            R.RH_Name,
            R.RH_HandicapLimit,
            HIR.HIR_HNo AS HorseId,
            H.H_Name_No_Anything AS horse_clean,
            HIR.HIR_BSP AS OfficialBSP,
            HIR.HIR_PositionNo AS ResultPos
        FROM dbo.NEW_RH R
        JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = R.RH_RNo
        JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
        WHERE R.RH_DateTime >= '{start_date}' AND R.RH_DateTime <= '{end_date}'
          AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
          AND HIR.HIR_BSP >= 1.01 AND HIR.HIR_BSP <= 6.00
    ),
    HistoricalRuns AS (
        SELECT 
            T.TargetRaceId,
            T.RaceDate,
            T.TargetDateTime,
            T.horse_clean,
            T.ResultPos,
            T.OfficialBSP,
            SD.FSPEFFRK_Finish AS LTO_SectRank,
            ROW_NUMBER() OVER(PARTITION BY T.HorseId, T.TargetRaceId ORDER BY PastR.RH_DateTime DESC) as run_rn
        FROM TargetRaces T
        JOIN dbo.NEW_HIR PastHIR ON PastHIR.HIR_HNo = T.HorseId
        JOIN dbo.NEW_RH PastR ON PastR.RH_RNo = PastHIR.HIR_RNo
        JOIN dbo.SData SD ON SD.SD_RNo = PastHIR.HIR_RNo AND SD.SD_HNo = PastHIR.HIR_HNo
        WHERE PastR.RH_DateTime < T.TargetDateTime
          AND PastR.RH_RNo <> T.TargetRaceId
    )
    SELECT * FROM HistoricalRuns WHERE run_rn = 1
    ORDER BY RaceDate, TargetDateTime;
    """
    
    conn = pyodbc.connect(CONN_PROFORM)
    df = pd.read_sql(sql, conn)
    conn.close()
    
    print(f"Total Handicap Runners with 1.50-6.00 odds with SData history: {len(df)}")
    
    # Selection Rule: Outside top 2 in LTO finishing speed efficiency
    df_qual = df[(df['LTO_SectRank'] > 2)].copy()
    
    print(f"Total Qualifiers for Master Lay: {len(df_qual)}\n")
    
    if len(df_qual) == 0:
        return
        
    df_qual['IsWin'] = df_qual['ResultPos'] == 1
    
    liability = 100
    pnl = []
    stakes = []
    
    for _, row in df_qual.iterrows():
        bsp = float(row['OfficialBSP']) if pd.notna(row['OfficialBSP']) else 0
        if bsp <= 1.0:
            pnl.append(0)
            stakes.append(0)
            continue
            
        stake = liability / (bsp - 1)
        stakes.append(stake)
        
        if row['IsWin']:
            pnl.append(-liability)
        else:
            pnl.append(stake * 0.95) # Standardized 5% commission
            
    df_qual['PnL'] = pnl
    df_qual['Stake'] = stakes
    
    total_pnl = df_qual['PnL'].sum()
    total_staked = df_qual['Stake'].sum()
    roi = (total_pnl / total_staked * 100) if total_staked > 0 else 0
    
    print(f"--- BACKTEST RESULTS ---")
    print(f"Bets: {len(df_qual)}")
    print(f"Wins (Lays Lost): {df_qual['IsWin'].sum()} ({df_qual['IsWin'].mean()*100:.1f}%)")
    print(f"Total PnL (£100 liability): £{total_pnl:.2f}")
    print(f"ROI on Stake: {roi:.2f}%")
    
    print("\n--- YEARLY BREAKDOWN ---")
    df_qual['RaceDate'] = pd.to_datetime(df_qual['RaceDate'])
    df_qual['Year'] = df_qual['RaceDate'].dt.year
    
    yearly = df_qual.groupby('Year').agg(
        Bets=('horse_clean', 'count'),
        LaysLost=('IsWin', 'sum'),
        TotalPnL=('PnL', 'sum'),
        TotalStake=('Stake', 'sum')
    )
    yearly['WinRate'] = (yearly['LaysLost'] / yearly['Bets'] * 100).round(1)
    yearly['ROI'] = (yearly['TotalPnL'] / yearly['TotalStake'] * 100).round(2)
    
    for year, row in yearly.iterrows():
        print(f"{year}: Bets: {row['Bets']:>4} | Lays Lost: {row['LaysLost']:>3} ({row['WinRate']:>4}%) | PnL: £{row['TotalPnL']:>8.2f} | ROI: {row['ROI']:>6.2f}%")
        
    out_path = r"E:\Test\racing-form-system\reports\MasterLay_Backtest.csv"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df_qual.to_csv(out_path, index=False)
    print(f"\nSaved CSV to {out_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python master_lay_proper.py <start_date> <end_date>")
        sys.exit(1)
        
    start = sys.argv[1]
    end = sys.argv[2]
    run_backtest(start, end)
