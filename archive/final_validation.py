import pyodbc
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

CONN_PROFORM = r'Driver={ODBC Driver 17 for SQL Server};Server=.\PROFORM_RACING;Database=PRODB;Trusted_Connection=yes;'

print('Running Final Validation and Control Group Comparison...')

# 1. Control Group Query
sql_control = '''
SELECT 
    R.RH_DateTime AS TargetDateTime,
    HIR.HIR_BSP AS TargetBSP,
    HIR.HIR_PositionNo AS TargetFinPos
FROM dbo.NEW_RH R
JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = R.RH_RNo
WHERE R.RH_DateTime >= '2023-01-01' AND R.RH_DateTime < '2026-08-22'
  AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
  AND HIR.HIR_BSP >= 6.0 AND HIR.HIR_BSP < 10.0
'''

# 2. System Query
sql_system = '''
WITH TargetRaces AS (
    SELECT 
        R.RH_RNo AS TargetRaceId,
        R.RH_DateTime AS TargetDateTime,
        YEAR(R.RH_DateTime) AS RaceYear,
        HIR.HIR_HNo AS HorseId,
        HIR.HIR_BSP AS TargetBSP,
        HIR.HIR_PositionNo AS TargetFinPos
    FROM dbo.NEW_RH R
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = R.RH_RNo
    WHERE R.RH_DateTime >= '2023-01-01' AND R.RH_DateTime < '2026-08-22'
      AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
      AND HIR.HIR_BSP >= 6.0 AND HIR.HIR_BSP < 10.0
),
HistoricalRuns AS (
    SELECT 
        T.TargetRaceId,
        T.TargetDateTime,
        T.RaceYear,
        T.TargetBSP,
        T.TargetFinPos,
        PastHIR.HIR_PositionNo AS LTOFinPos,
        SD.SPOS_1 AS Pos1,
        SD.STDIFF_1 AS STDiff1,
        SD.STDIFF_2 AS STDiff2,
        SD.STDIFF_3 AS STDiff3,
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

# Calculate Control Group Stats
df_control['TargetBSP'] = pd.to_numeric(df_control['TargetBSP'], errors='coerce')
df_control['TargetFinPos'] = pd.to_numeric(df_control['TargetFinPos'], errors='coerce')
df_control = df_control.dropna(subset=['TargetBSP', 'TargetFinPos'])
df_control['Back_PnL'] = df_control.apply(lambda row: 10.0 * (row['TargetBSP'] - 1.0) * 0.95 if row['TargetFinPos'] == 1 else -10.0, axis=1)

c_bets = len(df_control)
c_wins = (df_control['TargetFinPos'] == 1).sum()
c_win_pct = (c_wins / c_bets) * 100
c_pnl = df_control['Back_PnL'].sum()
c_roi = (c_pnl / (c_bets * 10.0)) * 100

print(f"\n--- CONTROL GROUP (All Handicap Runners BSP 6.0-10.0) ---")
print(f"Bets: {c_bets}")
print(f"Winners: {c_wins} ({c_win_pct:.2f}%)")
print(f"ROI: {c_roi:.2f}%")
print(f"Expected Win%: {( (1.0 / df_control['TargetBSP']).mean() ) * 100:.2f}%")

# Process System
for col in ['TargetBSP', 'TargetFinPos', 'LTOFinPos', 'Pos1', 'STDiff1', 'STDiff2', 'STDiff3']:
    df_sys[col] = pd.to_numeric(df_sys[col], errors='coerce')

df_sys = df_sys.dropna()
df_sys = df_sys[df_sys['LTOFinPos'] >= 4]
df_sys = df_sys[(df_sys['LTOFinPos'] - df_sys['Pos1']) >= 3]
df_sys['PaceDiff'] = df_sys['STDiff1'] + df_sys['STDiff2'] + df_sys['STDiff3']

# Apply Frozen Rule
df_frozen = df_sys[df_sys['PaceDiff'] < -1.5].copy()
df_frozen = df_frozen.sort_values('TargetDateTime')
df_frozen['Back_PnL'] = df_frozen.apply(lambda row: 10.0 * (row['TargetBSP'] - 1.0) * 0.95 if row['TargetFinPos'] == 1 else -10.0, axis=1)
df_frozen['Win'] = (df_frozen['TargetFinPos'] == 1).astype(int)

# Drawdown & Losing Run Calc
cum_pnl = df_frozen['Back_PnL'].cumsum()
running_max = cum_pnl.cummax()
drawdown = running_max - cum_pnl
max_drawdown = drawdown.max()

losing_run = 0
max_losing_run = 0
for w in df_frozen['Win']:
    if w == 0:
        losing_run += 1
        if losing_run > max_losing_run:
            max_losing_run = losing_run
    else:
        losing_run = 0

f_bets = len(df_frozen)
f_wins = df_frozen['Win'].sum()
f_win_pct = (f_wins / f_bets) * 100
f_pnl = df_frozen['Back_PnL'].sum()
f_roi = (f_pnl / (f_bets * 10.0)) * 100
f_avg_bsp = df_frozen['TargetBSP'].mean()

gross_win = sum([10.0 * (b - 1.0) for b, w in zip(df_frozen['TargetBSP'], df_frozen['Win']) if w == 1])
commission = gross_win * 0.05
total_loss = sum([10.0 for w in df_frozen['Win'] if w == 0])

returns = df_frozen['Back_PnL'] / 10.0
se = returns.std() / np.sqrt(f_bets) if f_bets > 1 else 0
ci_lower = (returns.mean() - 1.96 * se) * 100
ci_upper = (returns.mean() + 1.96 * se) * 100

print(f"\n--- FROZEN RULE (PaceDiff < -1.5 & BSP 6.0-10.0) ---")
print(f"Bets: {f_bets}")
print(f"Winners: {f_wins} ({f_win_pct:.2f}%)")
print(f"Avg BSP: {f_avg_bsp:.2f}")
print(f"Gross Profit: £{gross_win:.2f}")
print(f"Commission Paid: £{commission:.2f}")
print(f"Losing Stakes: £{total_loss:.2f}")
print(f"Net P&L: £{f_pnl:.2f}")
print(f"ROI: {f_roi:.2f}% [{ci_lower:.1f}%, {ci_upper:.1f}%]")
print(f"Max Losing Run: {max_losing_run}")
print(f"Max Drawdown: £{max_drawdown:.2f}")

print("\n--- YEARLY BREAKDOWN ---")
for year in [2023, 2024, 2025, 2026]:
    ydf = df_frozen[df_frozen['RaceYear'] == year]
    if len(ydf) > 0:
        y_bets = len(ydf)
        y_wins = ydf['Win'].sum()
        y_pnl = ydf['Back_PnL'].sum()
        y_roi = (y_pnl / (y_bets * 10.0)) * 100
        print(f"{year} | Bets: {y_bets} | Wins: {y_wins} | P&L: £{y_pnl:.2f} | ROI: {y_roi:.1f}%")
    else:
        print(f"{year} | Bets: 0")
