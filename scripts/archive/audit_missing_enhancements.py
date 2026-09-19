import sys
import pyodbc
import pandas as pd
import numpy as np
import datetime
import warnings
warnings.filterwarnings('ignore')

CONN_STR = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=(localdb)\MSSQLLocalDB;"
    r"Database=RACINGTV_2023_2026;"
    r"Trusted_Connection=yes;"
    r"MultipleActiveResultSets=True;"
)

def parse_sp(sp_str):
    if not sp_str or pd.isna(sp_str):
        return None
    sp_str = str(sp_str).strip()
    if sp_str.lower() in ['evens', 'eve']:
        return 2.0
    if '/' in sp_str:
        parts = sp_str.split('/')
        try:
            num = float(parts[0])
            den = float(parts[1])
            return round(1.0 + (num / den), 2)
        except:
            return None
    try:
        return float(sp_str)
    except:
        return None

def run_missing_enhancements_audit():
    print("="*95)
    print("  AUDITING POTENTIAL MISSING FEATURES FOR THE LAY SYSTEM")
    print("="*95)

    conn = pyodbc.connect(CONN_STR)
    
    sql = """
    SELECT 
        r.RaceDate,
        r.RaceTime,
        r.CourseName,
        r.RaceTitle,
        r.HorseName,
        r.PosNo,
        r.JockeyClaim,
        r.SP,
        r.BSP,
        r.Comment,
        TRY_CAST(iq.StrideLength AS FLOAT) as StrideLength,
        TRY_CAST(iq.TopSpeed AS FLOAT) as TopSpeed,
        TRY_CAST(iq.FinishingSpeedPct AS FLOAT) as FinishingSpeedPct
    FROM dbo.Scraped_Results r
    LEFT JOIN dbo.Scraped_RaceIQ iq 
      ON r.RaceDate = iq.RaceDate AND r.CourseName = iq.CourseName AND r.HorseName = iq.HorseName
    ORDER BY r.RaceDate ASC, r.CourseName, r.RaceTime;
    """
    df = pd.read_sql(sql, conn)
    conn.close()

    df['RaceDate'] = pd.to_datetime(df['RaceDate'])
    df['DecSP'] = df['BSP'].apply(parse_sp).fillna(df['SP'].apply(parse_sp))
    df['IsWin'] = df['PosNo'].astype(str).str.strip().str.lower().isin(['1', '1st'])
    df['IsHandicap'] = df['RaceTitle'].astype(str).str.contains('h\'cap|handicap|hcap', case=False, na=False)
    df['CleanHorse'] = df['HorseName'].astype(str).str.lower().str.replace(r'[^a-z0-9]', '', regex=True)

    df = df.sort_values(['CleanHorse', 'RaceDate', 'RaceTime']).reset_index(drop=True)

    df['PrevDate'] = df.groupby('CleanHorse')['RaceDate'].shift(1)
    df['DSLR'] = (df['RaceDate'] - df['PrevDate']).dt.days.fillna(999)

    df['PrevPos'] = df.groupby('CleanHorse')['PosNo'].shift(1).astype(str).str.strip().str.lower()
    df['PrevWin'] = df['PrevPos'].isin(['1', '1st'])
    df['PrevSecond'] = df['PrevPos'].isin(['2', '2nd'])

    df['PrevComment'] = df.groupby('CleanHorse')['Comment'].shift(1).astype(str).str.lower()
    df['BadDisc'] = df['PrevComment'].str.contains('slowly away|dwelt|pulled hard|keen|hung|erratic', regex=True)

    df['PrevFSP'] = df.groupby('CleanHorse')['FinishingSpeedPct'].shift(1)
    df['PrevStride'] = df.groupby('CleanHorse')['StrideLength'].shift(1)
    df['PrevTopSpeed'] = df.groupby('CleanHorse')['TopSpeed'].shift(1)

    df['FSP_Decay'] = df['PrevFSP'].apply(lambda x: 1 if (pd.notna(x) and x < 97.5) else 0)
    df['Short_Stride'] = df['PrevStride'].apply(lambda x: 1 if (pd.notna(x) and x < 6.80) else 0)
    df['Low_Top_Speed'] = df['PrevTopSpeed'].apply(lambda x: 1 if (pd.notna(x) and x < 37.0) else 0)

    df['ClaimVal'] = pd.to_numeric(df['JockeyClaim'], errors='coerce').fillna(0)

    # 1. BASE SYSTEM SCORE
    base_score = np.zeros(len(df))
    base_score += np.where(df['PrevWin'], 3, 0)
    base_score += np.where(df['PrevSecond'], 1, 0)
    base_score += np.where((df['DSLR'] <= 7) | (df['ClaimVal'] > 0), 2, 0)
    base_score -= np.where(df['DSLR'] > 60, 2, 0)
    base_score -= np.where(df['BadDisc'], 2, 0)
    base_score -= np.where((~df['PrevWin']) & (~df['PrevSecond']), 2, 0)
    base_score -= np.where(df['FSP_Decay'] == 1, 2, 0)
    base_score -= np.where(df['Short_Stride'] == 1, 2, 0)
    base_score -= np.where(df['Low_Top_Speed'] == 1, 1, 0)

    df['BaseScore'] = base_score

    # 2. ENHANCED SCORE (WITH ODDS TREND + BAD FORM STREAK)
    df['PrevThirdOrWorse'] = (~df['PrevWin']) & (~df['PrevSecond'])
    df['LongStreakUnplaced'] = df.groupby('CleanHorse')['PrevThirdOrWorse'].rolling(3).sum().reset_index(level=0, drop=True) == 3

    enhanced_score = base_score.copy()
    enhanced_score -= np.where(df['LongStreakUnplaced'].fillna(False), 1, 0) # 3+ Consecutive Losses

    df['EnhancedScore'] = enhanced_score

    print("\n  [AUDIT COMPARISON] BASE SYSTEM vs ENHANCED SYSTEM WITH FORM STREAKS:")
    print("="*95)

    for label, score_col in [("Current Base Lay System", "BaseScore"), ("Enhanced System (+3-Loss Streak Filter)", "EnhancedScore")]:
        df['ScoreTemp'] = df[score_col]
        df_hcap = df[df['IsHandicap']].copy()
        race_groups = df_hcap.groupby(['RaceDate', 'CourseName', 'RaceTime'])
        df_hcap['FieldSize'] = race_groups['CleanHorse'].transform('count')
        df_valid = df_hcap[df_hcap['FieldSize'] >= 5].copy()

        df_valid['RankWorst'] = df_valid.groupby(['RaceDate', 'CourseName', 'RaceTime'])['ScoreTemp'].rank(method='min', ascending=True)
        df_valid['IsLayTarget'] = (df_valid['RankWorst'] <= 2) & (df_valid['ScoreTemp'] < 0) & (df_valid['DecSP'].notna()) & (df_valid['DecSP'] <= 20.0)

        qualifiers = df_valid[df_valid['IsLayTarget']].copy()
        FIXED_LIABILITY = 15.00
        qualifiers['Stake'] = FIXED_LIABILITY / (qualifiers['DecSP'] - 1.0)
        qualifiers['LayPNL'] = np.where(qualifiers['IsWin'], -FIXED_LIABILITY, qualifiers['Stake'] * 0.98)

        total_lays = len(qualifiers)
        total_pnl = qualifiers['LayPNL'].sum()
        total_wins = qualifiers['IsWin'].sum()
        lay_success_rate = (1 - (total_wins / total_lays)) * 100
        total_staked = qualifiers['Stake'].sum()
        roi = (total_pnl / total_staked) * 100

        print(f"  {label:<45} | Bets: {total_lays:,} | Win Rate: {lay_success_rate:.2f}% | P&L: +£{total_pnl:,.2f} | ROI: +{roi:.2f}%")

    print("="*95)

if __name__ == '__main__':
    run_missing_enhancements_audit()
