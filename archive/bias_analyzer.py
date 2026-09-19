import pyodbc
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

CONN_PROFORM = r'Driver={ODBC Driver 17 for SQL Server};Server=.\PROFORM_RACING;Database=PRODB;Trusted_Connection=yes;'

print('Analyzing 1.50-6.00 Handicap Population...\n')

sql = '''
SELECT 
    R.RH_DateTime AS RaceTime,
    R.RH_RNo AS RaceId,
    HIR.HIR_HNo AS HorseId,
    HIR.HIR_BSP AS BSP,
    HIR.HIR_PositionNo AS ResultPos
FROM dbo.NEW_RH R
JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = R.RH_RNo
WHERE R.RH_DateTime >= '2023-01-01' AND R.RH_DateTime <= '2026-08-21'
  AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
  AND HIR.HIR_BSP >= 1.01 AND HIR.HIR_BSP <= 6.00
ORDER BY R.RH_DateTime;
'''

try:
    conn = pyodbc.connect(CONN_PROFORM)
    df = pd.read_sql(sql, conn)
    conn.close()
except Exception as e:
    print(f"SQL Error: {e}")
    import sys
    sys.exit(1)

# Clean and convert data
df['BSP'] = pd.to_numeric(df['BSP'], errors='coerce')
df['ResultPos'] = pd.to_numeric(df['ResultPos'], errors='coerce')
df = df.dropna(subset=['BSP', 'ResultPos'])

df['RaceTime'] = pd.to_datetime(df['RaceTime'])
df['YearMonth'] = df['RaceTime'].dt.to_period('M')
df['Year'] = df['RaceTime'].dt.year

df['IsWin'] = df['ResultPos'] == 1
df['ImpliedProb'] = 1.0 / df['BSP']

# Calculate Lay P&L
liability = 100.0
stakes = []
pnl_gross = []
pnl_net = []

for _, row in df.iterrows():
    bsp = row['BSP']
    stake = liability / (bsp - 1)
    stakes.append(stake)
    if row['IsWin']:
        pnl_gross.append(-liability)
        pnl_net.append(-liability)
    else:
        pnl_gross.append(stake)
        pnl_net.append(stake * 0.95) # 5% commission

df['LayStake'] = stakes
df['PnL_Gross'] = pnl_gross
df['PnL_Net'] = pnl_net
df['Cumulative_PnL'] = df['PnL_Net'].cumsum()

print("--- OVERALL METRICS ---")
bets = len(df)
wins = df['IsWin'].sum()
print(f"Total Bets: {bets}")
print(f"Average BSP: {df['BSP'].mean():.2f}")
print(f"Median BSP: {df['BSP'].median():.2f}")
print(f"Actual Winner %: {(wins/bets)*100:.2f}%")
print(f"Expected Winner % (Market Implied): {df['ImpliedProb'].mean()*100:.2f}%")

print(f"\nTotal Gross P&L: £{sum(pnl_gross):.2f}")
print(f"Total Net P&L: £{sum(pnl_net):.2f}")
print(f"Total Commission Paid: £{sum(pnl_gross) - sum(pnl_net):.2f}")

roi_liab = (sum(pnl_net) / (bets * liability)) * 100
print(f"Liability ROI: {roi_liab:.2f}%")

# Drawdown Calculation
roll_max = df['Cumulative_PnL'].cummax()
drawdown = df['Cumulative_PnL'] - roll_max
print(f"Max Drawdown: £{drawdown.min():.2f}")

print("\n--- ODDS BANDS ANALYSIS ---")
bins = [1.0, 2.0, 3.0, 4.0, 5.0, 6.1]
labels = ['1.50-2.00', '2.01-3.00', '3.01-4.00', '4.01-5.00', '5.01-6.00']
df['OddsBand'] = pd.cut(df['BSP'], bins=bins, labels=labels, right=False)

band_stats = df.groupby('OddsBand').agg(
    Bets=('HorseId', 'count'),
    Wins=('IsWin', 'sum'),
    AvgImplied=('ImpliedProb', 'mean'),
    NetPnL=('PnL_Net', 'sum')
).reset_index()

for _, row in band_stats.iterrows():
    if row['Bets'] > 0:
        actual_win_pct = (row['Wins'] / row['Bets']) * 100
        expected_win_pct = row['AvgImplied'] * 100
        roi = (row['NetPnL'] / (row['Bets'] * liability)) * 100
        print(f"Band: {row['OddsBand']}")
        print(f"  Bets: {row['Bets']} | Actual Win: {actual_win_pct:.2f}% | Expected Win: {expected_win_pct:.2f}%")
        print(f"  Net P&L: £{row['NetPnL']:.2f} | Liability ROI: {roi:.2f}%")

print("\n--- YEARLY ROI ---")
yearly = df.groupby('Year').agg(
    Bets=('HorseId', 'count'),
    NetPnL=('PnL_Net', 'sum')
)
yearly['ROI'] = (yearly['NetPnL'] / (yearly['Bets'] * liability)) * 100
print(yearly)
