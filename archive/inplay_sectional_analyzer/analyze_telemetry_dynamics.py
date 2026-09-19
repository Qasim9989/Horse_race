"""
IN-PLAY LIVE SECTIONAL, STRIDE & SPEED DYNAMICS ENGINE
Analyzes RaceIQ telemetry (78,979 records) to map in-running win/loss probabilities:
1. Front-Runner Slip / Concession Dynamics
2. Challenger Pressure & Closing Speed Deltas
3. Stride Length & Finishing Speed Efficiency Thresholds
"""

import pyodbc
import pandas as pd
import numpy as np

CONN_STR = r"DRIVER={ODBC Driver 17 for SQL Server};SERVER=(localdb)\MSSQLLocalDB;DATABASE=RACINGTV_2023_2026;Trusted_Connection=yes;"

def load_telemetry_data():
    conn = pyodbc.connect(CONN_STR)
    sql = """
    SELECT 
      r.RaceDate,
      r.RaceTime,
      r.CourseName,
      r.RaceTitle,
      r.HorseName,
      r.PosNo,
      TRY_CAST(r.PosNo AS INT) AS FinPosInt,
      ISNULL(r.BSP, ISNULL(r.SP, '0')) AS DecOdds,
      r.Comment,
      TRY_CAST(iq.StrideLength AS FLOAT) AS StrideLength,
      TRY_CAST(iq.AvgFrequency AS FLOAT) AS AvgFrequency,
      TRY_CAST(iq.TopSpeed AS FLOAT) AS TopSpeed,
      TRY_CAST(iq.FinishingSpeedPct AS FLOAT) AS FinishingSpeedPct
    FROM dbo.Scraped_Results r
    JOIN dbo.Scraped_RaceIQ iq 
      ON r.RaceDate = iq.RaceDate AND r.CourseName = iq.CourseName AND r.HorseName = iq.HorseName
    WHERE TRY_CAST(iq.StrideLength AS FLOAT) > 0
    ORDER BY r.RaceDate, r.RaceTime;
    """
    df = pd.read_sql(sql, conn)
    conn.close()
    
    df['FinPosInt'] = df['PosNo'].astype(str).str.extract(r'(\d+)')[0].astype(float)
    df['IsTop4'] = df['FinPosInt'] <= 4.0
    df['IsBeatenFar'] = df['FinPosInt'] >= 8.0
    df['Comment_Lower'] = df['Comment'].fillna('').str.lower()
    df['IsLeader'] = df['Comment_Lower'].apply(lambda c: any(w in c for w in ['led', 'disputed lead', 'made all', 'prominent']))
    df['LostLead'] = df['Comment_Lower'].apply(lambda c: any(w in c for w in ['headed', 'lost lead', 'weakened', 'headed over', 'headed inside']))
    df['Challenged'] = df['Comment_Lower'].apply(lambda c: any(w in c for w in ['pressed leader', 'chased leader', 'challenged', 'rallied to lead', 'led near finish']))
    
    return df

def analyze_dynamics():
    df = load_telemetry_data()
    print("="*80)
    print(f"IN-PLAY TELEMETRY & SECTIONAL DYNAMICS MODEL ({len(df):,} Telemetry Runners)")
    print("="*80)
    
    print("\n--- TELEMETRY DISTRIBUTION ANALYSIS ---")
    print(f"   Total Telemetry Runners Evaluated: {len(df):,}")
    print(f"   Top 4 Placers (Extra Place Winners): {df['IsTop4'].sum():,} ({df['IsTop4'].mean()*100:.1f}%)")
    print(f"   Mid/Back Field Runners (8th+ Place): {df['IsBeatenFar'].sum():,} ({df['IsBeatenFar'].mean()*100:.1f}%)")
    
    # 1. Front-Runner Slip & Concession Dynamics
    leaders = df[df['IsLeader']]
    print("\n1. FRONT-RUNNER SLIP & CONCESSION DYNAMICS:")
    print(f"   Total Front-Runners Tracked:         {len(leaders):,}")
    print(f"   Front-Runners Reaching Top 4 Finish:  {leaders['IsTop4'].sum():,} ({leaders['IsTop4'].mean()*100:.1f}%)")
    
    headed = leaders[leaders['LostLead']]
    print(f"   Front-Runners Who Lost Lead / Headed: {len(headed):,}")
    print(f"   Top 4 Place Rate After Being Headed:  {headed['IsTop4'].sum():,} ({headed['IsTop4'].mean()*100:.1f}%)")
    
    # 2. Finishing Speed Efficiency (%) vs Top 4 Place Rate
    print("\n2. FINISHING SPEED EFFICIENCY (%) vs TOP 4 PLACE RATE:")
    df['SpeedTier'] = pd.cut(df['FinishingSpeedPct'], bins=[0, 95, 100, 105, 110, 200], labels=['<95% (Fading)', '95-100% (Steady)', '100-105% (Strong)', '105-110% (Surging)', '>110% (Elite Surge)'])
    speed_summary = df.groupby('SpeedTier', observed=False).agg(
        total_runners=('IsTop4', 'count'),
        top4_placers=('IsTop4', 'sum'),
        avg_stride=('StrideLength', 'mean'),
        avg_top_speed=('TopSpeed', 'mean')
    ).reset_index()
    speed_summary['top4_rate_%'] = (speed_summary['top4_placers'] / speed_summary['total_runners'] * 100).round(1)
    speed_summary['avg_stride_ft'] = (speed_summary['avg_stride'] * 3.28084).round(2)
    speed_summary['avg_top_speed_mph'] = speed_summary['avg_top_speed'].round(1)
    print(speed_summary[['SpeedTier', 'total_runners', 'top4_placers', 'top4_rate_%', 'avg_stride_ft', 'avg_top_speed_mph']].to_string(index=False))
    
    # 3. Stride Length & Cadence Thresholds
    print("\n3. STRIDE LENGTH THRESHOLDS vs IN-RUNNING PERFORMANCE:")
    df['StrideTier'] = pd.cut(df['StrideLength'], bins=[0, 6.8, 7.3, 7.6, 10.0], labels=['<6.8m (<22.3ft Short)', '6.8-7.3m (22.3-24.0ft Avg)', '7.3-7.6m (24.0-24.9ft Long)', '>7.6m (>24.9ft Elite)'])
    stride_summary = df.groupby('StrideTier', observed=False).agg(
        total_runners=('IsTop4', 'count'),
        top4_placers=('IsTop4', 'sum'),
        avg_top_speed=('TopSpeed', 'mean'),
        avg_fin_speed=('FinishingSpeedPct', 'mean')
    ).reset_index()
    stride_summary['top4_rate_%'] = (stride_summary['top4_placers'] / stride_summary['total_runners'] * 100).round(1)
    stride_summary['avg_top_speed_mph'] = stride_summary['avg_top_speed'].round(1)
    stride_summary['avg_fin_speed_%'] = stride_summary['avg_fin_speed'].round(1)
    print(stride_summary[['StrideTier', 'total_runners', 'top4_placers', 'top4_rate_%', 'avg_top_speed_mph', 'avg_fin_speed_%']].to_string(index=False))
    
    # 4. Challenger Surge Dynamics
    challengers = df[df['Challenged']]
    print("\n4. CHALLENGER SURGE DYNAMICS:")
    print(f"   Total Challengers Pressing Leader:    {len(challengers):,}")
    print(f"   Challenger Top 4 Finish Rate:          {challengers['IsTop4'].sum():,} ({challengers['IsTop4'].mean()*100:.1f}%)")
    
    print("="*80)

if __name__ == "__main__":
    analyze_dynamics()
