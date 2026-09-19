"""
COMPREHENSIVE B2L & OUTSIDER REALISTIC STRESS TEST
===================================================
Models exact real-world betting terms:
1. Standard Industry Each-Way Terms based on RH_NoOfRunners:
   - 16+ Runners: 1/4 odds, 4 Places
   - 12-15 Runners: 1/4 odds, 3 Places
   - 8-11 Runners: 1/5 odds, 3 Places
   - 5-7 Runners: 1/4 odds, 2 Places
   - < 5 Runners: Win Only
2. Bookmaker Extra Place Terms (e.g. SkyBet/Bet365/PaddyPower):
   - 16+ Runners: 1/5 odds, 5 Places
   - 12-15 Runners: 1/5 odds, 4 Places
   - 8-11 Runners: 1/5 odds, 4 Places
3. Month-by-Month Stress Breakdown (like Lay System Audit)
4. Zero Lookahead (Strict LTO) & Zero Outcome Pre-filtering
"""
import sys, os, re, datetime, pyodbc, pandas as pd, numpy as np, warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

CONN = ("Driver={ODBC Driver 17 for SQL Server};"
        "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")
MIN_ODDS = 20.0
SDATA_START = "2015-12-19"

SPEED_WORDS = ["led","ran on","ran on well","ran on strongly","kept on","kept on well",
    "strong finish","headway","good headway","quickened","quickened well",
    "chased leaders","chased leader","pushed along","driven out","made all",
    "disputed lead","prominent","stayed on","stayed on well","rallied","finished well"]
DISC_WORDS  = ["slowly away","dwelt","pulled hard","keen","hung","erratic",
    "lost ground start","missed break","reared"]

print("\n" + "="*88)
print("  RIGOROUS REAL-WORLD STRESS TEST: B2L & OUTSIDERS (2016 - 2026)")
print("  Real Terms: Standard EW (Field-Based) & Extra Place Promos | 5% Comm")
print("="*88)

conn = pyodbc.connect(CONN)

sql = f"""
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
    WHERE R.RH_DateTime >= '{SDATA_START}'
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
    -- Strict LTO features:
    Prev.Pace AS LTO_Pace,
    Prev.DSLR AS LTO_DSLR,
    Prev.JockClaim AS LTO_JockClaim,
    Prev.Comment AS LTO_Comment,
    CASE WHEN Prev.ASL > 0 AND Prev.SL_Finish > 0 THEN (Prev.ASL - Prev.SL_Finish) END AS LTO_StrideDecay
FROM RaceSequence Curr
JOIN RaceSequence Prev 
    ON Prev.HIR_HNo = Curr.HIR_HNo 
   AND Prev.RunSeq = Curr.RunSeq - 1
WHERE Curr.BSP >= {MIN_ODDS}
  AND (Curr.RH_HandicapLimit IS NOT NULL OR LOWER(Curr.RH_Name) LIKE '%handicap%')
  AND Curr.FinPos IS NOT NULL
ORDER BY Curr.RH_DateTime ASC;
"""

print("Executing SQL query across all SData history...")
df = pd.read_sql(sql, conn)
conn.close()
print(f"Loaded {len(df):,} total handicap runners at BSP >= {MIN_ODDS}.")

# Scoring logic
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
    if pd.notna(sd) and sd >= 0.66: score -= 3
    if dslr > 120: score -= 1
    return score, is_fr, spd, bad

scores_info = [compute_scores(r) for _, r in df.iterrows()]
df['Score'] = [x[0] for x in scores_info]
df['IsFrontRunner'] = [x[1] for x in scores_info]
df['GoodSpeed'] = [x[2] for x in scores_info]
df['BadDisc'] = [x[3] for x in scores_info]

