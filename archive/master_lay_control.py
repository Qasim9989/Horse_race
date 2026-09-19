import pyodbc
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

CONN_PROFORM = r'Driver={ODBC Driver 17 for SQL Server};Server=.\PROFORM_RACING;Database=PRODB;Trusted_Connection=yes;'

print('Running Master Lay Control Group Comparison...\n')

# 1. Control Group Query
sql_control = '''
SELECT 
    HIR.HIR_BSP AS OfficialBSP,
    HIR.HIR_PositionNo AS ResultPos
FROM dbo.NEW_RH R
JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = R.RH_RNo
WHERE R.RH_DateTime >= '2023-01-01' AND R.RH_DateTime <= '2026-08-21'
  AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
  AND HIR.HIR_BSP >= 1.01 AND HIR.HIR_BSP <= 6.00
'''

# 2. System Query
sql_system = '''
WITH TargetRaces AS (
    SELECT 
        R.RH_RNo AS TargetRaceId,
        R.RH_DateTime AS TargetDateTime,
        CAST(R.RH_DateTime AS DATE) AS RaceDate,
        HIR.HIR_HNo AS HorseId,
        HIR.HIR_BSP AS OfficialBSP,
        HIR.HIR_PositionNo AS ResultPos
    FROM dbo.NEW_RH R
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = R.RH_RNo
    WHERE R.RH_DateTime >= '2023-01-01' AND R.RH_DateTime <= '2026-08-21'
      AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
      AND HIR.HIR_BSP >= 1.01 AND HIR.HIR_BSP <= 6.00
),
HistoricalRuns AS (
    SELECT 
        T.TargetRaceId,
        T.RaceDate,
        T.TargetDateTime,
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
SELECT * FROM HistoricalRuns WHERE run_rn = 1;
'''

conn = pyodbc.connect(CONN_PROFORM)
df_control = pd.read_sql(sql_control, conn)
df_sys = pd.read_sql(sql_system, conn)
conn.close()

def calc_lay_metrics(df, name):
    df['OfficialBSP'] = pd.to_numeric(df['OfficialBSP'], errors='coerce')
    df['ResultPos'] = pd.to_numeric(df['ResultPos'], errors='coerce')
    df = df.dropna(subset=['OfficialBSP'])
    
    bets = len(df)
    if bets == 0:
        return
        
    df['IsWin'] = df['ResultPos'] == 1
    wins = df['IsWin'].sum()
    
    liability = 100.0
    pnl = []
    stakes = []
    
    for _, row in df.iterrows():
        bsp = float(row['OfficialBSP'])
        if bsp <= 1.0:
            pnl.append(0)
            stakes.append(0)
            continue
            
        stake = liability / (bsp - 1)
        stakes.append(stake)
        
        if row['IsWin']:
            pnl.append(-liability)
        else:
            pnl.append(stake * 0.95)
            
    df['PnL'] = pnl
    df['Stake'] = stakes
    
    total_pnl = sum(pnl)
    total_stake = sum(stakes)
    total_liability = bets * liability
    
    roi_liability = (total_pnl / total_liability) * 100
    roi_stake = (total_pnl / total_stake) * 100
    avg_pnl_per_bet = total_pnl / bets
    
    print(f"--- {name} ---")
    print(f"Bets (Lays): {bets}")
    print(f"Lays Lost (Winners): {wins} ({(wins/bets)*100:.1f}%)")
    print(f"Total P&L: £{total_pnl:.2f}")
    print(f"ROI on Liability: {roi_liability:.2f}%")
    print(f"ROI on Lay Stakes: {roi_stake:.2f}%")
    print(f"Avg P&L per £100 lay: £{avg_pnl_per_bet:.2f}\n")

calc_lay_metrics(df_control, "CONTROL GROUP (All Handicap Runners BSP 1.50 - 6.00)")

df_sys_filtered = df_sys[df_sys['LTO_SectRank'] > 2].copy()
calc_lay_metrics(df_sys_filtered, "FROZEN RULE (LTO_SectRank > 2 & BSP 1.50 - 6.00)")
