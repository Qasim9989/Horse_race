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

def run_winner_pattern_analysis():
    print("="*95)
    print("  STATISTICAL WINNER PATTERN ANALYSIS (RACINGTV_2023_2026)")
    print("  Comparing 169,706 Runner Records: WINNERS vs LOSERS")
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
    
    df['PosClean'] = df['PosNo'].astype(str).str.extract(r'(\d+)')[0]
    df['PosInt'] = pd.to_numeric(df['PosClean'], errors='coerce').fillna(999).astype(int)
    df['IsWin'] = df['PosInt'] == 1
    
    df['IsHandicap'] = df['RaceTitle'].astype(str).str.contains('h\'cap|handicap|hcap', case=False, na=False)
    df['CleanHorse'] = df['HorseName'].astype(str).str.lower().str.replace(r'[^a-z0-9]', '', regex=True)

    df = df.sort_values(['CleanHorse', 'RaceDate', 'RaceTime']).reset_index(drop=True)

    # LTO features (point-in-time zero lookahead)
    df['PrevDate'] = df.groupby('CleanHorse')['RaceDate'].shift(1)
    df['DSLR'] = (df['RaceDate'] - df['PrevDate']).dt.days.fillna(999)

    df['PrevPosInt'] = df.groupby('CleanHorse')['PosInt'].shift(1).fillna(999).astype(int)
    df['PrevWin'] = df['PrevPosInt'] == 1
    df['PrevPlaced'] = df['PrevPosInt'] <= 3

    df['PrevComment'] = df.groupby('CleanHorse')['Comment'].shift(1).astype(str).str.lower()
    df['BadDisc'] = df['PrevComment'].str.contains('slowly away|dwelt|pulled hard|keen|hung|erratic', regex=True)
    df['GoodForm'] = df['PrevComment'].str.contains('stayed on|ran on|led|prominent|made all|pressed', regex=True)

    df['PrevFSP'] = df.groupby('CleanHorse')['FinishingSpeedPct'].shift(1)
    df['PrevStride'] = df.groupby('CleanHorse')['StrideLength'].shift(1)
    df['PrevTopSpeed'] = df.groupby('CleanHorse')['TopSpeed'].shift(1)

    df['ClaimVal'] = pd.to_numeric(df['JockeyClaim'], errors='coerce').fillna(0)

    # 1. STATISTICAL COMPARISON: WINNERS vs LOSERS
    winners = df[df['IsWin']].copy()
    losers = df[~df['IsWin']].copy()

    print("\n[1/3] STATISTICAL COMPARISON OF WINNERS VS LOSERS:")
    print("-" * 95)
    print(f"  Total Winners Analyzed: {len(winners):,}")
    print(f"  Total Losers Analyzed:  {len(losers):,}")
    print("-" * 95)
    print(f"  Feature                        | Winners Avg / %  | Losers Avg / %  | Predictive Edge")
    print("-" * 95)
    print(f"  Avg Stride Length (LTO)        | {winners['PrevStride'].mean():.2f} meters     | {losers['PrevStride'].mean():.2f} meters     | +{winners['PrevStride'].mean() - losers['PrevStride'].mean():.2f}m")
    print(f"  Avg Top Speed (LTO)            | {winners['PrevTopSpeed'].mean():.2f} MPH        | {losers['PrevTopSpeed'].mean():.2f} MPH        | +{winners['PrevTopSpeed'].mean() - losers['PrevTopSpeed'].mean():.2f} MPH")
    print(f"  Avg Finishing Speed % (LTO)    | {winners['PrevFSP'].mean():.2f}%         | {losers['PrevFSP'].mean():.2f}%         | +{winners['PrevFSP'].mean() - losers['PrevFSP'].mean():.2f}%")
    print(f"  Won LTO (PrevWin) %            | {winners['PrevWin'].mean()*100:.2f}%         | {losers['PrevWin'].mean()*100:.2f}%         | {winners['PrevWin'].mean()/max(0.001, losers['PrevWin'].mean()):.2f}x Higher")
    print(f"  Placed LTO (Top 3) %           | {winners['PrevPlaced'].mean()*100:.2f}%         | {losers['PrevPlaced'].mean()*100:.2f}%         | {winners['PrevPlaced'].mean()/max(0.001, losers['PrevPlaced'].mean()):.2f}x Higher")
    print(f"  Quick Return (<= 14 Days) %    | {(winners['DSLR'] <= 14).mean()*100:.2f}%         | {(losers['DSLR'] <= 14).mean()*100:.2f}%         | {(winners['DSLR'] <= 14).mean()/max(0.001, (losers['DSLR'] <= 14).mean()):.2f}x Higher")
    print(f"  Positive Comment (LTO) %       | {winners['GoodForm'].mean()*100:.2f}%         | {losers['GoodForm'].mean()*100:.2f}%         | {winners['GoodForm'].mean()/max(0.001, losers['GoodForm'].mean()):.2f}x Higher")
    print(f"  Discipline Issue (LTO) %       | {winners['BadDisc'].mean()*100:.2f}%         | {losers['BadDisc'].mean()*100:.2f}%         | {losers['BadDisc'].mean()/max(0.001, winners['BadDisc'].mean()):.2f}x Less in Winners")
    print("-" * 95)

    # 2. BUILD BRAND NEW WINNER BACKING SCORE
    print("\n[2/3] BUILDING BRAND NEW WINNER BACKING SCORE SYSTEM...")

    winner_score = np.zeros(len(df))
    # Positive Winner Modifiers
    winner_score += np.where(df['PrevWin'], 4, 0)
    winner_score += np.where(df['PrevPosInt'] == 2, 3, 0)
    winner_score += np.where(df['PrevPosInt'] == 3, 2, 0)
    winner_score += np.where(df['DSLR'] <= 14, 3, 0)
    winner_score += np.where(df['PrevFSP'] >= 100.0, 3, 0)
    winner_score += np.where(df['PrevStride'] >= 7.10, 2, 0)
    winner_score += np.where(df['GoodForm'], 2, 0)

    # Negative Winner Penalties
    winner_score -= np.where(df['BadDisc'], 3, 0)
    winner_score -= np.where(df['DSLR'] > 45, 3, 0)
    winner_score -= np.where(df['PrevFSP'] < 97.5, 3, 0)
    winner_score -= np.where(df['PrevStride'] < 6.80, 2, 0)

    df['WinnerScore'] = winner_score

    # Filter Valid Races & Odds (Handicaps, Field >= 5)
    df_hcap = df[df['IsHandicap']].copy()
    race_groups = df_hcap.groupby(['RaceDate', 'CourseName', 'RaceTime'])
    df_hcap['FieldSize'] = race_groups['CleanHorse'].transform('count')
    df_valid = df_hcap[(df_hcap['FieldSize'] >= 5) & (df_hcap['DecSP'].notna())].copy()

    # Rank Highest Winner Score per race (#1 Top Winner Target in Race)
    df_valid['RankWinner'] = df_valid.groupby(['RaceDate', 'CourseName', 'RaceTime'])['WinnerScore'].rank(method='min', ascending=False)

    # WINNER BACKING SELECTION RULE: #1 Top Scored Horse in Race with Score >= 5
    df_valid['IsWinnerTarget'] = (df_valid['RankWinner'] == 1) & (df_valid['WinnerScore'] >= 5)

    back_qualifiers = df_valid[df_valid['IsWinnerTarget']].copy()
    
    # Backing P&L (£1 Flat Stake / 2% commission on win returns)
    back_qualifiers['BackPNL'] = np.where(back_qualifiers['IsWin'], (back_qualifiers['DecSP'] - 1.0) * 0.98, -1.0)
    back_qualifiers['YearMonth'] = back_qualifiers['RaceDate'].dt.strftime('%Y-%m')

    print(f"  Generated {len(back_qualifiers):,} Top Winner Backing Selections.")

    print("\n[3/3] NEW WINNER BACKING SYSTEM MONTHLY PERFORMANCE (2023 - 2026):")
    print("="*95)

    monthly = back_qualifiers.groupby('YearMonth').agg(
        Total_Bets=('BackPNL', 'count'),
        Wins=('IsWin', 'sum'),
        Monthly_PNL=('BackPNL', 'sum')
    ).reset_index()

    monthly['Win_Rate_%'] = round((monthly['Wins'] / monthly['Total_Bets']) * 100, 2)
    monthly['Cum_PNL'] = monthly['Monthly_PNL'].cumsum()
    monthly['Peak_PNL'] = monthly['Cum_PNL'].cummax()
    monthly['Drawdown'] = monthly['Cum_PNL'] - monthly['Peak_PNL']

    print(monthly[['YearMonth', 'Total_Bets', 'Wins', 'Win_Rate_%', 'Monthly_PNL', 'Cum_PNL', 'Drawdown']].to_string(index=False))
    print("="*95)

    total_bets = len(back_qualifiers)
    total_wins = back_qualifiers['IsWin'].sum()
    total_pnl = back_qualifiers['BackPNL'].sum()
    win_sr = (total_wins / total_bets) * 100
    roi = (total_pnl / total_bets) * 100
    max_dd = monthly['Drawdown'].min()

    print(f"\n  NEW WINNER BACKING SYSTEM OVERALL SUMMARY:")
    print(f"  - Total Back Bets:      {total_bets:,}")
    print(f"  - Win Strike Rate:      {win_sr:.2f}% ({total_wins:,} Winners)")
    print(f"  - Total Net P&L:        +£{total_pnl:,.2f} (@ £1 flat stake)")
    print(f"  - System ROI:           +{roi:.2f}%")
    print(f"  - Maximum Drawdown:     £{max_dd:,.2f}")
    print("="*95)

if __name__ == '__main__':
    run_winner_pattern_analysis()
