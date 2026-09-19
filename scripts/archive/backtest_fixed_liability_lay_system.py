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

def run_backtest_fixed_liability():
    print("="*95)
    print("  FIXED LIABILITY (£15 PER LAY BET) SYSTEM BACKTEST (2023 - 2026)")
    print("="*95)

    conn = pyodbc.connect(CONN_STR)
    
    print("\n[1/4] Loading runner data from RACINGTV_2023_2026...")
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
        iq.StrideLength,
        iq.TopSpeed,
        iq.FinishingSpeedPct
    FROM dbo.Scraped_Results r
    LEFT JOIN dbo.Scraped_RaceIQ iq 
      ON r.RaceDate = iq.RaceDate AND r.CourseName = iq.CourseName AND r.HorseName = iq.HorseName
    ORDER BY r.RaceDate ASC, r.CourseName, r.RaceTime;
    """
    df = pd.read_sql(sql, conn)
    conn.close()

    df['RaceDate'] = pd.to_datetime(df['RaceDate'])
    df['RaceDateTime'] = pd.to_datetime(
        df['RaceDate'].dt.strftime('%Y-%m-%d') + ' ' + df['RaceTime'].astype(str),
        errors='coerce'
    )
    df['DecSP'] = df['BSP'].apply(lambda x: float(x) if (pd.notna(x) and str(x).replace('.','').isdigit() and float(x) > 1.0) else None)
    df['DecSP'] = df['DecSP'].fillna(df['SP'].apply(parse_sp))
    
    df['IsWin'] = df['PosNo'].astype(str).str.strip().str.lower().isin(['1', '1st'])
    df['IsHandicap'] = df['RaceTitle'].astype(str).str.contains('h\'cap|handicap|hcap', case=False, na=False)

    df['CleanHorse'] = df['HorseName'].astype(str).str.lower().str.replace(r'[^a-z0-9]', '', regex=True)

    df = df.sort_values(['CleanHorse', 'RaceDateTime']).reset_index(drop=True)

    df['PrevDate'] = df.groupby('CleanHorse')['RaceDate'].shift(1)
    df['DSLR'] = (df['RaceDate'] - df['PrevDate']).dt.days.fillna(99)

    df['PrevPos'] = df.groupby('CleanHorse')['PosNo'].shift(1).astype(str).str.strip().str.lower()
    df['PrevWin'] = df['PrevPos'].isin(['1', '1st'])
    df['PrevSecond'] = df['PrevPos'].isin(['2', '2nd'])

    df['PrevComment'] = df.groupby('CleanHorse')['Comment'].shift(1).astype(str).str.lower()
    df['BadDisc'] = df['PrevComment'].str.contains('slowly away|dwelt|pulled hard|keen|hung|erratic', regex=True)

    df['PrevFSP'] = pd.to_numeric(df.groupby('CleanHorse')['FinishingSpeedPct'].shift(1), errors='coerce')
    df['StrideDecay'] = df['PrevFSP'].apply(lambda x: 1 if (pd.notna(x) and x < 97.5) else 0)

    df['ClaimVal'] = pd.to_numeric(df['JockeyClaim'], errors='coerce').fillna(0)

    score = np.zeros(len(df))
    score += np.where(df['PrevWin'], 3, 0)
    score += np.where(df['PrevSecond'], 1, 0)
    score += np.where((df['DSLR'] <= 7) | (df['ClaimVal'] > 0), 2, 0)

    score -= np.where(df['BadDisc'], 2, 0)
    score -= np.where((~df['PrevWin']) & (~df['PrevSecond']), 2, 0)
    score -= np.where(df['StrideDecay'] == 1, 2, 0)

    df['Score'] = score

    df_hcap = df[df['IsHandicap']].copy()
    race_groups = df_hcap.groupby(['RaceDateTime', 'CourseName'])
    df_hcap['FieldSize'] = race_groups['CleanHorse'].transform('count')
    df_valid = df_hcap[df_hcap['FieldSize'] >= 5].copy()

    df_valid = df_valid.sort_values(
        ['RaceDateTime', 'CourseName', 'Score', 'DSLR', 'CleanHorse'],
        ascending=[True, True, True, False, True]
    ).reset_index(drop=True)
    df_valid['RankWorst'] = df_valid.groupby(['RaceDateTime', 'CourseName']).cumcount() + 1

    # Lay Target: documented rule is BSP <= 6.0, not <= 20.0.
    df_valid['IsLayTarget'] = (df_valid['RankWorst'] <= 2) & (df_valid['Score'] < 0) & (df_valid['DecSP'].notna()) & (df_valid['DecSP'] <= 6.0)

    qualifiers = df_valid[df_valid['IsLayTarget']].copy()

    # Fixed Liability Staking Model (£15 Liability per bet)
    FIXED_LIABILITY = 15.00
    
    # Stake = Fixed Liability / (Odds - 1)
    qualifiers['Stake'] = FIXED_LIABILITY / (qualifiers['DecSP'] - 1.0)
    
    # If Loss (Horse Wins): Loss = -FIXED_LIABILITY (-£15.00)
    # If Win for Lay (Horse Loses): Profit = Stake * 0.95 (5% commission)
    qualifiers['LayPNL'] = np.where(qualifiers['IsWin'], -FIXED_LIABILITY, qualifiers['Stake'] * 0.95)

    qualifiers['YearMonth'] = qualifiers['RaceDate'].dt.strftime('%Y-%m')

    monthly = qualifiers.groupby('YearMonth').agg(
        Total_Lays=('LayPNL', 'count'),
        Winners_Layed=('IsWin', 'sum'),
        Total_Liability=('LayPNL', lambda s: len(s) * FIXED_LIABILITY),
        Monthly_PNL=('LayPNL', 'sum')
    ).reset_index()

    monthly['Success_Rate_%'] = round((1 - (monthly['Winners_Layed'] / monthly['Total_Lays'])) * 100, 2)
    monthly['Cum_PNL'] = monthly['Monthly_PNL'].cumsum()
    monthly['Peak_PNL'] = monthly['Cum_PNL'].cummax()
    monthly['Drawdown'] = monthly['Cum_PNL'] - monthly['Peak_PNL']

    print("="*95)
    print("  MONTH-BY-MONTH FIXED LIABILITY (£15 PER BET) LAY SYSTEM PERFORMANCE")
    print("="*95)
    print(monthly[['YearMonth', 'Total_Lays', 'Winners_Layed', 'Success_Rate_%', 'Monthly_PNL', 'Cum_PNL', 'Drawdown']].to_string(index=False))
    print("="*95)

    total_lays = len(qualifiers)
    total_pnl = qualifiers['LayPNL'].sum()
    total_wins = qualifiers['IsWin'].sum()
    lay_success_rate = (1 - (total_wins / total_lays)) * 100
    max_dd = monthly['Drawdown'].min()
    total_liability = total_lays * FIXED_LIABILITY
    roi = (total_pnl / total_liability) * 100

    print(f"\n  OVERALL FIXED LIABILITY (£15 PER BET) SUMMARY:")
    print(f"  - Total Lay Bets:       {total_lays:,}")
    print(f"  - Lay Success Rate:     {lay_success_rate:.2f}% ({total_lays - total_wins:,} Losers / {total_wins:,} Winners)")
    print(f"  - Total Liability Risked: £{total_liability:,.2f}")
    print(f"  - Total Net P&L:        +£{total_pnl:,.2f} (@ £15 fixed liability / 5% comm)")
    print(f"  - Strategy ROI on Liability: +{roi:.2f}%")
    print(f"  - Maximum Drawdown:     £{max_dd:,.2f}")
    print("="*95)

if __name__ == '__main__':
    run_backtest_fixed_liability()
