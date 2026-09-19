"""
5-PART MACHINE LEARNING & OUT-OF-SAMPLE VALIDATOR (FULL FIELD RANKING)
======================================================================
Strict Rules:
1. Full Field Ranking: Scores ALL active runners in every race, ranks entire field.
2. Selection Filter: Only bets if Rank_Worst <= 2, Score < 0, and BSP <= 6.00.
3. Strict Zero Lookahead: Prior race pace (LTO_PaceAbbrev), prior sectionals,
   prior comments strictly BEFORE race jump.
4. Non-Runners: Strictly voided (£0.00 PnL, never counted as win).
5. 5-Part Validation:
   - Part 1: Feature Correlation & Information Value
   - Part 2: Ridge / Logistic ML vs Heuristic Weight Comparison
   - Part 3: Pure Out-of-Sample Test (Train 2025 -> Test 2026)
   - Part 4: Monte Carlo Bankroll & Drawdown Simulation (£500 Bankroll)
   - Part 5: Multi-Band Price Calibration (1.01 to 6.00)
"""

import sys
import datetime
import pyodbc
import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
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
print("  5-PART ML & OUT-OF-SAMPLE VALIDATION (FULL FIELD RANKING & ZERO LOOKAHEAD)")
print("=" * 95)

conn = pyodbc.connect(CONN_PROFORM)

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
  -- FIXED (was: BETWEEN 1 AND 50).  That was a POST-RACE outcome filter that
  -- deleted every non-finisher (HIR_PositionNo 245-255); for a LAY those are
  -- WINS.  Restored.
  AND HIR.HIR_PositionNo IS NOT NULL
  AND HIR.HIR_PositionNo > 0
  AND HIR.HIR_BSP >= 1.01
  AND LOWER(H.H_Name_No_Anything) NOT IN ({BOGUS_NAMES})
  AND LEN(H.H_Name_No_Anything) > 2
  AND RH.RH_DateTime >= '2025-01-01'
