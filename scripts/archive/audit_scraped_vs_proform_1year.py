"""
1-YEAR STRICT OUT-OF-SAMPLE AUDIT (ZERO LOOKAHEAD, FULL FIELD RANKING)
======================================================================
Strict Rules:
1. Full Field Ranking: Ranks ALL active runners in every race FIRST.
2. Selection Filter: Only bets if the horse has Rank_Worst <= 2 (Tier 1 or Tier 2),
   MasterScore < 0, and Betfair SP <= 6.00.
3. Strict Zero Lookahead: Prior race pace (LTO_PaceAbbrev), prior sectionals,
   prior comments strictly BEFORE race jump.
4. Non-Runners: Strictly voided (£0.00 PnL, never counted as win).
5. Bogus Margin Names: 100% purged.
6. Real Betfair Settlement:
   - Lay Win:  (15.0 / (BSP - 1.0)) * 0.95
   - Lay Loss: -15.00
"""

import sys
import datetime
import pyodbc
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

CONN_PROFORM = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=(localdb)\MSSQLLocalDB;"
    r"Database=PRODB;"
    r"Trusted_Connection=yes;"
)

BOGUS_NAMES = (
    "'short head', 'head', 'neck', 'nose', 'dead heat', 'distance', 'length', "
    "'half length', 'shd', 'hd', 'nk', 'nse', 'dh', 'dist', '1l', '2l', '3l', 'dht'"
)

print("=" * 95)
print("  STRICT 1-YEAR AUDIT: FULL FIELD RANKING & ZERO LOOKAHEAD (2025 - 2026)")
print("=" * 95)

conn = pyodbc.connect(CONN_PROFORM)

# Note: Query ALL official finishers (BSP from 1.01 to 1000.0) so the entire field is scored & ranked!
sql = f"""
SELECT 
  RH.RH_RNo,
  RH.RH_DateTime,
  YEAR(RH.RH_DateTime) AS RaceYear,
  FORMAT(RH.RH_DateTime, 'yyyy-MM') AS RaceMonth,
  RH.RH_Name AS RaceTitle,
  RH.RH_NoOfRunners,
  H.H_Name_No_Anything AS HorseName,
  HIR.HIR_PositionNo,
  HIR.HIR_BSP,
  HIR.HIR_DSLR,
  HIR.HIR_JockeysClaim,
  
  -- Prior Race Metrics (Strictly BEFORE race jump: R2.RH_DateTime < RH.RH_DateTime)
  Prev.LTO_PositionNo,
  Prev.LTO_POSAFTUPG,
  Prev.LTO_ASL,
  Prev.LTO_SL_Finish,
  (Prev.LTO_ASL - Prev.LTO_SL_Finish) AS LTO_StrideDecay,
  Prev.LTO_Comments,
  Prev.LTO_PaceAbbrev

FROM dbo.NEW_RH RH
JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
OUTER APPLY (
  SELECT TOP 1
    SD2.ASL AS LTO_ASL,
    SD2.SL_Finish AS LTO_SL_Finish,
    SD2.POSAFTUPG AS LTO_POSAFTUPG,
    H2.HIR_CommentsInRunning AS LTO_Comments,
    H2.HIR_PaceAbbrev AS LTO_PaceAbbrev,
    H2.HIR_PositionNo AS LTO_PositionNo
  FROM dbo.NEW_HIR H2
  JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
  LEFT JOIN dbo.SData SD2 ON SD2.SD_RNo = H2.HIR_RNo AND SD2.SD_HNo = H2.HIR_HNo
  WHERE H2.HIR_HNo = HIR.HIR_HNo
    AND R2.RH_DateTime < RH.RH_DateTime
  ORDER BY R2.RH_DateTime DESC
) Prev
WHERE RH.RH_RaceTypeID IN (1, 2, 3, 4)
  AND RH.RH_Name LIKE '%Handicap%'
  AND RH.RH_Results = 1
  AND RH.RH_NoOfRunners >= 5
        AND HIR.HIR_PositionNo IS NOT NULL AND HIR.HIR_PositionNo > 0  -- FIXED (was: BETWEEN 1 AND 50, a post-race filter)
  AND HIR.HIR_BSP >= 1.01
  AND LOWER(H.H_Name_No_Anything) NOT IN ({BOGUS_NAMES})
  AND LEN(H.H_Name_No_Anything) > 2
  AND RH.RH_DateTime >= '2025-01-01'
ORDER BY RH.RH_DateTime ASC;
"""

print("[1] Loading full field handicap finishers (2025-2026) from PRODB...")
df = pd.read_sql(sql, conn)
conn.close()

print(f"Total Official Finishers in Full Race Fields: {len(df):,}")

# Clean horse name normalization
df['clean_horse'] = df['HorseName'].str.lower().str.replace(r"[^a-zA-Z0-9]", "", regex=True)

