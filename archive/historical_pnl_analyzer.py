import pyodbc
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

CONN_PROFORM = r'Driver={ODBC Driver 17 for SQL Server};Server=.\PROFORM_RACING;Database=PRODB;Trusted_Connection=yes;'

print("Running historical P&L and Bias Analysis (2023-2026)...")

sql = '''
WITH TargetRaces AS (
    SELECT 
        R.RH_RNo AS TargetRaceId,
        YEAR(R.RH_DateTime) AS RaceYear,
        R.RH_DateTime AS TargetDateTime,
        C.C_Name AS Course,
        H.H_Name_No_Anything AS HorseName,
        HIR.HIR_HNo AS HorseId,
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
        T.HorseId,
        T.TargetDateTime,
        T.Course,
        T.HorseName,
        T.TargetBSP,
        T.TargetFinPos,
        PastR.RH_RNo AS LTORaceId,
        PastR.RH_DateTime AS LTODateTime,
        PastHIR.HIR_PositionNo AS LTOFinPos,
        SD.SPOS_1 AS Pos1,
        SD.STDIFF_1 AS STDiff1,
        SD.STDIFF_2 AS STDiff2,
        SD.STDIFF_3 AS STDiff3,
        SD.FSPDIFF_Finish AS FSPDiff,
        ROW_NUMBER() OVER(PARTITION BY T.HorseId, T.TargetRaceId ORDER BY PastR.RH_DateTime DESC) as run_rn
    FROM TargetRaces T
    LEFT JOIN dbo.NEW_HIR PastHIR ON PastHIR.HIR_HNo = T.HorseId
    LEFT JOIN dbo.NEW_RH PastR ON PastR.RH_RNo = PastHIR.HIR_RNo AND PastR.RH_DateTime < T.TargetDateTime AND PastR.RH_RNo <> T.TargetRaceId
    LEFT JOIN dbo.SData SD ON SD.SD_RNo = PastHIR.HIR_RNo AND SD.SD_HNo = PastHIR.HIR_HNo
)
SELECT * FROM HistoricalRuns WHERE run_rn = 1 OR run_rn IS NULL;
'''

def check_pace_meltdown(row):
    fin_pos = row.get('LTOFinPos', 0)
    pos_1 = row.get('Pos1', 0)
    std1 = row.get('STDiff1', 0)
    std2 = row.get('STDiff2', 0)
    std3 = row.get('STDiff3', 0)
    if pd.isna(fin_pos) or pd.isna(pos_1): return False
    if pd.isna(std1) or pd.isna(std2) or pd.isna(std3): return False
    if fin_pos < 4: return False
    if fin_pos - pos_1 < 3: return False
    if (std1 + std2 + std3) > -1.5: return False
    return True

def check_hidden_finisher(row):
    fin_pos = row.get('LTOFinPos', 0)
    pos_1 = row.get('Pos1', 0)
    fsp_diff = row.get('FSPDiff', 0)
    if pd.isna(fin_pos) or pd.isna(pos_1) or pd.isna(fsp_diff): return False
    if fin_pos < 4: return False
    if pos_1 < 5: return False
    if fsp_diff <= 1.5: return False
    return True

conn = pyodbc.connect(CONN_PROFORM)
df = pd.read_sql(sql, conn)
conn.close()

# Deduplicate if LEFT JOIN caused issues
df = df.sort_values('LTODateTime', ascending=False).drop_duplicates(subset=['TargetRaceId', 'HorseId'], keep='first')

# Bias check on Missing SData
df['Has_SData'] = df['FSPDiff'].notna()

course_bias = df.groupby('Course').agg(
    Total_Runners=('TargetRaceId', 'count'),
    Missing_SData=('Has_SData', lambda x: (~x).sum())
)
course_bias['Missing_Pct'] = (course_bias['Missing_SData'] / course_bias['Total_Runners']) * 100
top_missing_courses = course_bias[course_bias['Total_Runners'] >= 100].sort_values('Missing_Pct', ascending=False).head(15)

print("\n=== TOP 15 COURSES MISSING SDATA ===")
print(top_missing_courses.to_markdown())

# Calculate P&L
df_sdata = df[df['Has_SData'] == True].copy()
df_sdata['Is_Meltdown'] = df_sdata.apply(lambda row: check_pace_meltdown(row) and (1.50 <= (float(row['TargetBSP']) if pd.notna(row['TargetBSP']) else 0) <= 6.00), axis=1)
df_sdata['Is_Finisher'] = df_sdata.apply(check_hidden_finisher, axis=1)

# Back P&L logic
def calc_back_pnl(row):
    if not row['Is_Finisher']: return 0.0
    if pd.isna(row['TargetBSP']) or row['TargetBSP'] == 0: return -10.0
    if row['TargetFinPos'] == 1:
        return 10.0 * (row['TargetBSP'] - 1) * 0.95
    return -10.0

# Lay P&L logic
def calc_lay_pnl(row):
    if not row['Is_Meltdown']: return 0.0
    bsp = row['TargetBSP']
    if pd.isna(bsp) or bsp <= 1.0: return 0.0
    stake = 10.0 / (bsp - 1)
    if row['TargetFinPos'] == 1:
        return -10.0 # Lost liability
    return stake * 0.95 # Kept stake * 0.95

df_sdata['Back_PnL'] = df_sdata.apply(calc_back_pnl, axis=1)
df_sdata['Lay_PnL'] = df_sdata.apply(calc_lay_pnl, axis=1)
df_sdata['Back_Bet'] = df_sdata['Is_Finisher'].astype(int)
df_sdata['Lay_Bet'] = df_sdata['Is_Meltdown'].astype(int)
df_sdata['Back_Win'] = (df_sdata['Is_Finisher'] & (df_sdata['TargetFinPos'] == 1)).astype(int)
df_sdata['Lay_Win'] = (df_sdata['Is_Meltdown'] & (df_sdata['TargetFinPos'] > 1)).astype(int)

print("\n=== HIDDEN FINISHER (BACK) P&L ===")
back_summary = df_sdata[df_sdata['Is_Finisher'] == True].groupby('RaceYear').agg(
    Bets=('Back_Bet', 'sum'),
    Winners=('Back_Win', 'sum'),
    PnL=('Back_PnL', 'sum')
)
back_summary['StrikeRate'] = (back_summary['Winners'] / back_summary['Bets']) * 100
back_summary['ROI'] = (back_summary['PnL'] / (back_summary['Bets'] * 10)) * 100
print(back_summary.round(2).to_markdown())

print("\n=== PACE MELTDOWN (LAY) P&L ===")
lay_summary = df_sdata[df_sdata['Is_Meltdown'] == True].groupby('RaceYear').agg(
    Lays=('Lay_Bet', 'sum'),
    Successful_Lays=('Lay_Win', 'sum'),
    PnL=('Lay_PnL', 'sum')
)
lay_summary['StrikeRate'] = (lay_summary['Successful_Lays'] / lay_summary['Lays']) * 100
# ROI for lays is PnL / Total Liability (Lays * £10)
lay_summary['ROI'] = (lay_summary['PnL'] / (lay_summary['Lays'] * 10)) * 100
print(lay_summary.round(2).to_markdown())