# Assign Tiers
df['Tier'] = 'No Bet'
df.loc[df['Score'] >= 7, 'Tier'] = 'WIN STAR (7+)'
df.loc[(df['Score'] >= 4) & (df['Score'] < 7), 'Tier'] = 'Tier 1 EW (4-6)'
df.loc[(df['Score'] >= 2) & (df['GoodSpeed']) & (df['Score'] < 4), 'Tier'] = 'Tier 2 B2L (2-3 + Speed)'

# Function to calculate exact place terms based on actual field size
def calc_pnl(row, promo=False, stake=1.0):
    bsp = row['BSP']
    pos = row['FinPos']
    field = row['FieldSize'] if pd.notna(row['FieldSize']) and row['FieldSize'] > 0 else 10
    
    half_stake = stake / 2.0
    
    # 1. Win Part PnL
    if pos == 1:
        win_pnl = (bsp - 1.0) * half_stake * 0.95  # 5% exchange commission on winnings
    else:
        win_pnl = -half_stake
        
    # 2. Place Part Terms
    if not promo:
        # Standard Industry Terms for Handicaps
        if field >= 16:
            places = 4
            fraction = 4.0 # 1/4 odds
        elif field >= 12:
            places = 3
            fraction = 4.0 # 1/4 odds
        elif field >= 8:
            places = 3
            fraction = 5.0 # 1/5 odds
        elif field >= 5:
            places = 2
            fraction = 4.0 # 1/4 odds
        else:
            places = 1
            fraction = 1.0
    else:
        # Extra Place Bookmaker Promotion Terms
        if field >= 16:
            places = 5
            fraction = 5.0 # 1/5 odds 5 places
        elif field >= 12:
            places = 4
            fraction = 5.0 # 1/5 odds 4 places
        elif field >= 8:
            places = 4
            fraction = 5.0 # 1/5 odds 4 places
        elif field >= 5:
            places = 3
            fraction = 5.0
        else:
            places = 1
            fraction = 1.0
            
    if pos <= places:
        plc_odds = (bsp - 1.0) / fraction
        plc_pnl = plc_odds * half_stake * 0.95 # net return
    else:
        plc_pnl = -half_stake
        
    return win_pnl + plc_pnl, (pos <= places)

# Calculate Standard EW PnL and Extra Place PnL for every row
df['Std_EW_PnL'], df['Std_Plc_Won'] = zip(*[calc_pnl(r, promo=False, stake=1.0) for _, r in df.iterrows()])
df['Promo_EW_PnL'], df['Promo_Plc_Won'] = zip(*[calc_pnl(r, promo=True, stake=1.0) for _, r in df.iterrows()])

def print_detailed_tier(sub, name):
    if sub.empty: return
    n = len(sub)
    wins = (sub['FinPos'] == 1).sum()
    std_places = sub['Std_Plc_Won'].sum()
    promo_places = sub['Promo_Plc_Won'].sum()
    
    std_pnl = sub['Std_EW_PnL'].sum()
    std_roi = std_pnl / n * 100
    
    promo_pnl = sub['Promo_EW_PnL'].sum()
    promo_roi = promo_pnl / n * 100
    
    # Profit factor (Std EW)
    gp = sub.loc[sub['Std_EW_PnL'] > 0, 'Std_EW_PnL'].sum()
    gl = abs(sub.loc[sub['Std_EW_PnL'] < 0, 'Std_EW_PnL'].sum())
    pf = gp / gl if gl > 0 else 99.0
    
    # Max drawdown (Std EW)
    cum = sub['Std_EW_PnL'].cumsum()
    max_dd = (cum - cum.cummax()).min()
    
    print(f"\n{'─'*88}")
    print(f" {name} | Total Bets: {n:,} | Avg Odds: {sub['BSP'].mean():.1f}")
    print(f"{'─'*88}")
    print(f" Win Strike Rate:            {wins/n*100:5.2f}% ({wins:,} winners)")
    print(f" Standard Place Rate:        {std_places/n*100:5.2f}% ({std_places:,} placed)")
    print(f" Extra Place Promo Rate:     {promo_places/n*100:5.2f}% ({promo_places:,} placed)")
    print(f" Standard EW P&L (£1 stake): £{std_pnl:+10.2f}  |  ROI: {std_roi:+6.2f}%  |  PF: {pf:4.2f}")
    print(f" Extra Place P&L (£1 stake): £{promo_pnl:+10.2f}  |  ROI: {promo_roi:+6.2f}%")
    print(f" Max Drawdown (Std EW):      £{max_dd:8.2f}")