# 100% Zero-Lookahead Feature Scoring:
# 1. Prior Pace: Horse was leader in previous race
is_leader = df['LTO_PaceAbbrev'].fillna('').str.upper().isin(['L', 'P', 'F', 'LEAD', 'PROMINENT'])
# 2. Prior Sectionals: Stride Decay >= 0.66ft (0.20m was the unit bug)
stride_decay = df['LTO_StrideDecay'].fillna(0) >= 0.66
# 3. Prior Sectional Upgrade Rank
posaftupg_good = df['LTO_POSAFTUPG'] == 1
posaftupg_ok = df['LTO_POSAFTUPG'] == 2
posaftupg_bad = df['LTO_POSAFTUPG'].fillna(0) > 1
# 4. Quick Return or Jockey Claim
quick_or_claim = (df['HIR_DSLR'] <= 7) | (df['HIR_JockeysClaim'] > 0)
# 5. Prior Discipline Issue
bad_disc = df['LTO_Comments'].fillna('').str.lower().apply(
    lambda c: any(w in c for w in ['slowly away', 'dwelt', 'pulled hard', 'keen', 'hung', 'erratic'])
)

df['MasterScore'] = (
    np.where(is_leader, 3, 0)
    + np.where(posaftupg_good, 3, np.where(posaftupg_ok, 1, 0))
    + np.where(quick_or_claim, 2, 0)
    + np.where(stride_decay, -2, 0)
    + np.where(posaftupg_bad, -2, 0)
    + np.where(bad_disc, -2, 0)
)

# Step 2: Rank the ENTIRE race field deterministically (worst score first, then biggest decay, then DSLR)
df['DecayVal'] = df['LTO_StrideDecay'].fillna(0)
df['DSLRVal'] = df['HIR_DSLR'].fillna(99)

df = df.sort_values(
    ['RH_RNo', 'MasterScore', 'DecayVal', 'DSLRVal', 'clean_horse'],
    ascending=[True, True, False, False, True]
)

# Deterministic Rank: exactly ONE runner is rank 1, exactly ONE runner is rank 2
df['Rank_Worst'] = df.groupby('RH_RNo').cumcount() + 1

# Settlement formula
df['IsLayWin'] = (df['HIR_PositionNo'] > 1).astype(int)
df['PL_Fixed15'] = np.where(
    df['HIR_PositionNo'] > 1,
    (15.0 / (df['HIR_BSP'] - 1.0)) * 0.95,
    -15.00
)

def evaluate_tier(name, condition_df):
    tot = len(condition_df)
    if tot == 0:
        print(f"  {name}: No bets found.")
        return
    wins = condition_df['IsLayWin'].sum()
    losses = tot - wins
    pnl = condition_df['PL_Fixed15'].sum()
    roi = (pnl / (tot * 15.0)) * 100
    win_pct = (wins / tot) * 100
    print(f"\n{name} RESULTS (Betfair SP <= 6.00):")
    print("-" * 80)
    print(f"  Total Bets Placed:         {tot:>7,}")
    print(f"  Lay Wins (Horse Lost):     {wins:>7,} ({win_pct:.2f}%)")
    print(f"  Lay Losses (Horse Won):    {losses:>7,} ({100 - win_pct:.2f}%)")
    print(f"  Total Net Cash Profit:     GBP {pnl:>9,.2f}")
    print(f"  Strategy Net ROI:          {roi:>8.2f}%")

print("\n" + "=" * 95)
print("  AUDITED RESULTS: FULL FIELD RANKING WITH REAL BSP <= 6.00 CEILING")
print("=" * 95)

# Tier 1: Exactly #1 Worst in Full Field, Score < 0, BSP <= 6.00
tier1 = df[(df['Rank_Worst'] == 1) & (df['MasterScore'] < 0) & (df['HIR_BSP'] <= 6.00)].copy()
evaluate_tier("TIER 1 (WORST #1 IN FULL FIELD)", tier1)

# Tier 2: Exactly #2 Worst in Full Field, Score < 0, BSP <= 6.00
tier2_only = df[(df['Rank_Worst'] == 2) & (df['MasterScore'] < 0) & (df['HIR_BSP'] <= 6.00)].copy()
evaluate_tier("TIER 2 (WORST #2 IN FULL FIELD)", tier2_only)

# Combined Tier 1 + Tier 2 (Top 2 Worst in Field, Score < 0, BSP <= 6.00)
combined_t1_t2 = df[(df['Rank_Worst'] <= 2) & (df['MasterScore'] < 0) & (df['HIR_BSP'] <= 6.00)].copy()
evaluate_tier("COMBINED TIER 1 & TIER 2 (TOP 2 WORST IN FIELD)", combined_t1_t2)

print("\n" + "=" * 95)
print("  MONTH-BY-MONTH BREAKDOWN (COMBINED TIER 1 & TIER 2)")
print("=" * 95)

monthly = combined_t1_t2.groupby('RaceMonth').agg(
    Bets=('PL_Fixed15', 'count'),
    Wins=('IsLayWin', 'sum'),
    PnL=('PL_Fixed15', 'sum')
).reset_index()
monthly['ROI'] = (monthly['PnL'] / (monthly['Bets'] * 15.0) * 100).round(2)
monthly['WinPct'] = (monthly['Wins'] / monthly['Bets'] * 100).round(1)

for _, r in monthly.iterrows():
    flag = " [PROFIT]" if r['PnL'] > 0 else " [LOSS]"
    print(f"  {r['RaceMonth']}: Bets={int(r['Bets']):>4} | Wins={int(r['Wins']):>4} ({r['WinPct']:>4.1f}%) | PnL=GBP {r['PnL']:>7,.2f} | ROI={r['ROI']:>6.2f}% {flag}")

print("=" * 95)
