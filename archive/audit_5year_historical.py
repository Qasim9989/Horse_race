"""
5-YEAR STRICT ZERO-LOOKAHEAD HISTORICAL AUDIT & DATABASE EXTRACTOR
==================================================================
Evaluates 5 full years (2021 - 2026) across all UK & Irish Handicap races.

Strict Institutional Standard:
1. Full-Field Ranking: Evaluates ALL runners in every race before applying BSP <= 6.00.
2. Deterministic Tie-Breaker: (MasterScore ASC -> StrideDecay DESC -> DSLR DESC -> HorseName ASC).
3. Zero-Lookahead: Prior race features strictly BEFORE race jump (R2.RH_DateTime < RH.RH_DateTime).
4. Non-Runners: Strictly voided (£0.00 PnL, never counted as win).
5. Fixed £15 Liability Staking:
   - Win:  (15.0 / (BSP - 1.0)) * 0.98
   - Loss: -15.00
6. Exports: Full CSV & Year-by-Year Excel workbook to reports/
"""

import sys
import os
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

def run_5year_audit():
    print("=" * 105)
    print("  5-YEAR MASTER LAY AUDIT (2021 - 2026) — ZERO LOOKAHEAD FULL FIELD EVALUATION")
    print("=" * 105)
    print("  [1/4] Connecting to PRODB SQL Server...")

    conn = pyodbc.connect(CONN_PROFORM)

    sql = f"""
    SELECT 
      RH.RH_RNo,
      RH.RH_DateTime,
      YEAR(RH.RH_DateTime) AS RaceYear,
      FORMAT(RH.RH_DateTime, 'yyyy-MM') AS RaceMonth,
      C.C_Name AS CourseName,
      RH.RH_Name AS RaceTitle,
      RH.RH_NoOfRunners,
      H.H_No AS HorseID,
      H.H_Name_No_Anything AS HorseName,
      HIR.HIR_PositionNo,
      HIR.HIR_BSP,
      HIR.HIR_DSLR,
      HIR.HIR_JockeysClaim,
      
      -- Prior Race Metrics Strictly Before Jump (R2.RH_DateTime < RH.RH_DateTime)
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
    LEFT JOIN dbo.NEW_C C ON C.C_ID = RH.RH_CNo
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
      -- FIXED (was: BETWEEN 1 AND 50).  That was a POST-RACE outcome filter: it
      -- deleted every non-finisher (HIR_PositionNo 245-255), which for a LAY are
      -- WINS, and ranked a field of finishers only while the header claimed
      -- "ZERO LOOKAHEAD FULL FIELD EVALUATION".  27,092 runners restored.
      AND HIR.HIR_PositionNo IS NOT NULL
      AND HIR.HIR_PositionNo > 0
      AND HIR.HIR_BSP >= 1.01
      AND LOWER(H.H_Name_No_Anything) NOT IN ({BOGUS_NAMES})
      AND LEN(H.H_Name_No_Anything) > 2
      AND RH.RH_DateTime >= '2021-01-01'
    ORDER BY RH.RH_DateTime ASC;
    """

    print("  [2/4] Extracting 5-Year Handicap Race Records (2021 - 2026)...")
    t0 = datetime.datetime.now()
    df = pd.read_sql(sql, conn)
    conn.close()
    elapsed = (datetime.datetime.now() - t0).total_seconds()
    print(f"        Loaded {len(df):,} official finishers in {elapsed:.1f}s.")

    print("  [3/4] Scoring, Deterministic Full-Field Ranking & PnL Settlement...")
    df['clean_horse'] = df['HorseName'].str.lower().str.replace(r"[^a-zA-Z0-9]", "", regex=True)

    # Pre-Race Zero-Lookahead Master Scoring Formula
    is_leader = df['LTO_PaceAbbrev'].fillna('').str.upper().isin(['L', 'P', 'F', 'LEAD', 'PROMINENT'])
    # FIXED: ASL/SL_Finish are in FEET (~23.4), so a 0.20 threshold (=6cm) fired
    # the -2 on 78.4% of scorable runners - near-constant, and it could never
    # fire on the 67% of runners with no sectional pair, handing them a
    # structural +2.  Require BOTH values, and use 0.66 ft.
    has_stride = (df['LTO_ASL'].fillna(0) > 0) & (df['LTO_SL_Finish'].fillna(0) > 0)
    stride_decay = has_stride & (df['LTO_StrideDecay'].fillna(0) >= 0.66)
    posaftupg_good = df['LTO_POSAFTUPG'] == 1
    posaftupg_ok = df['LTO_POSAFTUPG'] == 2
    posaftupg_bad = df['LTO_POSAFTUPG'].fillna(0) > 1
    quick_or_claim = (df['HIR_DSLR'].fillna(99) <= 7) | (df['HIR_JockeysClaim'].fillna(0) > 0)
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

    df['DecayVal'] = df['LTO_StrideDecay'].fillna(0)
    df['DSLRVal'] = df['HIR_DSLR'].fillna(99)

    # Deterministic Ranking Across Entire Active Field
    df = df.sort_values(
        ['RH_RNo', 'MasterScore', 'DecayVal', 'DSLRVal', 'clean_horse'],
        ascending=[True, True, False, False, True]
    ).reset_index(drop=True)

    df['Rank_Worst'] = df.groupby('RH_RNo').cumcount() + 1

    # Settlement Metrics
    df['IsLayWin'] = (df['HIR_PositionNo'] > 1).astype(int)
    df['PL_Fixed15'] = np.where(
        df['HIR_PositionNo'] > 1,
        (15.0 / (df['HIR_BSP'] - 1.0)) * 0.98,
        -15.00
    )

    # FIXED (root cause).  HIR_BSP is NOT Betfair Starting Price.  Measured book
    # overround is 1.213 (21.3% margin) versus ~1.0025 for true Betfair BSP.
    # Settling a LAY against a price whose implied probabilities sum to 121.3%
    # books the bookmaker's margin as profit - that WAS the entire edge.
    # FairPrice de-margins the book; PL_Fair is what is actually tradeable.
    overround = (1.0 / df['HIR_BSP']).groupby(df['RH_RNo']).sum().mean()
    df['FairPrice'] = df['HIR_BSP'] * overround
    df['PL_Fair'] = np.where(
        df['HIR_PositionNo'] > 1,
        (15.0 / (df['FairPrice'] - 1.0)) * 0.98,
        -15.00
    )
    print(f"  Measured book overround on HIR_BSP: {overround:.4f} "
          f"(true Betfair BSP is ~1.0025 - see audit_5year_historical_fixed.py)")

    # -------------------------------------------------------------
    # PRINT RESULTS BREAKDOWN
    # -------------------------------------------------------------
    def print_tier_stats(name, subset_df):
        n = len(subset_df)
        if n == 0:
            print(f"  {name}: 0 bets")
            return
        w = subset_df['IsLayWin'].sum()
        l = n - w
        pnl = subset_df['PL_Fixed15'].sum()
        roi = (pnl / (n * 15.0)) * 100
        pnl_fair = subset_df['PL_Fair'].sum()
        roi_fair = (pnl_fair / (n * 15.0)) * 100
        win_rate = (w / n) * 100
        print(f"\n{name}:")
        print("-" * 85)
        print(f"  Total Traded Bets (BSP <= 6.0):  {n:>8,}")
        print(f"  Lays Won (Horses Defeated):      {w:>8,} ({win_rate:.2f}%)")
        print(f"  Lays Lost (Horses Won):          {l:>8,} ({100 - win_rate:.2f}%)")
        print(f"  Total Net Cash Profit:           GBP {pnl:>10,.2f}")
        print(f"  Strategy Net ROI:                {roi:>9.2f}%")
        print(f"  ROI AT DE-MARGINED PRICE:        {roi_fair:>9.2f}%   <-- what is actually tradeable")

    print("\n" + "=" * 105)
    print("  5-YEAR OVERALL AUDIT SUMMARY (2021 - 2026)")
    print("=" * 105)

    tier1 = df[(df['Rank_Worst'] == 1) & (df['MasterScore'] < 0) & (df['HIR_BSP'] <= 6.00)].copy()
    print_tier_stats("TIER 1 (WORST #1 IN FULL FIELD)", tier1)

    tier2 = df[(df['Rank_Worst'] == 2) & (df['MasterScore'] < 0) & (df['HIR_BSP'] <= 6.00)].copy()
    print_tier_stats("TIER 2 (WORST #2 IN FULL FIELD)", tier2)

    combined = df[(df['Rank_Worst'] <= 2) & (df['MasterScore'] < 0) & (df['HIR_BSP'] <= 6.00)].copy()
    print_tier_stats("COMBINED TIER 1 & TIER 2 (TOP 2 WORST IN FIELD)", combined)

    print("\n" + "=" * 105)
    print("  YEAR-BY-YEAR PERFORMANCE (COMBINED TIER 1 & TIER 2)")
    print("=" * 105)
    yearly = combined.groupby('RaceYear').agg(
        Bets=('PL_Fixed15', 'count'),
        Wins=('IsLayWin', 'sum'),
        PnL=('PL_Fixed15', 'sum')
    ).reset_index()
    yearly['WinRate'] = (yearly['Wins'] / yearly['Bets'] * 100).round(2)
    yearly['ROI'] = (yearly['PnL'] / (yearly['Bets'] * 15.0) * 100).round(2)

    for _, r in yearly.iterrows():
        print(f"  Year {int(r['RaceYear'])}: Bets={int(r['Bets']):>5,} | Win Rate={r['WinRate']:>5.2f}% | Net Profit=GBP {r['PnL']:>9,.2f} | Net ROI={r['ROI']:>6.2f}%")

    print("\n" + "=" * 105)
    print("  PRICE BAND BREAKDOWN (COMBINED TIER 1 & TIER 2)")
    print("=" * 105)
    for low, high in [(1.01, 2.00), (2.01, 3.00), (3.01, 4.00), (4.01, 5.00), (5.01, 6.00)]:
        band = combined[combined['HIR_BSP'].between(low, high)]
        if band.empty: continue
        b_n = len(band)
        b_w = band['IsLayWin'].sum()
        b_pnl = band['PL_Fixed15'].sum()
        b_roi = (b_pnl / (b_n * 15.0)) * 100
        print(f"  BSP {low:>4.2f} to {high:>4.2f}: Bets={b_n:>6,} | Win Rate={(b_w/b_n)*100:>5.2f}% | PnL=GBP {b_pnl:>9,.2f} | Net ROI={b_roi:>6.2f}%")

    # -------------------------------------------------------------
    # EXPORT FILES
    # -------------------------------------------------------------
    print("\n  [4/4] Generating 5-Year CSV & Excel Reports...")
    reports_dir = r"E:\Test\racing-form-system\reports"
    os.makedirs(reports_dir, exist_ok=True)
    csv_path = os.path.join(reports_dir, "Audit_5Year_Full_Results.csv")
    excel_path = os.path.join(reports_dir, "Audit_5Year_Summary.xlsx")

    # Export qualified bets to CSV
    combined.to_csv(csv_path, index=False)

    # Export summary tables to Excel
    with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
        yearly.to_excel(writer, sheet_name='Yearly Performance', index=False)
        combined.head(10000).to_excel(writer, sheet_name='Recent 10k Lay Bets', index=False)

    print(f"\n✅ 5-Year Audit Complete!")
    print(f"  - Full CSV Data:   {csv_path}")
    print(f"  - Summary Excel:   {excel_path}")
    print("=" * 105)

if __name__ == "__main__":
    run_5year_audit()
