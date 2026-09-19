"""
CUSTOM DATE HISTORICAL AUDITOR & CSV/EXCEL EXPORTER (DUAL AUDIT: LAY + B2L)
=============================================================================
Strict Audit Standards:
1. Exact Point-in-Time Prior Form: Evaluates prior race features strictly
   before each target runner's exact race timestamp (R2.RH_DateTime < RH.RH_DateTime).
2. Dual Strategy Audit:
   - Lay System: Evaluates low-odds lay targets (BSP <= 6.0) at fixed £15 liability.
   - B2L System: Evaluates high-odds outsider targets (BSP >= 20.0) on WIN, EW, and Extra Place.
3. Active Runner Ranking: Non-runners are filtered out BEFORE computing RankWorst / RankBest.
4. Deterministic Field Ranking & Clean Settlement against actual results.
"""

import sys
import os
import re
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

CONN_P = r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;"

BOGUS_NAMES = {
    'short head', 'head', 'neck', 'nose', 'dead heat', 'distance', 'length', 
    'half length', 'shd', 'hd', 'nk', 'nse', 'dh', 'dist', '1l', '2l', '3l', 'dht'
}

SPEED_WORDS = [
    "led", "led briefly", "ran on", "ran on well", "ran on strongly",
    "kept on", "kept on well", "strong finish", "headway", "good headway",
    "quickened", "quickened well", "chased leaders", "chased leader",
    "pushed along", "driven out", "made all", "disputed lead", "prominent",
    "stayed on", "stayed on well", "rallied", "finished well"
]

DISC_WORDS = [
    "slowly away", "dwelt", "pulled hard", "keen", "hung", "erratic",
    "lost ground start", "missed break", "reared"
]

def clean_horse_name(name):
    if not name: return ""
    name = re.sub(r"\s*\([A-Z]{2,4}\)$", "", str(name), flags=re.IGNORECASE).strip()
    return "".join(c for c in name.lower() if c.isalnum())

