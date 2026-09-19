import pyodbc
import pandas as pd

CONN = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=(localdb)\\MSSQLLocalDB;"
    "Database=PRODB;"
    "Trusted_Connection=yes;"
)

conn = pyodbc.connect(CONN)

# Reuse the same backtest SQL as b2l_realistic_stress_test.py,
# but filter to a small date range.
sql = """
WITH RaceSequence AS (
    SELECT 
        HIR.HIR_HNo,
        LOWER(H.H_Name_No_Anything) AS horse_clean,
        R.RH_RNo,
        R.RH_DateTime,
        CAST(R.RH_DateTime AS DATE) AS RaceDate,
        YEAR(R.RH_DateTime) AS Yr,
        FORMAT(R.RH_DateTime, 'yyyy-MM') AS YrMonth,
        R.RH_Name,
        R.RH_NoOfRunners AS FieldSize,
        R.RH_HandicapLimit,
        HIR.HIR_PositionNo AS FinPos,
        HIR.HIR_BSP AS BSP,
        HIR.HIR_PaceAbbrev AS Pace,
        HIR.HIR_DSLR AS DSLR,
        HIR.HIR_JockeysClaim AS JockClaim,
        HIR.HIR_CommentsInRunning AS Comment,
        SD.ASL,
        SD.SL_Finish,
        ROW_NUMBER() OVER(PARTITION BY HIR.HIR_HNo ORDER BY R.RH_DateTime ASC) AS RunSeq
    FROM dbo.NEW_HIR HIR
    JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
    JOIN dbo.NEW_RH R ON R.RH_RNo = HIR.HIR_RNo
    LEFT JOIN dbo.SData SD ON SD.SD_RNo = HIR.HIR_RNo AND SD.SD_HNo = HIR.HIR_HNo
    WHERE R.RH_DateTime >= '2015-12-19'
)
SELECT 
    Curr.horse_clean,
    Curr.RH_DateTime,
    Curr.RaceDate,
    Curr.Yr,
    Curr.YrMonth,
    Curr.FieldSize,
    Curr.FinPos,
    Curr.BSP,
    Prev.Pace AS LTO_Pace,
    Prev.DSLR AS LTO_DSLR,
    Prev.JockClaim AS LTO_JockClaim,
    Prev.Comment AS LTO_Comment,
    (Prev.ASL - Prev.SL_Finish) AS LTO_StrideDecay
FROM RaceSequence Curr
JOIN RaceSequence Prev 
    ON Prev.HIR_HNo = Curr.HIR_HNo 
   AND Prev.RunSeq = Curr.RunSeq - 1
WHERE Curr.BSP >= 20.0
  AND (Curr.RH_HandicapLimit IS NOT NULL OR LOWER(Curr.RH_Name) LIKE '%handicap%')
  AND Curr.FinPos IS NOT NULL
  AND Curr.RaceDate IN ('2026-08-19', '2026-08-18', '2026-08-17')
ORDER BY Curr.RaceDate, Curr.RH_DateTime;
"""

df = pd.read_sql(sql, conn)
conn.close()

# Apply the same scoring logic as the backtest
SPEED_WORDS = [
    "led","ran on","ran on well","ran on strongly","kept on","kept on well",
    "strong finish","headway","good headway","quickened","quickened well",
    "chased leaders","chased leader","pushed along","driven out","made all",
    "disputed lead","prominent","stayed on","stayed on well","rallied","finished well"
]
DISC_WORDS = [
    "slowly away","dwelt","pulled hard","keen","hung","erratic",
    "lost ground start","missed break","reared"
]

def compute_scores(row):
    pace = str(row['LTO_Pace'] or '').upper()
    is_fr = pace in ['L', 'P', 'F', 'LEAD', 'PROMINENT']
    comm = str(row['LTO_Comment'] or '').lower()
    spd = any(w in comm for w in SPEED_WORDS)
    bad = any(w in comm for w in DISC_WORDS)
    sd = row['LTO_StrideDecay'] if pd.notna(row['LTO_StrideDecay']) else 0.0
    dslr = row['LTO_DSLR'] if pd.notna(row['LTO_DSLR']) else 99
    jock = row['LTO_JockClaim'] if pd.notna(row['LTO_JockClaim']) else 0.0

    score = 0
    if is_fr: score += 3
    if spd: score += 2
    if jock > 0: score += 2
    if dslr <= 7: score += 2
    if not bad: score += 2
    if bad: score -= 2
    if sd >= 0.66: score -= 3  # FIXED: ft not m
    if dslr > 120: score -= 1
    return score, is_fr, spd, bad

scores_info = [compute_scores(r) for _, r in df.iterrows()]
df['Score'] = [x[0] for x in scores_info]
df['IsFrontRunner'] = [x[1] for x in scores_info]
df['GoodSpeed'] = [x[2] for x in scores_info]
df['BadDisc'] = [x[3] for x in scores_info]

# Assign tiers exactly as backtest
df['Tier'] = 'No Bet'
df.loc[df['Score'] >= 7, 'Tier'] = 'WIN STAR (7+)'
df.loc[(df['Score'] >= 4) & (df['Score'] < 7), 'Tier'] = 'Tier 1 EW (4-6)'
df.loc[(df['Score'] >= 2) & (df['GoodSpeed']) & (df['Score'] < 4), 'Tier'] = 'Tier 2 B2L (2-3 + Speed)'

# Save for easy comparison
out_path = r"E:\Test\racing-form-system\reports\b2l_backtest_slice_2026-08-17_to_19.csv"
df.to_csv(out_path, index=False)
print("Backtest slice saved to:", out_path)
print(df[df['Tier'] != 'No Bet'][['RaceDate', 'horse_clean', 'BSP', 'Score', 'Tier']].head(20))
