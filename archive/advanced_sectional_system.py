import sys
import os
import datetime
import pyodbc
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

CONN_PROFORM = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=.\PROFORM_RACING;"
    r"Database=PRODB;"
    r"Trusted_Connection=yes;"
)

def check_pace_meltdown(row):
    # Lay System: Early Burn and Fade
    fin_pos = row.get('FinPos', 0)
    pos_1 = row.get('Pos1', 0)
    std1 = row.get('STDiff1', 0)
    std2 = row.get('STDiff2', 0)
    std3 = row.get('STDiff3', 0)
    
    if pd.isna(fin_pos) or pd.isna(pos_1): return False
    if pd.isna(std1) or pd.isna(std2) or pd.isna(std3): return False
    
    # Needs to be 4th or worse
    if fin_pos < 4: return False
    
    # Position dropped by 3+ places (e.g. 1st to 4th)
    if fin_pos - pos_1 < 3: return False
    
    # Sum of first 3 diffs < -1.5 seconds (Faster than Par)
    if (std1 + std2 + std3) > -1.5: return False
    
    return True

def check_hidden_finisher(row):
    # Back System: Late Surge
    fin_pos = row.get('FinPos', 0)
    pos_1 = row.get('Pos1', 0)
    fsp_diff = row.get('FSPDiff', 0)
    
    if pd.isna(fin_pos) or pd.isna(pos_1) or pd.isna(fsp_diff): return False
    
    # Needs to be 4th or worse (ignored by public)
    if fin_pos < 4: return False
    
    # Started 5th or worse (Held up / bad start)
    if pos_1 < 5: return False
    
    # Finished > 1.5% faster than Par
    if fsp_diff <= 1.5: return False
    
    return True

def run_system(target_date):
    print(f"Running DB-Only Advanced Sectional System for {target_date}...\n")
    
    # Unified SQL Query combining today's declarations directly with their LTO SData
    sql = f"""
    WITH TargetRaces AS (
        SELECT 
            R.RH_RNo AS TargetRaceId,
            R.RH_Name AS RaceTitle,
            R.RH_DateTime AS TargetDateTime,
            C.C_Name AS Course,
            H.H_Name_No_Anything AS HorseName,
            HIR.HIR_HNo AS HorseId,
            HIR.HIR_BSP AS BSP
        FROM dbo.NEW_RH R
        JOIN dbo.NEW_C C ON C.C_ID = R.RH_CNo
        JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = R.RH_RNo
        JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
        WHERE CAST(R.RH_DateTime AS DATE) = '{target_date}'
          AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
    ),
    HistoricalRuns AS (
        SELECT 
            T.TargetRaceId,
            T.HorseId,
            T.Course,
            T.TargetDateTime,
            T.HorseName,
            T.BSP,
            PastR.RH_RNo AS LTORaceId,
            PastR.RH_DateTime AS LTODateTime,
            PastHIR.HIR_PositionNo AS FinPos,
            SD.SPOS_1 AS Pos1,
            SD.STDIFF_1 AS STDiff1,
            SD.STDIFF_2 AS STDiff2,
            SD.STDIFF_3 AS STDiff3,
            SD.FSPDIFF_Finish AS FSPDiff,
            ROW_NUMBER() OVER(PARTITION BY T.HorseId, T.TargetRaceId ORDER BY PastR.RH_DateTime DESC) as run_rn
        FROM TargetRaces T
        JOIN dbo.NEW_HIR PastHIR ON PastHIR.HIR_HNo = T.HorseId
        JOIN dbo.NEW_RH PastR ON PastR.RH_RNo = PastHIR.HIR_RNo
        JOIN dbo.SData SD ON SD.SD_RNo = PastHIR.HIR_RNo AND SD.SD_HNo = PastHIR.HIR_HNo
        WHERE PastR.RH_DateTime < T.TargetDateTime
          AND PastR.RH_RNo <> T.TargetRaceId
    )
    SELECT * FROM HistoricalRuns WHERE run_rn = 1;
    """
    
    conn = pyodbc.connect(CONN_PROFORM)
    df = pd.read_sql(sql, conn)
    conn.close()
    
    if df.empty:
        print(f"No qualifying races or SData found for {target_date}.")
        return
        
    df['Is_Meltdown'] = df.apply(check_pace_meltdown, axis=1)
    df['Is_Finisher'] = df.apply(check_hidden_finisher, axis=1)
    
    num_handicaps = df['TargetRaceId'].nunique()
    num_runners_with_sdata = len(df)
    num_meltdowns = df['Is_Meltdown'].sum()
    num_finishers = df['Is_Finisher'].sum()
    
    print(f"--- VALIDATION AUDIT ({target_date}) ---")
    print(f"Handicap Races: {num_handicaps}")
    print(f"Runners w/ LTO SData: {num_runners_with_sdata}")
    print(f"Pace Meltdown Qualifiers: {num_meltdowns}")
    print(f"Hidden Finisher Qualifiers: {num_finishers}")
    print("-" * 35)
    
    out_dir = r"E:\Test\racing-form-system\reports"
    os.makedirs(out_dir, exist_ok=True)
    
    # Save Full Audit
    audit_path = os.path.join(out_dir, f"AdvancedSectional_AUDIT_{target_date}.csv")
    df.to_csv(audit_path, index=False)
    
    qualifiers = []
    
    for _, row in df.iterrows():
        bsp = float(row['BSP']) if pd.notna(row['BSP']) else 0
        
        time_str = pd.to_datetime(row['TargetDateTime']).strftime('%H:%M')
        
        if row['Is_Meltdown'] and (1.50 <= bsp <= 6.00):
            qualifiers.append({
                'Time': f"{time_str} {row['Course'].title()}",
                'Horse': row['HorseName'],
                'BSP': bsp,
                'System': 'PACE MELTDOWN',
                'Instruction': 'LAY',
                'LTO_Summary': f"Led {int(row['Pos1'])} faded to {int(row['FinPos'])}. Early Par Diff: {row['STDiff1']+row['STDiff2']+row['STDiff3']:.2f}s"
            })
            
        if row['Is_Finisher']:
            qualifiers.append({
                'Time': f"{time_str} {row['Course'].title()}",
                'Horse': row['HorseName'],
                'BSP': bsp,
                'System': 'HIDDEN FINISHER',
                'Instruction': 'BACK',
                'LTO_Summary': f"Dropped to {int(row['Pos1'])} finished {int(row['FinPos'])}. Final Pct vs Par: +{row['FSPDiff']:.2f}%"
            })
            
    if qualifiers:
        df_q = pd.DataFrame(qualifiers)
        print("\n--- SYSTEM SELECTIONS ---")
        print(df_q.to_string(index=False))
        out_path = os.path.join(out_dir, f"AdvancedSectional_System_{target_date}.csv")
        df_q.to_csv(out_path, index=False)
        print(f"\nSelections saved to {out_path}")
        print(f"Full Validation Audit saved to {audit_path}")
    else:
        print("No qualifiers found matching Odds requirements.")
        print(f"Full Validation Audit saved to {audit_path}")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y-%m-%d")
    run_system(target)