def audit_date(target_date_str):
    print("=" * 115)
    print(f"  HISTORICAL DATE DUAL AUDITOR (LAY + B2L): {target_date_str}")
    print("  Zero Lookahead (Strict Prior Race < Race Timestamp) | Non-Runners Excluded Before Ranking")
    print("=" * 115)

    conn_p = pyodbc.connect(CONN_P)

    # 1. Fetch complete race records with Point-in-Time Prior Run Features via OUTER APPLY
    sql_audit = f"""
    SELECT 
      RH.RH_RNo,
      CAST(RH.RH_DateTime AS DATE) AS RaceDate,
      FORMAT(RH.RH_DateTime, 'HH:mm') AS RaceTime,
      RH.RH_DateTime AS RaceDateTime,
      C.C_Name AS CourseName,
      RH.RH_Name AS RaceTitle,
      RH.RH_NoOfRunners AS FieldSize,
      H.H_No AS HorseID,
      H.H_Name_No_Anything AS HorseName,
      CAST(HIR.HIR_PositionNo AS VARCHAR) AS PosNo,
      HIR.HIR_PositionNo AS FinPosInt,
      HIR.HIR_BSP AS DecOdds,
      HIR.HIR_DSLR AS DSLR,
      HIR.HIR_JockeysClaim AS JockeyClaim,
      HIR.HIR_CommentsInRunning AS InRunningComment,

      -- EXACT POINT-IN-TIME PRIOR RACE FEATURES (R2.RH_DateTime < RH.RH_DateTime)
      Prev.LTO_DateTime,
      Prev.LTO_PaceAbbrev,
      Prev.LTO_Comments,
      Prev.LTO_ASL,
      Prev.LTO_SL_Finish,
      (Prev.LTO_ASL - Prev.LTO_SL_Finish) AS LTO_StrideDecay,
      Prev.LTO_POSAFTUPG

    FROM dbo.NEW_RH RH
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
    JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
    LEFT JOIN dbo.NEW_C C ON C.C_ID = RH.RH_CNo
    OUTER APPLY (
      SELECT TOP 1
        R2.RH_DateTime AS LTO_DateTime,
        H2.HIR_PaceAbbrev AS LTO_PaceAbbrev,
        H2.HIR_CommentsInRunning AS LTO_Comments,
        SD2.ASL AS LTO_ASL,
        SD2.SL_Finish AS LTO_SL_Finish,
        SD2.POSAFTUPG AS LTO_POSAFTUPG
      FROM dbo.NEW_HIR H2
      JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
      LEFT JOIN dbo.SData SD2 ON SD2.SD_RNo = H2.HIR_RNo AND SD2.SD_HNo = H2.HIR_HNo
      WHERE H2.HIR_HNo = HIR.HIR_HNo
        AND R2.RH_DateTime < RH.RH_DateTime -- STRICT POINT-IN-TIME CUTOFF
      ORDER BY R2.RH_DateTime DESC
    ) Prev
    WHERE CAST(RH.RH_DateTime AS DATE) = '{target_date_str}'
    ORDER BY RH.RH_DateTime, HIR.HIR_PositionNo;
    """

    df = pd.read_sql(sql_audit, conn_p)
    conn_p.close()

    if df.empty:
        print(f"\n❌ No race records found for date: {target_date_str} in database.")
        return

    # Filter out bogus margin names
    df = df[~df['HorseName'].str.lower().isin(BOGUS_NAMES) & (df['HorseName'].str.len() > 2)].copy()
    df['clean_horse'] = df['HorseName'].apply(clean_horse_name)

    # Clean PosNo and Non-Runner detection
    df['PosStr'] = df['PosNo'].fillna('').astype(str).str.strip().str.upper()
    df['IsNonRunner'] = df['PosStr'].isin(['NR', 'NON-RUNNER', 'NON RUNNER', 'WD', 'V'])
    df['IsWinner'] = (df['PosStr'].isin(['1', '1ST'])) & (~df['IsNonRunner'])

    # Parse Features
    is_leader = df['LTO_PaceAbbrev'].fillna('').str.upper().isin(['L', 'P', 'F', 'LEAD', 'PROMINENT'])
    stride_decay_high = df['LTO_StrideDecay'].fillna(0) >= 0.66
    posaftupg_good = df['LTO_POSAFTUPG'] == 1
    posaftupg_ok = df['LTO_POSAFTUPG'] == 2
    posaftupg_bad = df['LTO_POSAFTUPG'].fillna(0) > 1
    quick_or_claim = (df['DSLR'].fillna(99) <= 7) | (df['JockeyClaim'].fillna(0) > 0)

    lto_comments = df['LTO_Comments'].fillna('').str.lower()
    bad_disc = lto_comments.apply(lambda c: any(w in c for w in DISC_WORDS))
    good_speed = lto_comments.apply(lambda c: any(w in c for w in SPEED_WORDS))

    # Master Lay Score (Negative = Lay Candidate)
    df['MasterLayScore'] = (
        np.where(is_leader, 3, 0)
        + np.where(posaftupg_good, 3, np.where(posaftupg_ok, 1, 0))
        + np.where(quick_or_claim, 2, 0)
        + np.where(stride_decay_high, -2, 0)
        + np.where(posaftupg_bad, -2, 0)
        + np.where(bad_disc, -2, 0)
    )

    # B2L Value Score (Positive = Outsider Value Candidate)
    df['B2LScore'] = (
        np.where(is_leader, 3, 0)
        + np.where(good_speed, 2, 0)
        + np.where(quick_or_claim, 2, 0)
        + np.where(~bad_disc, 2, 0)
        + np.where(bad_disc, -2, 0)
        + np.where(stride_decay_high, -3, 0)
        + np.where(df['DSLR'].fillna(99) > 120, -1, 0)
    )

    df['DecayVal'] = df['LTO_StrideDecay'].fillna(0)
    df['DSLRVal'] = df['DSLR'].fillna(99)

    # Compute RankWorst (for Lay) and RankBest (for B2L) across active runners
    df['RankWorst'] = np.nan
    df['RankBest'] = np.nan

    active_indices = df[~df['IsNonRunner']].index
    
    # Sort for Lay (Worst first)
    active_sorted_lay = df.loc[active_indices].sort_values(
        ['RH_RNo', 'MasterLayScore', 'DecayVal', 'DSLRVal', 'clean_horse'],
        ascending=[True, True, False, False, True]
    )
    df.loc[active_sorted_lay.index, 'RankWorst'] = active_sorted_lay.groupby('RH_RNo').cumcount() + 1

    # Sort for B2L (Best first)
    active_sorted_b2l = df.loc[active_indices].sort_values(
        ['RH_RNo', 'B2LScore', 'DecayVal', 'DSLRVal', 'clean_horse'],
        ascending=[True, False, True, True, True]
    )
    df.loc[active_sorted_b2l.index, 'RankBest'] = active_sorted_b2l.groupby('RH_RNo').cumcount() + 1

    # Filter for Handicap races
    handicaps = df[df['RaceTitle'].str.contains('Handicap|H\'cap', case=False, na=False)]

    total_lays = 0; total_lay_wins = 0; total_lay_losses = 0; total_lay_pnl = 0.0
    total_b2l_bets = 0; total_b2l_wins = 0; total_b2l_places = 0; total_b2l_pnl = 0.0

    lay_csv_rows = []
    b2l_csv_rows = []
    all_csv_rows = []

    print("\n" + "=" * 115)
    print("  SECTION 1: MASTER LAY SYSTEM AUDIT (BSP <= 6.00, £15 Fixed Liability)")
    print("=" * 115)

    for r_no, group in handicaps.groupby('RH_RNo'):
        active_runners = group[~group['IsNonRunner']]
        if len(active_runners) < 5:
            continue

        first_r = group.iloc[0]
        print(f"\n[RACE] {first_r['RaceTime']} {first_r['CourseName']} - {first_r['RaceTitle']} ({len(active_runners)} Active Runners)")
        print("-" * 115)
        print(f"{'LAY STATUS':<18} | {'HORSE':<22} | {'SCORE':<5} | {'ODDS':<7} | {'RESULT':<10} | {'PNL (£15)':<10} | {'SIGNALS'}")
        print("-" * 115)

        for _, runner in group.sort_values(by=['IsNonRunner', 'RankWorst']).iterrows():
            is_nr = runner['IsNonRunner']
            rank_w = runner['RankWorst']
            is_tier1 = (rank_w == 1 and runner['MasterLayScore'] < 0) and not is_nr
            is_tier2 = (rank_w == 2 and runner['MasterLayScore'] < 0) and not is_nr
            tier_name = "TIER 1" if is_tier1 else ("TIER 2" if is_tier2 else "")
            is_target = is_tier1 or is_tier2

            odds = runner['DecOdds']
            flags = []
            if bad_disc.loc[runner.name]: flags.append("Discipline Issue")
            if runner['DecayVal'] >= 0.66: flags.append(f"Stride Decay ({runner['DecayVal']:.1f}ft)")
            if is_leader.loc[runner.name]: flags.append("Front Runner (+3)")
            if runner['DSLRVal'] <= 7: flags.append(f"Quick ({int(runner['DSLRVal'])}d)")
            if runner['JockeyClaim'] > 0: flags.append(f"Claim ({int(runner['JockeyClaim'])}lb)")
            reasons_str = ", ".join(flags) if flags else "Standard Form"

            pnl_val = 0.0
            pnl_str = "-"
            status_str = "Pass"
            bet_placed = False

            if is_nr:
                status_str = "⚪ NON-RUNNER"
            elif is_target:
                if odds is None or pd.isna(odds):
                    status_str = f"⚪ NO ODDS ({tier_name})"
                elif odds <= 6.0:
                    status_str = f"🔴 {tier_name} LAY" if is_tier1 else f"🟠 {tier_name} LAY"
                    bet_placed = True
                    total_lays += 1
                    if runner['IsWinner']:
                        total_lay_losses += 1
                        pnl_val = -15.00
                        pnl_str = "-GBP 15.00"
                    else:
                        total_lay_wins += 1
                        pnl_val = (15.0 / (odds - 1.0)) * 0.98
                        pnl_str = f"+GBP {pnl_val:.2f}"
                    total_lay_pnl += pnl_val
                else:
                    status_str = f"⚪ SKIP {tier_name} (>6)"

            pos_display = "NON-RUNNER" if is_nr else (f"{runner['PosNo']} (WON)" if runner['IsWinner'] else f"{runner['PosNo']} (LOST)")
            odds_disp = f"{runner['DecOdds']:.2f}" if pd.notna(runner['DecOdds']) else "N/A"
            print(f"{status_str:<18} | {runner['HorseName']:<22} | {runner['MasterLayScore']:>4}  | {odds_disp:<7} | {pos_display:<10} | {pnl_str:<10} | {reasons_str}")

            all_csv_rows.append({
                'Date': target_date_str,
                'Time': runner['RaceTime'],
                'Course': runner['CourseName'],
                'RaceTitle': runner['RaceTitle'],
                'HorseName': runner['HorseName'],
                'LayTier': tier_name if is_target else "None",
                'LayScore': runner['MasterLayScore'],
                'B2LScore': runner['B2LScore'],
                'DecOdds': runner['DecOdds'],
                'PosNo': runner['PosNo'],
                'IsWinner': runner['IsWinner'],
                'IsNonRunner': runner['IsNonRunner'],
                'LayPnL_15Liab': round(pnl_val, 2) if bet_placed else 0.0,
                'Signals': reasons_str
            })

            if bet_placed:
                lay_csv_rows.append({
                    'Date': target_date_str,
                    'Time': runner['RaceTime'],
                    'Course': runner['CourseName'],
                    'HorseName': runner['HorseName'],
                    'Tier': tier_name,
                    'BSP': runner['DecOdds'],
                    'Result': pos_display,
                    'PnL_15Liab': round(pnl_val, 2),
                    'Signals': reasons_str
                })

    # SECTION 2: B2L AUDIT
    print("\n" + "=" * 115)
    print("  SECTION 2: B2L HIGH-ODDS OUTSIDER VALUE AUDIT (BSP >= 20.0, £1 EW Stake)")
    print("=" * 115)
    print(f"{'B2L TIER':<18} | {'TIME & COURSE':<20} | {'HORSE':<22} | {'SCORE':<5} | {'BSP':<7} | {'POS':<6} | {'EW PNL (£1)':<12} | {'SIGNALS'}")
    print("-" * 115)

    for r_no, group in handicaps.groupby('RH_RNo'):
        active_runners = group[~group['IsNonRunner']]
        if len(active_runners) < 5: continue
        field_sz = len(active_runners)

        for _, runner in group.sort_values(by=['IsNonRunner', 'RankBest']).iterrows():
            if runner['IsNonRunner']: continue
            rank_b = runner['RankBest']
            sc = runner['B2LScore']
            odds = runner['DecOdds']
            if odds is None or pd.isna(odds) or odds < 20.0: continue

            is_win_star = (rank_b <= 2 and sc >= 7)
            is_t1 = (rank_b <= 2 and sc >= 4 and not is_win_star)
            is_t2 = (rank_b <= 3 and sc >= 2 and good_speed.loc[runner.name] and not is_win_star and not is_t1)

            if not (is_win_star or is_t1 or is_t2): continue

            tier_label = "*** WIN STAR ***" if is_win_star else ("Tier 1 EW" if is_t1 else "Tier 2 B2L")
            total_b2l_bets += 1

            pos = runner['FinPosInt'] if pd.notna(runner['FinPosInt']) else 99
            is_win = (pos == 1)
            # Standard EW place terms based on field size
            plc_num = 4 if field_sz >= 16 else (3 if field_sz >= 8 else 2)
            fraction = 4.0 if field_sz >= 12 else 5.0

            is_place = (pos <= plc_num)
            if is_win: total_b2l_wins += 1
            if is_place: total_b2l_places += 1

            # PnL £1 EW (£0.50 win, £0.50 place)
            win_ret = (odds - 1.0) * 0.5 * 0.98 if is_win else -0.5
            plc_ret = ((odds - 1.0) / fraction) * 0.5 * 0.98 if is_place else -0.5
            ew_pnl = win_ret + plc_ret
            total_b2l_pnl += ew_pnl

            flags = []
            if is_leader.loc[runner.name]: flags.append("FrontRunner")
            if good_speed.loc[runner.name]: flags.append("SpeedComment")
            if runner['DecayVal'] >= 0.66: flags.append(f"Decay({runner['DecayVal']:.1f}ft)")
            signals_str = ", ".join(flags) if flags else "Form Score"

            tc = f"{runner['RaceTime']} {runner['CourseName'][:12]}"
            print(f"{tier_label:<18} | {tc:<20} | {runner['HorseName']:<22} | {sc:>4}  | {odds:<7.1f} | {pos:<6} | £{ew_pnl:>+9.2f}  | {signals_str}")

            b2l_csv_rows.append({
                'Date': target_date_str,
                'Time': runner['RaceTime'],
                'Course': runner['CourseName'],
                'HorseName': runner['HorseName'],
                'Tier': tier_label,
                'B2LScore': sc,
                'BSP': odds,
                'FinPos': pos,
                'IsWin': is_win,
                'IsPlace': is_place,
                'EWPnL_1Stake': round(ew_pnl, 2),
                'Signals': signals_str
            })

    # Summary
    print("\n" + "=" * 115)
    print(f"  DUAL AUDIT SUMMARY ({target_date_str}):")
    print("-" * 115)
    lay_roi = (total_lay_pnl / (total_lays * 15) * 100) if total_lays > 0 else 0
    print(f"  [LAY SYSTEM] Matched Bets (BSP <= 6.0):  {total_lays} | Won: {total_lay_wins} | Lost: {total_lay_losses} | Net P&L: GBP {total_lay_pnl:+.2f} | ROI: {lay_roi:+.2f}%")
    
    b2l_roi = (total_b2l_pnl / total_b2l_bets * 100) if total_b2l_bets > 0 else 0
    print(f"  [B2L SYSTEM] High-Odds Bets (BSP >= 20.0): {total_b2l_bets} | Wins: {total_b2l_wins} | Places: {total_b2l_places} | Net P&L: GBP {total_b2l_pnl:+.2f} | ROI: {b2l_roi:+.2f}%")
    print("=" * 115)

    # Export to Excel
    reports_dir = r"E:\Test\racing-form-system\reports"
    os.makedirs(reports_dir, exist_ok=True)
    excel_path = os.path.join(reports_dir, f"Audit_Date_{target_date_str}.xlsx")
    csv_path = os.path.join(reports_dir, f"Audit_Date_{target_date_str}.csv")

    if all_csv_rows:
        pd.DataFrame(all_csv_rows).to_csv(csv_path, index=False)
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            if lay_csv_rows:
                pd.DataFrame(lay_csv_rows).to_excel(writer, sheet_name='Lay Bets Placed', index=False)
            if b2l_csv_rows:
                pd.DataFrame(b2l_csv_rows).to_excel(writer, sheet_name='B2L High Odds (20+)', index=False)
            pd.DataFrame(all_csv_rows).to_excel(writer, sheet_name='All Scored Runners', index=False)

        print(f"\n📁 Reports Saved:")
        print(f"  - CSV:   {csv_path}")
        print(f"  - Excel: {excel_path}")

if __name__ == "__main__":
    target_d = sys.argv[1].strip() if len(sys.argv) > 1 else (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    audit_date(target_d)
