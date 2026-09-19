import datetime
import os
import sys
import warnings

import pandas as pd
import pyodbc

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

def export_daily_card(target_date):
    print(f"Exporting Daily Race Card with SData for {target_date}...")

    sql = f"""
    SELECT
        R.RH_DateTime AS RaceDateTime,
        C.C_Name AS Course,
        R.RH_Name AS RaceTitle,
        R.RH_Exact_Race_Distance AS Distance,
        R.RH_GoingFull AS Going,
        H.H_Name_No_Anything AS Horse,
        J.J_Name AS Jockey,
        T.T_Name AS Trainer,
        HIR.HIR_CardNo AS CardNo,
        HIR.HIR_Drawn AS Stall,
        HIR.HIR_Pounds AS WeightLbs,
        HIR.HIR_BSP AS BSP,
        HIR.HIR_PositionNo AS FinishPos,
        HIR.HIR_PaceAbbrev AS Pace,
        HIR.HIR_CommentsInRunning AS InRunningComment,
        SD.*
    FROM dbo.NEW_RH R
    JOIN dbo.NEW_C C ON C.C_ID = R.RH_CNo
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = R.RH_RNo
    JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
    LEFT JOIN dbo.NEW_T T ON T.T_No = HIR.HIR_TNo
    LEFT JOIN dbo.NEW_J J ON J.J_No = HIR.HIR_JNo
    JOIN dbo.SData SD ON SD.SD_RNo = HIR.HIR_RNo AND SD.SD_HNo = HIR.HIR_HNo
    WHERE CAST(R.RH_DateTime AS DATE) = '{target_date}'
    ORDER BY R.RH_DateTime, HIR.HIR_PositionNo
    """

    try:
        conn = pyodbc.connect(CONN_PROFORM)
        df = pd.read_sql(sql, conn)
        conn.close()
    except Exception as e:
        print(f"Database error: {e}")
        return

    if df.empty:
        print(f"No races or runners found for {target_date}.")
        return

    if 'SD_RNo' in df.columns: df.drop(columns=['SD_RNo'], inplace=True)
    if 'SD_HNo' in df.columns: df.drop(columns=['SD_HNo'], inplace=True)

    # Rename abbreviations to readable english
    import re
    def rename_col(c):
        # Specific known abbreviations
        mapping = {
            'MAXUPG': 'Max_Upgrade', 'MAXUPGRK': 'Max_Upgrade_Rank',
            'LBSAFTUPG': 'Lbs_After_Upgrade', 'LENGTHSAFTUPG': 'Lengths_After_Upgrade',
            'CHGLENGTHS': 'Change_In_Lengths', 'POSAFTUPG': 'Position_After_Upgrade',
            'CHGPOS': 'Change_In_Position', 'TS': 'Top_Speed', 'TSRK': 'Top_Speed_Rank',
            'ASL': 'Avg_Stride_Length', 'ASLRK': 'Avg_Stride_Length_Rank',
            'MINSL': 'Min_Stride_Length', 'MINSLRK': 'Min_Stride_Length_Rank',
            'MAXSL': 'Max_Stride_Length', 'MAXSLRK': 'Max_Stride_Length_Rank',
            'ASF': 'Avg_Stride_Freq', 'ASFRK': 'Avg_Stride_Freq_Rank',
            'MINSF': 'Min_Stride_Freq', 'MINSFRK': 'Min_Stride_Freq_Rank',
            'MAXSF': 'Max_Stride_Freq', 'MAXSFRK': 'Max_Stride_Freq_Rank',
            'ROS': 'Run_Of_Race_Pace', 'ROSRK': 'Run_Of_Race_Pace_Rank'
        }
        if c in mapping: return mapping[c]

        # Regex mappings for furlongs and finish
        c = re.sub(r'^MPH_(\d+|Finish)$', r'Speed_MPH_\1', c)
        c = re.sub(r'^MPHRK_(\d+|Finish)$', r'Speed_MPH_Rank_\1', c)
        c = re.sub(r'^PARFSP_(\d+|Finish)$', r'Par_Finish_Speed_Pct_\1', c)
        c = re.sub(r'^PARST_(\d+|Finish)$', r'Par_Sectional_Time_\1', c)
        c = re.sub(r'^SPOS_(\d+|Finish)$', r'Race_Position_\1', c)
        c = re.sub(r'^LBL_(\d+|Finish)$', r'Lengths_Behind_Leader_\1', c)
        c = re.sub(r'^LBLRK_(\d+|Finish)$', r'Lengths_Behind_Leader_Rank_\1', c)
        c = re.sub(r'^STDIFF_(\d+|Finish)$', r'Sectional_Time_Diff_\1', c)
        c = re.sub(r'^STDIFFRK_(\d+|Finish)$', r'Sectional_Time_Diff_Rank_\1', c)
        c = re.sub(r'^FSPDIFF_(\d+|Finish)$', r'Finish_Speed_Pct_Diff_\1', c)
        c = re.sub(r'^FSPDIFFRK_(\d+|Finish)$', r'Finish_Speed_Pct_Diff_Rank_\1', c)
        c = re.sub(r'^FSPEFFRK_(\d+|Finish)$', r'Finish_Speed_Efficiency_Rank_\1', c)
        c = re.sub(r'^SL_(\d+|Finish)$', r'Stride_Length_\1', c)
        c = re.sub(r'^SLRK_(\d+|Finish)$', r'Stride_Length_Rank_\1', c)
        c = re.sub(r'^SF_(\d+|Finish)$', r'Stride_Frequency_\1', c)
        c = re.sub(r'^SFRK_(\d+|Finish)$', r'Stride_Frequency_Rank_\1', c)
        c = re.sub(r'^NOS_(\d+|Finish)$', r'Num_Of_Strides_\1', c)
        c = re.sub(r'^NOSRK_(\d+|Finish)$', r'Num_Of_Strides_Rank_\1', c)
        c = re.sub(r'^UPG_(\d+|Finish)$', r'Upgrade_\1', c)
        c = re.sub(r'^UPGRK_(\d+|Finish)$', r'Upgrade_Rank_\1', c)
        c = re.sub(r'^TTR(\d+)$', r'Time_To_Run_\1_Furlongs', c)
        c = re.sub(r'^TTR(\d+)RK$', r'Time_To_Run_\1_Furlongs_Rank', c)

        # New ones from the header
        c = re.sub(r'^ST_(\d+|Finish)$', r'Sectional_Time_\1', c)
        c = re.sub(r'^STRK_(\d+|Finish)$', r'Sectional_Time_Rank_\1', c)
        c = re.sub(r'^FSP_(\d+|Finish)$', r'Finish_Speed_Pct_\1', c)
        c = re.sub(r'^FSPRK_(\d+|Finish)$', r'Finish_Speed_Pct_Rank_\1', c)

        if c == 'JFLE': return 'Jump_Fluency_Loss_Early'
        if c == 'JFLERK': return 'Jump_Fluency_Loss_Early_Rank'
        if c == 'JFLM': return 'Jump_Fluency_Loss_Middle'
        if c == 'JFLMRK': return 'Jump_Fluency_Loss_Middle_Rank'
        if c == 'JFLL': return 'Jump_Fluency_Loss_Late'
        if c == 'JFLLRK': return 'Jump_Fluency_Loss_Late_Rank'

        if c == 'SiD': return 'Sectional_ID'
        if c == 'PA_Meeting_iD': return 'PA_Meeting_ID'
        if c == 'PA_Race_iD': return 'PA_Race_ID'
        if c == 'PA_Horse_iD': return 'PA_Horse_ID'

        return c

    df.rename(columns={c: rename_col(c) for c in df.columns}, inplace=True)

    out_dir = r"E:\Test\racing-form-system\reports"
    os.makedirs(out_dir, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%H%M%S")
    csv_path = os.path.join(out_dir, f"RaceCard_SData_{target_date}_{timestamp}.csv")
    df.to_csv(csv_path, index=False)

    print(f"\nSuccessfully generated race card for {len(df)} runners across {(df['RaceDateTime'].nunique())} races.")
    print(f"Saved to: {csv_path}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        target_date = datetime.date.today().strftime('%Y-%m-%d')

    export_daily_card(target_date)