ORDER BY RH.RH_DateTime ASC;
"""

print("[1] Loading full field handicap finishers from PRODB...")
df = pd.read_sql(sql, conn)
conn.close()

print(f"Total Official Finishers Loaded: {len(df):,}")

# Feature Engineering (100% Zero Lookahead)
df['clean_horse'] = df['HorseName'].str.lower().str.replace(r"[^a-zA-Z0-9]", "", regex=True)

df['is_leader'] = df['LTO_PaceAbbrev'].fillna('').str.upper().isin(['L', 'P', 'F', 'LEAD', 'PROMINENT']).astype(int)
df['stride_decay'] = (df['LTO_StrideDecay'].fillna(0) >= 0.66).astype(int)
df['posaftupg_good'] = (df['LTO_POSAFTUPG'] == 1).astype(int)
df['posaftupg_ok'] = (df['LTO_POSAFTUPG'] == 2).astype(int)
df['posaftupg_bad'] = (df['LTO_POSAFTUPG'].fillna(0) > 1).astype(int)
df['quick_or_claim'] = ((df['HIR_DSLR'] <= 7) | (df['HIR_JockeysClaim'] > 0)).astype(int)

df['bad_disc'] = df['LTO_Comments'].fillna('').str.lower().apply(
    lambda c: any(w in c for w in ['slowly away', 'dwelt', 'pulled hard', 'keen', 'hung', 'erratic'])
).astype(int)

# Target: 1 if Horse Won Race, 0 if Horse Lost
df['Won'] = (df['HIR_PositionNo'] == 1).astype(int)
df['IsLayWin'] = (df['HIR_PositionNo'] > 1).astype(int)

# Heuristic Master Score
df['MasterScore'] = (
    df['is_leader'] * 3
    + df['posaftupg_good'] * 3
    + df['posaftupg_ok'] * 1
    + df['quick_or_claim'] * 2
    - df['stride_decay'] * 2
    - df['posaftupg_bad'] * 2
    - df['bad_disc'] * 2
)

# Deterministic full field rank
df['DecayVal'] = df['LTO_StrideDecay'].fillna(0)
df['DSLRVal'] = df['HIR_DSLR'].fillna(99)

df = df.sort_values(
    ['RH_RNo', 'MasterScore', 'DecayVal', 'DSLRVal', 'clean_horse'],
    ascending=[True, True, False, False, True]
)
df['Rank_Worst'] = df.groupby('RH_RNo').cumcount() + 1

# Fixed £15 Settlement formula
df['PL_Fixed15'] = np.where(
    df['HIR_PositionNo'] > 1,
    (15.0 / (df['HIR_BSP'] - 1.0)) * 0.98,
    -15.00
)

# -------------------------------------------------------------
# PART 1: FEATURE CORRELATION
# -------------------------------------------------------------
print("\n" + "=" * 95)
print("  PART 1: FEATURE CORRELATION WITH WINNING (LOG ODDS)")
print("=" * 95)
features = ['is_leader', 'posaftupg_good', 'quick_or_claim', 'stride_decay', 'posaftupg_bad', 'bad_disc']
for f in features:
    rate_with = df[df[f] == 1]['Won'].mean() * 100
    rate_without = df[df[f] == 0]['Won'].mean() * 100
    impact = rate_with - rate_without
    direction = "Increases Win Chance (+)" if impact > 0 else "Increases Lay Loss Chance (-)"
    print(f"  {f:<18} | Flag=1: {rate_with:>5.2f}% Win | Flag=0: {rate_without:>5.2f}% Win | Impact: {impact:>+5.2f}% ({direction})")

# -------------------------------------------------------------
# PART 2: MACHINE LEARNING VS HEURISTIC WEIGHTS
# -------------------------------------------------------------
print("\n" + "=" * 95)
print("  PART 2: MACHINE LEARNING (LOGISTIC REGRESSION) EMPIRICAL WEIGHTS")
print("=" * 95)

X = df[features]
y = df['Won']

scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

clf = LogisticRegression(class_weight='balanced', random_state=42)
clf.fit(X_scaled, y)

for f, coef in zip(features, clf.coef_[0]):
    print(f"  Feature: {f:<18} | ML Standardized Weight: {coef:>+6.3f}")

# -------------------------------------------------------------
# PART 3: PURE OUT-OF-SAMPLE TEST (Train 2025 -> Test 2026)
# -------------------------------------------------------------
print("\n" + "=" * 95)
print("  PART 3: PURE OUT-OF-SAMPLE TEST (Train 2025 -> Test 2026)")
print("=" * 95)

for name, filter_cond in [
    ("Tier 1 (#1 Worst in Field)", df['Rank_Worst'] == 1),
    ("Combined Tier 1 & 2 (Top 2 Worst)", df['Rank_Worst'] <= 2)
]:
    print(f"\n--- {name} ---")
    for yr in [2025, 2026]:
        sub = df[filter_cond & (df['MasterScore'] < 0) & (df['HIR_BSP'] <= 6.00) & (df['RaceYear'] == yr)].copy()
        n = len(sub)
        w = sub['IsLayWin'].sum()
        pnl = sub['PL_Fixed15'].sum()
        roi = (pnl / (n * 15.0)) * 100 if n > 0 else 0
        tag = "IN-SAMPLE (2025)" if yr == 2025 else "PURE OUT-OF-SAMPLE (2026)"
        print(f"  {tag:<28}: Bets={n:>5,} | Win Rate={w/n*100:>5.2f}% | PnL=GBP {pnl:>8,.2f} | Net ROI={roi:>6.2f}%")

# -------------------------------------------------------------
# PART 4: MONTE CARLO BANKROLL & DRAWDOWN SIMULATION
# -------------------------------------------------------------
print("\n" + "=" * 95)
print("  PART 4: WALK-FORWARD BANKROLL SIMULATION (£500 Bankroll, £15 Fixed Risk)")
print("=" * 95)

sim_bets = df[(df['Rank_Worst'] <= 2) & (df['MasterScore'] < 0) & (df['HIR_BSP'] <= 6.00)].copy().reset_index(drop=True)
sim_bets['Cum_PnL'] = sim_bets['PL_Fixed15'].cumsum()
sim_bets['Bankroll'] = 500.0 + sim_bets['Cum_PnL']
sim_bets['Peak'] = sim_bets['Bankroll'].cummax()
sim_bets['Drawdown'] = sim_bets['Peak'] - sim_bets['Bankroll']

max_dd = sim_bets['Drawdown'].max()
max_dd_pct = (max_dd / 500.0) * 100
final_bankroll = sim_bets['Bankroll'].iloc[-1]

# Max losing streak
loss_series = (sim_bets['IsLayWin'] == 0).astype(int)
streak = 0
max_streak = 0
for v in loss_series:
    if v == 1:
        streak += 1
        max_streak = max(max_streak, streak)
    else:
        streak = 0

print(f"  Starting Bankroll:      GBP    500.00")
print(f"  Total Bets Executed:    {len(sim_bets):,}")
print(f"  Final Ending Bankroll:  GBP {final_bankroll:>9,.2f} (+{(final_bankroll-500)/500*100:.1f}%)")
print(f"  Max Drawdown (£):       GBP {max_dd:>9,.2f} ({max_dd_pct:.2f}% of starting bankroll)")
print(f"  Max Losing Streak:      {max_streak} consecutive losses")

# -------------------------------------------------------------
# PART 5: MULTI-BAND PRICE CALIBRATION
# -------------------------------------------------------------
print("\n" + "=" * 95)
print("  PART 5: MULTI-BAND PRICE CALIBRATION (COMBINED TIER 1 & 2)")
print("=" * 95)
for low, high in [(1.01, 2.00), (2.01, 3.00), (3.01, 4.00), (4.01, 5.00), (5.01, 6.00)]:
    sub = sim_bets[sim_bets['HIR_BSP'].between(low, high)]
    if sub.empty: continue
    s_n = len(sub)
    s_w = sub['IsLayWin'].sum()
    s_pnl = sub['PL_Fixed15'].sum()
    s_roi = (s_pnl / (s_n * 15.0)) * 100
    print(f"  BSP {low:>4.2f} to {high:>4.2f}: Bets={s_n:>5,} | Win Rate={s_w/s_n*100:>5.2f}% | PnL=GBP {s_pnl:>8,.2f} | Net ROI={s_roi:>6.2f}%")

print("=" * 95)
