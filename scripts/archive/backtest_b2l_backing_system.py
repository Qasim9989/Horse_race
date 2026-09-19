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
    r"Connection Timeout=30;"
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

def get_place_terms(field_size):
    if field_size < 5:
        return (1, 1.0, 1)
    elif 5 <= field_size <= 7:
        return (2, 0.25, 3)
    elif 8 <= field_size <= 11:
        return (3, 0.20, 4)
    elif 12 <= field_size <= 15:
        return (3, 0.25, 4)
    else: # 16+ runners
        return (4, 0.25, 5)

def run_b2l_top4_backing_backtest():
    print("="*95)
    print("  B2L & EXTRA PLACE PROMO BACKING SYSTEM (TOP 4 DAILY QUALIFIERS | BSP >= 20.0)")
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

    # Point-in-time LTO features
    df['PrevDate'] = df.groupby('CleanHorse')['RaceDate'].shift(1)
    df['DSLR'] = (df['RaceDate'] - df['PrevDate']).dt.days.fillna(999)

    df['PrevPosInt'] = df.groupby('CleanHorse')['PosInt'].shift(1).fillna(999).astype(int)
    df['PrevWin'] = df['PrevPosInt'] == 1
    df['PrevSecond'] = df['PrevPosInt'] == 2
    df['PrevPlaced'] = df['PrevPosInt'] <= 3

    df['PrevComment'] = df.groupby('CleanHorse')['Comment'].shift(1).astype(str).str.lower()
    df['BadDisc'] = df['PrevComment'].str.contains('slowly away|dwelt|pulled hard|keen|hung|erratic', regex=True)

    df['ClaimVal'] = pd.to_numeric(df['JockeyClaim'], errors='coerce').fillna(0)

    # POSITIVE SCORE FOR HIGH-ODDS OUTSIDER BACKING SELECTION
    score = np.zeros(len(df))
    score += np.where(df['PrevWin'], 5, 0)
    score += np.where(df['PrevSecond'], 3, 0)
    score += np.where((df['PrevPosInt'] == 3) | (df['PrevPosInt'] == 4), 2, 0)
    score += np.where(df['DSLR'] <= 14, 3, 0)
    score += np.where(df['ClaimVal'] > 0, 2, 0)

    score -= np.where(df['DSLR'] > 60, 3, 0)
    score -= np.where(df['BadDisc'], 3, 0)

    df['Score'] = score

    df_hcap = df[df['IsHandicap']].copy()
    race_groups = df_hcap.groupby(['RaceDate', 'CourseName', 'RaceTime'])
    df_hcap['FieldSize'] = race_groups['CleanHorse'].transform('count')
    
    # Outsiders (Odds >= 20.00) in Handicaps >= 5 runners
    df_valid = df_hcap[(df_hcap['FieldSize'] >= 5) & (df_hcap['DecSP'].notna()) & (df_hcap['DecSP'] >= 20.00)].copy()

    # Rank highest scored outsiders per day
    df_valid = df_valid.sort_values(['RaceDate', 'Score', 'DecSP'], ascending=[True, False, False])
    df_valid['DailyRank'] = df_valid.groupby('RaceDate').cumcount() + 1
    
    # Filter Top 4 Daily Best Outsider Qualifiers
    top4_df = df_valid[df_valid['DailyRank'] <= 4].copy()

    # Place terms calculation
    terms = top4_df['FieldSize'].apply(get_place_terms)
    top4_df['StdPlaces'] = [t[0] for t in terms]
    top4_df['PlaceFrac'] = [t[1] for t in terms]
    top4_df['ExtraPlaces'] = [t[2] for t in terms]

    top4_df['IsStdPlace'] = top4_df['PosInt'] <= top4_df['StdPlaces']
    top4_df['IsExtraPlace'] = top4_df['PosInt'] <= top4_df['ExtraPlaces']

    # Extra Place Promo EW P&L (£1 EW = £2 Total Stake per bet)
    win_part = np.where(top4_df['IsWin'], (top4_df['DecSP'] - 1.0) * 0.95, -1.0)
    place_part_extra = np.where(top4_df['IsExtraPlace'], ((top4_df['DecSP'] - 1.0) * top4_df['PlaceFrac']) * 0.95, -1.0)
    top4_df['ExtraPlaceEW_PNL'] = win_part + place_part_extra

    top4_df['YearMonth'] = top4_df['RaceDate'].dt.strftime('%Y-%m')

    total_bets = len(top4_df)
    total_staked = total_bets * 2.0
    total_pnl = top4_df['ExtraPlaceEW_PNL'].sum()
    wins = top4_df['IsWin'].sum()
    extra_places = top4_df['IsExtraPlace'].sum()
    roi = (total_pnl / total_staked) * 100

    print("="*95)
    print("  TOP 4 DAILY HIGH-ODDS EXTRA PLACE PROMO BACKING RESULTS (2023 - 2026)")
    print("="*95)
    print(f"  - Total EW Bets:         {total_bets:,} (£1 EW = £2 stake per bet)")
    print(f"  - Wins:                  {wins} (Win SR: {(wins/total_bets)*100:.2f}%)")
    print(f"  - Extra Places Hit:      {extra_places:,} (Place SR: {(extra_places/total_bets)*100:.2f}%)")
    print(f"  - Total Staked:          £{total_staked:,.2f}")
    print(f"  - Net P&L:               +£{total_pnl:,.2f}")
    print(f"  - ROI %:                 +{roi:.2f}%")
    print("="*95)

    monthly = top4_df.groupby('YearMonth').agg(
        Total_Bets=('ExtraPlaceEW_PNL', 'count'),
        Wins=('IsWin', 'sum'),
        Extra_Places=('IsExtraPlace', 'sum'),
        Monthly_PNL=('ExtraPlaceEW_PNL', 'sum')
    ).reset_index()

    monthly['Total_Staked'] = monthly['Total_Bets'] * 2.0
    monthly['Monthly_ROI_%'] = round((monthly['Monthly_PNL'] / monthly['Total_Staked']) * 100, 2)
    monthly['Cum_PNL'] = monthly['Monthly_PNL'].cumsum()
    monthly['Peak_PNL'] = monthly['Cum_PNL'].cummax()
    monthly['Drawdown'] = monthly['Cum_PNL'] - monthly['Peak_PNL']

    print("\n" + "="*95)
    print("  MONTH-BY-MONTH B2L EXTRA PLACE BACKING PERFORMANCE (2023 - 2026)")
    print("="*95)
    print(monthly[['YearMonth', 'Total_Bets', 'Wins', 'Extra_Places', 'Monthly_PNL', 'Monthly_ROI_%', 'Cum_PNL', 'Drawdown']].to_string(index=False))
    print("="*95)

if __name__ == '__main__':
    run_b2l_top4_backing_backtest()
