import pyodbc
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

CONN_PROFORM = r'Driver={ODBC Driver 17 for SQL Server};Server=.\PROFORM_RACING;Database=PRODB;Trusted_Connection=yes;'

print('Running Robustness Checks on Extreme LTO Early Speed...')

sql = '''
WITH TargetRaces AS (
    SELECT 
        R.RH_RNo AS TargetRaceId,
        YEAR(R.RH_DateTime) AS RaceYear,
        CAST(R.RH_DateTime AS DATE) AS TargetDate,
        C.C_Name AS TargetCourse,
        R.RH_Exact_Race_Distance AS TargetDist,
        R.RH_GoingLong AS TargetGoing,
        R.RH_NoOfRunners AS TargetRunners,
        HIR.HIR_HNo AS HorseId,
        H.H_Name_No_Anything AS HorseName,
        HIR.HIR_BSP AS TargetBSP,
        HIR.HIR_PositionNo AS TargetFinPos
    FROM dbo.NEW_RH R
    JOIN dbo.NEW_C C ON C.C_ID = R.RH_CNo
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = R.RH_RNo
    JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
    WHERE R.RH_DateTime >= '2023-01-01' AND R.RH_DateTime < '2026-08-22'
      AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
),
HistoricalRuns AS (
    SELECT 
        T.TargetRaceId,
        T.RaceYear,
        T.TargetDate,
        T.TargetCourse,
        T.TargetDist,
        T.TargetGoing,
        T.TargetRunners,
        T.HorseId,
        T.HorseName,
        T.TargetBSP,
        T.TargetFinPos,
        PastR.RH_Exact_Race_Distance AS LTODist,
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
    WHERE PastR.RH_DateTime < T.TargetDate
      AND PastR.RH_RNo <> T.TargetRaceId
)
SELECT * FROM HistoricalRuns WHERE run_rn = 1;
'''

conn = pyodbc.connect(CONN_PROFORM)
df = pd.read_sql(sql, conn)
conn.close()

# Conversions
for col in ['TargetBSP', 'TargetFinPos', 'LTOFinPos', 'Pos1', 'STDiff1', 'STDiff2', 'STDiff3', 'TargetDist', 'LTODist', 'TargetRunners']:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')

df = df.dropna(subset=['TargetBSP', 'TargetFinPos', 'LTOFinPos', 'Pos1', 'STDiff1', 'STDiff2', 'STDiff3'])

# Filter for the Fade pattern
df = df[df['LTOFinPos'] >= 4]
df = df[(df['LTOFinPos'] - df['Pos1']) >= 3]
df['PaceDiff'] = df['STDiff1'] + df['STDiff2'] + df['STDiff3']

def get_bsp_band(bsp):
    if bsp < 1.5: return '<1.5'
    if 1.5 <= bsp < 2.0: return '1.5-2.0'
    if 2.0 <= bsp < 3.0: return '2.0-3.0'
    if 3.0 <= bsp < 4.0: return '3.0-4.0'
    if 4.0 <= bsp < 6.0: return '4.0-6.0'
    if 6.0 <= bsp < 10.0: return '6.0-10.0'
    return '10+'

df['BSP_Band'] = df['TargetBSP'].apply(get_bsp_band)
df['Implied_Prob'] = 1.0 / df['TargetBSP']
df['Back_PnL'] = df.apply(lambda row: 10.0 * (row['TargetBSP'] - 1.0) * 0.95 if row['TargetFinPos'] == 1 else -10.0, axis=1)

def calc_ci(pnl_series, n):
    if n == 0: return ""
    returns = pnl_series / 10.0
    se = returns.std() / np.sqrt(n) if n > 1 else 0
    return f"[{(returns.mean() - 1.96 * se)*100:.1f}%, {(returns.mean() + 1.96 * se)*100:.1f}%]"

# 3. Pace Thresholds across BSP Bands
bsp_order = ['1.5-2.0', '2.0-3.0', '3.0-4.0', '4.0-6.0', '6.0-10.0', '10+']
thresholds = [-1.0, -1.25, -1.5, -1.75, -2.0]

