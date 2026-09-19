import pyodbc
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

CONN_PROFORM = r'Driver={ODBC Driver 17 for SQL Server};Server=.\PROFORM_RACING;Database=PRODB;Trusted_Connection=yes;'

print('Running High-Volume Strategy Analysis...')

sql = '''
WITH TargetRaces AS (
    SELECT 
        R.RH_RNo AS TargetRaceId,
        R.RH_DateTime AS TargetDateTime,
        C.C_Name AS TargetCourse,
        R.RH_ClassNum AS TargetClass,
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
        T.TargetDateTime,
        T.TargetCourse,
        T.TargetClass,
        T.TargetBSP,
        T.TargetFinPos,
        PastHIR.HIR_PositionNo AS LTOFinPos,
        PastR.RH_ClassNum AS LTOClass,
        SD.SPOS_1 AS Pos1,
        SD.STDIFF_1 + SD.STDIFF_2 + SD.STDIFF_3 AS PaceDiff,
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

try:
    conn = pyodbc.connect(CONN_PROFORM)
    df = pd.read_sql(sql, conn)
    conn.close()
except Exception as e:
    print(f"SQL Error: {e}")
    # Fallback if RH_ClassNum fails
    sql = sql.replace("R.RH_ClassNum", "0").replace("PastR.RH_ClassNum", "0")
    conn = pyodbc.connect(CONN_PROFORM)
    df = pd.read_sql(sql, conn)
    conn.close()

# Conversions
numeric_cols = ['TargetBSP', 'TargetFinPos', 'LTOFinPos', 'Pos1', 'PaceDiff', 'TargetClass', 'LTOClass']
for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors='coerce')

df = df.dropna(subset=['TargetBSP', 'TargetFinPos', 'PaceDiff'])

# Calculate P&L (Backing at 10 flat stakes)
df['Win'] = (df['TargetFinPos'] == 1).astype(int)
df['Back_PnL'] = df.apply(lambda row: 10.0 * (row['TargetBSP'] - 1.0) * 0.95 if row['Win'] == 1 else -10.0, axis=1)

def eval_angle(angle_df, name):
    bets = len(angle_df)
    if bets == 0:
        print(f"\n--- {name} ---")
        print("0 Bets")
        return
    wins = angle_df['Win'].sum()
    pnl = angle_df['Back_PnL'].sum()
    roi = (pnl / (bets * 10.0)) * 100
    win_pct = (wins / bets) * 100
    avg_bsp = angle_df['TargetBSP'].mean()
    print(f"\n--- {name} ---")
    print(f"Bets: {bets} | Wins: {wins} ({win_pct:.1f}%) | Avg BSP: {avg_bsp:.2f}")
    print(f"Net P&L: £{pnl:.2f} | ROI: {roi:.2f}%")
    
    # Slice by BSP
    print("  BSP Bands:")
    for bsp_min, bsp_max in [(1.0, 3.0), (3.0, 6.0), (6.0, 10.0), (10.0, 20.0), (20.0, 1000.0)]:
        bdf = angle_df[(angle_df['TargetBSP'] >= bsp_min) & (angle_df['TargetBSP'] < bsp_max)]
        if len(bdf) > 0:
            b_roi = (bdf['Back_PnL'].sum() / (len(bdf) * 10.0)) * 100
            print(f"    {bsp_min}-{bsp_max}: Bets: {len(bdf)} | ROI: {b_roi:.1f}%")

# Angle 1: Pure AW Speed
# No fade requirement, PaceDiff < -1.0, AW Target
aw_courses = ['Southwell', 'Kempton', 'Lingfield', 'Wolverhampton', 'Chelmsford City', 'Newcastle', 'Dundalk']
a1 = df[(df['PaceDiff'] < -1.0) & (df['TargetCourse'].isin(aw_courses))]
eval_angle(a1, "ANGLE 1: PURE AW SPEED (PaceDiff < -1.0, AW Target)")

# Angle 2: Soft Fade
# PaceDiff < 0, LTOFinPos >= 4, Dropped 3+ from Pos1, Any Course
a2 = df[(df['PaceDiff'] < 0) & (df['LTOFinPos'] >= 4) & ((df['LTOFinPos'] - df['Pos1']) >= 3)]
eval_angle(a2, "ANGLE 2: SOFT FADE (PaceDiff < 0, LTOFade >= 3, Any Course)")

# Angle 3: Speed x Class Drop
# PaceDiff < 0, TargetClass > LTOClass (larger number = lower class)
a3 = df[(df['PaceDiff'] < 0) & (df['TargetClass'] > df['LTOClass'])]
eval_angle(a3, "ANGLE 3: SPEED x CLASS DROP (PaceDiff < 0, Dropping Class, Any Course)")

# Wait, let's combine AW + PaceDiff < 0 (A high volume variation of Angle 1)
a4 = df[(df['PaceDiff'] < 0) & (df['TargetCourse'].isin(aw_courses))]
eval_angle(a4, "ANGLE 4: RELAXED AW SPEED (PaceDiff < 0, AW Target)")
