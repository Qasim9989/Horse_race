import pyodbc
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

CONN_PROFORM = r'Driver={ODBC Driver 17 for SQL Server};Server=.\PROFORM_RACING;Database=PRODB;Trusted_Connection=yes;'

print('Running Value Diagnostic on Extreme LTO Early Speed...')

sql = '''
WITH TargetRaces AS (
    SELECT 
        R.RH_RNo AS TargetRaceId,
        YEAR(R.RH_DateTime) AS RaceYear,
        R.RH_DateTime AS TargetDateTime,
        C.C_Name AS Course,
        HIR.HIR_HNo AS HorseId,
        HIR.HIR_BSP AS TargetBSP,
        HIR.HIR_PositionNo AS TargetFinPos
    FROM dbo.NEW_RH R
    JOIN dbo.NEW_C C ON C.C_ID = R.RH_CNo
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = R.RH_RNo
    WHERE R.RH_DateTime >= '2023-01-01' AND R.RH_DateTime < '2026-08-22'
      AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
),
HistoricalRuns AS (
    SELECT 
        T.TargetRaceId,
        T.RaceYear,
        T.HorseId,
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
df = pd.read_sql(sql, conn)
conn.close()

df['TargetBSP'] = pd.to_numeric(df['TargetBSP'], errors='coerce')
df['TargetFinPos'] = pd.to_numeric(df['TargetFinPos'], errors='coerce')
df['LTOFinPos'] = pd.to_numeric(df['LTOFinPos'], errors='coerce')
df['Pos1'] = pd.to_numeric(df['Pos1'], errors='coerce')
df['STDiff1'] = pd.to_numeric(df['STDiff1'], errors='coerce')
df['STDiff2'] = pd.to_numeric(df['STDiff2'], errors='coerce')
df['STDiff3'] = pd.to_numeric(df['STDiff3'], errors='coerce')

df = df.dropna(subset=['TargetBSP', 'TargetFinPos', 'LTOFinPos', 'Pos1', 'STDiff1', 'STDiff2', 'STDiff3'])

# We are testing extreme early speed (the horse went fast early), we DO NOT filter by them fading (LTOFinPos) here,
# because we want to see the pure value of extreme early speed.
# Wait, the user specifically referenced the "Pace Meltdown" runners ("horse demonstrated exceptional early speed... don't dismiss because it faded").
# I will still filter for fading (LTOFinPos >= 4 and LTOFinPos - Pos1 >= 3) to stick to the exact profile we were testing.
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

# Calculate BACK P&L (£10 stakes)
def calc_back_pnl(row):
    if row['TargetFinPos'] == 1:
        return 10.0 * (row['TargetBSP'] - 1.0) * 0.95
    return -10.0

df['Back_PnL'] = df.apply(calc_back_pnl, axis=1)

bsp_order = ['1.5-2.0', '2.0-3.0', '3.0-4.0', '4.0-6.0', '6.0-10.0', '10+']

print('\n--- EXTREME EARLY SPEED (BACK VALUE) DIAGNOSTIC ---')
# We will test PaceDiff < -1.5 as the definition of "extreme early speed"
thresh = -1.5
print(f'Profile: Led early (Pos1), faded 3+ places, PaceDiff < {thresh}')

subset = df[df['PaceDiff'] < thresh]

results = []
for band in bsp_order:
    band_df = subset[subset['BSP_Band'] == band]
    n = len(band_df)
    if n == 0:
        continue
        
    winners = (band_df['TargetFinPos'] == 1).sum()
    win_pct = (winners / n) * 100
    expected_win_pct = band_df['Implied_Prob'].mean() * 100
    
    # ROI and Confidence Interval
    roi = (band_df['Back_PnL'].sum() / (n * 10.0)) * 100
    
    # Standard Error of ROI (using individual return percentages)
    returns = band_df['Back_PnL'] / 10.0
    se = returns.std() / np.sqrt(n) if n > 1 else 0
    ci_lower = (returns.mean() - 1.96 * se) * 100
    ci_upper = (returns.mean() + 1.96 * se) * 100
    ci_str = f"[{ci_lower:.1f}%, {ci_upper:.1f}%]"
    
    # Yearly ROI
    yearly_roi = []
    for year in sorted(band_df['RaceYear'].unique()):
        ydf = band_df[band_df['RaceYear'] == year]
        y_n = len(ydf)
        if y_n > 0:
            y_roi = (ydf['Back_PnL'].sum() / (y_n * 10.0)) * 100
            yearly_roi.append(f"{year}:{y_roi:.1f}%")
            
    yearly_str = " | ".join(yearly_roi)
    
    results.append({
        'BSP_Band': band,
        'N': n,
        'Win%': f"{win_pct:.1f}%",
        'Exp_Win%': f"{expected_win_pct:.1f}%",
        'ROI': f"{roi:.1f}%",
        '95% CI': ci_str,
        'Yearly_ROI': yearly_str
    })
    
res_df = pd.DataFrame(results)
print(res_df.to_markdown(index=False))