print("\n" + "="*88)
print("  PERFORMANCE BY TIER WITH REALISTIC PLACE TERMS & 5% COMMISSION")
print("="*88)
print_detailed_tier(df[df['Tier'] == 'WIN STAR (7+)'], "TIER: WIN STAR (Score >= 7)")
print_detailed_tier(df[df['Tier'] == 'Tier 1 EW (4-6)'], "TIER: Tier 1 EW (Score 4 - 6)")
print_detailed_tier(df[df['Tier'] == 'Tier 2 B2L (2-3 + Speed)'], "TIER: Tier 2 B2L (Score 2 - 3 + Speed)")

all_sel = df[df['Tier'] != 'No Bet']
print_detailed_tier(all_sel, "TIER: ALL QUALIFYING SELECTIONS COMBINED")

# Month-by-month stress test (Like lay system audit)
print("\n" + "="*88)
print("  YEAR-BY-YEAR STRESS TEST WITH REALISTIC FIELD TERMS & EXTRA PLACES")
print("="*88)
print(f" {'Year':<6} | {'Bets':>6} | {'Wins':>5} | {'StdPlc':>6} | {'Std EW P&L':>11} | {'Std ROI%':>8} | {'ExtraPlc P&L':>12} | {'Promo ROI%':>10}")
print("─"*88)

for yr in sorted(all_sel['Yr'].unique()):
    yd = all_sel[all_sel['Yr'] == yr]
    n_y = len(yd)
    w_y = (yd['FinPos'] == 1).sum()
    p_y = yd['Std_Plc_Won'].sum()
    
    std_p = yd['Std_EW_PnL'].sum()
    promo_p = yd['Promo_EW_PnL'].sum()
    
    print(f" {yr:<6} | {n_y:>6} | {w_y:>5} | {p_y:>6} | £{std_p:>+10.2f} | {std_p/n_y*100:>+7.2f}% | £{promo_p:>+11.2f} | {promo_p/n_y*100:>+9.2f}%")

tot_n = len(all_sel)
tot_w = (all_sel['FinPos'] == 1).sum()
tot_p = all_sel['Std_Plc_Won'].sum()
tot_std_p = all_sel['Std_EW_PnL'].sum()
tot_promo_p = all_sel['Promo_EW_PnL'].sum()

print("─"*88)
print(f" {'TOTAL':<6} | {tot_n:>6} | {tot_w:>5} | {tot_p:>6} | £{tot_std_p:>+10.2f} | {tot_std_p/tot_n*100:>+7.2f}% | £{tot_promo_p:>+11.2f} | {tot_promo_p/tot_n*100:>+9.2f}%")
print("="*88)

# Calculate monthly winning rate
monthly = all_sel.groupby('YrMonth')['Std_EW_PnL'].sum()
pos_months = (monthly > 0).sum()
tot_months = len(monthly)
print(f"\n Monthly Win Rate (Std EW): {pos_months} / {tot_months} Profitable Months ({pos_months/tot_months*100:.1f}%)")

monthly_promo = all_sel.groupby('YrMonth')['Promo_EW_PnL'].sum()
pos_p_months = (monthly_promo > 0).sum()
print(f" Monthly Win Rate (Extra Place Promo): {pos_p_months} / {tot_months} Profitable Months ({pos_p_months/tot_months*100:.1f}%)")

csv_path = r"E:\Test\racing-form-system\reports\B2L_Realistic_Field_StressTest.csv"
all_sel.to_csv(csv_path, index=False)
print(f"\nSaved full realistic audit dataset to: {csv_path}\n")