print('--- 1. PACE THRESHOLD ROBUSTNESS (ROI%) ---')
print('| BSP Band | ' + ' | '.join([f'< {t}' for t in thresholds]) + ' |')
print('|---' + '|---'*len(thresholds) + '|')
for band in bsp_order:
    row_vals = []
    for t in thresholds:
        bdf = df[(df['PaceDiff'] < t) & (df['BSP_Band'] == band)]
        if len(bdf) > 0:
            roi = (bdf['Back_PnL'].sum() / (len(bdf) * 10.0)) * 100
            row_vals.append(f"{roi:.1f}% (n={len(bdf)})")
        else:
            row_vals.append("N/A")
    print(f'| {band} | ' + ' | '.join(row_vals) + ' |')

# 2. BSP 6-10 Deep Dive (PaceDiff < -1.5)
subset_6_10 = df[(df['PaceDiff'] < -1.5) & (df['BSP_Band'] == '6.0-10.0')]
n_610 = len(subset_6_10)
winners_610 = (subset_6_10['TargetFinPos'] == 1).sum()
roi_610 = (subset_6_10['Back_PnL'].sum() / (n_610 * 10.0)) * 100
avg_bsp = subset_6_10['TargetBSP'].mean()
ci_610 = calc_ci(subset_6_10['Back_PnL'], n_610)

print('\n--- 2. BSP 6-10 DEEP DIVE (PaceDiff < -1.5) ---')
print(f"Bets: {n_610}")
print(f"Winners: {winners_610} ({(winners_610/n_610)*100:.1f}%)")
print(f"Avg BSP: {avg_bsp:.2f}")
print(f"Expected Win%: {(subset_6_10['Implied_Prob'].mean())*100:.1f}%")
print(f"ROI: {roi_610:.1f}% {ci_610}")

print('\n--- 3. YEAR-BY-YEAR (BSP 6-10, PaceDiff < -1.5) ---')
for year in [2023, 2024, 2025, 2026]:
    ydf = subset_6_10[subset_6_10['RaceYear'] == year]
    if len(ydf) > 0:
        y_win = (ydf['TargetFinPos'] == 1).sum()
        y_roi = (ydf['Back_PnL'].sum() / (len(ydf) * 10.0)) * 100
        print(f"{year}: Bets={len(ydf)}, Winners={y_win}, ROI={y_roi:.1f}%")
    else:
        print(f"{year}: 0 bets")

# Walk-forward proxy
print('\n--- 4. WALK-FORWARD PROXY (BSP 6-10, PaceDiff < -1.5) ---')
dev_df = subset_6_10[subset_6_10['RaceYear'].isin([2023, 2024])]
test_25 = subset_6_10[subset_6_10['RaceYear'] == 2025]
test_26 = subset_6_10[subset_6_10['RaceYear'] == 2026]
if len(dev_df)>0: print(f"Dev (2023-24) ROI: {(dev_df['Back_PnL'].sum()/(len(dev_df)*10))*100:.1f}% (n={len(dev_df)})")
if len(test_25)>0: print(f"Test (2025) ROI: {(test_25['Back_PnL'].sum()/(len(test_25)*10))*100:.1f}% (n={len(test_25)})")
if len(test_26)>0: print(f"Test (2026) ROI: {(test_26['Back_PnL'].sum()/(len(test_26)*10))*100:.1f}% (n={len(test_26)})")

print('\n--- 5. INDIVIDUAL RUNNERS (The 38 Bets) ---')
display_cols = ['TargetDate', 'HorseName', 'TargetCourse', 'TargetGoing', 'TargetRunners', 
                'LTODist', 'TargetDist', 'TargetBSP', 'TargetFinPos']
subset_6_10 = subset_6_10.sort_values('TargetDate')
# Ensure columns exist before displaying
valid_cols = [c for c in display_cols if c in subset_6_10.columns]
print(subset_6_10[valid_cols].to_string(index=False))
