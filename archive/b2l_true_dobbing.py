import sys, os, re, datetime, pyodbc, glob
import pandas as pd
import warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

CONN = ("Driver={ODBC Driver 17 for SQL Server};"
        "Server=.\\PROFORM_RACING;Database=PRODB;Trusted_Connection=yes;")

SPEED_WORDS = ["led","ran on","ran on well","ran on strongly","kept on","kept on well",
    "strong finish","headway","good headway","quickened","quickened well",
    "chased leaders","chased leader","pushed along","driven out","made all",
    "disputed lead","prominent","stayed on","stayed on well","rallied","finished well"]
DISC_WORDS  = ["slowly away","dwelt","pulled hard","keen","hung","erratic",
    "lost ground start","missed break","reared"]

def clean(n):
    n = re.sub(r"\s*\([A-Z]{2,4}\)$","",str(n or ""),flags=re.IGNORECASE).strip()
    return "".join(c for c in n.lower() if c.isalnum())

def score(pace, stride_decay, bad_disc, good_speed, dslr, jock_claim):
    s = 0
    if str(pace).upper() in ["L","P","F","LEAD","PROMINENT"]: s += 3
    if good_speed:  s += 2
    if jock_claim > 0: s += 2
    if dslr <= 7:   s += 2
    if not bad_disc: s += 2
    if bad_disc:    s -= 2
    if stride_decay >= 0.66: s -= 3  # FIXED: ft not m
    if dslr > 120:  s -= 1
    return s

def build_excel_map():
    print("Indexing D:\\RDB\\Res...")
    paths = glob.glob(r'D:\RDB\Res\*\Results - *.xlsx')
    date_map = {}
    for p in paths:
        fname = os.path.basename(p)
        m = re.search(r'Results - (\d{2})(\d{2})(\d{4})\.xlsx', fname)
        if m:
            dt_str = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            date_map[dt_str] = p
    print(f"Indexed {len(date_map)} daily Excel files.")
    return date_map

def run(start_date='2023-01-01', end_date='2023-03-31'):
    excel_map = build_excel_map()
    
    conn = pyodbc.connect(CONN)
    
    print(f"\n{'='*80}")
    print(f"  TRUE B2L (DOBBING) BACKTEST: {start_date} to {end_date}")
    print(f"  ALL handicap runners | No BSP Filter | Cross-referenced against RDB in-play ticks")
    print(f"{'='*80}")

    sql = f"""
    SELECT LOWER(H.H_Name_No_Anything) AS horse_clean,
           R.RH_DateTime,
           CAST(R.RH_DateTime AS DATE) AS RaceDate,
           YEAR(R.RH_DateTime) AS Yr
    FROM dbo.NEW_H H
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_HNo = H.H_No
    JOIN dbo.NEW_RH R    ON R.RH_RNo    = HIR.HIR_RNo
    WHERE CAST(R.RH_DateTime AS DATE) >= '{start_date}'
      AND CAST(R.RH_DateTime AS DATE) <= '{end_date}'
      AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
    ORDER BY R.RH_DateTime;
    """
    print("Fetching universe...")
    df = pd.read_sql(sql, conn)
    print(f"Total handicap runners: {len(df)}")
    if df.empty: conn.close(); return

    cur = conn.cursor()
    cur.execute("""
    IF OBJECT_ID('tempdb..#BT') IS NOT NULL DROP TABLE #BT;
    CREATE TABLE #BT (TargetDateTime DATETIME, clean_horse VARCHAR(200));
    """)
    conn.commit()
    cur.fast_executemany = True
    cur.executemany("INSERT INTO #BT VALUES (?,?)",
        [(r.RH_DateTime.to_pydatetime(), r.horse_clean) for r in df.itertuples(index=False)])
    conn.commit()

    sql_lto = """
    WITH RankedRuns AS (
        SELECT 
            T.clean_horse,
            T.TargetDateTime,
            H2.HIR_PaceAbbrev AS Pace,
            H2.HIR_DSLR AS DSLR,
            H2.HIR_JockeysClaim AS Claim,
            H2.HIR_CommentsInRunning AS Comment,
            SD2.ASL AS ASL,
            SD2.SL_Finish AS SLF,
            ROW_NUMBER() OVER(PARTITION BY T.clean_horse, T.TargetDateTime ORDER BY R2.RH_DateTime DESC) as rn
        FROM #BT T
        JOIN dbo.NEW_H H2M ON LOWER(H2M.H_Name_No_Anything) = T.clean_horse
        JOIN dbo.NEW_HIR H2 ON H2.HIR_HNo = H2M.H_No
        JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
        LEFT JOIN dbo.SData SD2 ON SD2.SD_RNo = H2.HIR_RNo AND SD2.SD_HNo = H2.HIR_HNo
        WHERE R2.RH_DateTime < T.TargetDateTime
    )
    SELECT clean_horse, TargetDateTime, Pace, DSLR, Claim, Comment, ASL, SLF
    FROM RankedRuns
    WHERE rn = 1;
    """
    print("Fetching LTO signals (strict < TargetDateTime)...")
    df_lto = pd.read_sql(sql_lto, conn)
    conn.close()
    
    lto_map = {(r["clean_horse"], pd.Timestamp(r["TargetDateTime"])): r for _, r in df_lto.iterrows()}

    qualifiers = []
    for _, w in df.iterrows():
        lto = lto_map.get((w["horse_clean"], pd.Timestamp(w["RH_DateTime"])))
        if lto is None: continue
        
        pace  = lto["Pace"]    if pd.notna(lto["Pace"])    else ""
        dslr  = int(lto["DSLR"])  if pd.notna(lto["DSLR"])  else 99
        jock  = float(lto["Claim"]) if pd.notna(lto["Claim"]) else 0.0
        comm  = str(lto["Comment"]).lower() if pd.notna(lto["Comment"]) else ""
        asl   = float(lto["ASL"]) if pd.notna(lto["ASL"]) else None
        slf   = float(lto["SLF"]) if pd.notna(lto["SLF"]) else None
        sd    = (asl - slf) if asl is not None and slf is not None else 0.0
        bad   = any(x in comm for x in DISC_WORDS)
        spd   = any(x in comm for x in SPEED_WORDS)
        sc    = score(pace, sd, bad, spd, dslr, jock)
        
        if sc >= 2 and spd:
            tier = "WIN STAR" if sc >= 7 else "Tier1 EW" if sc >= 4 else "Tier2 B2L"
            qualifiers.append({
                "RaceDate": w["RaceDate"].strftime('%Y-%m-%d'),
                "horse_clean": w["horse_clean"],
                "Score": sc,
                "Tier": tier
            })

    print(f"Found {len(qualifiers)} LTO Qualifiers. Verifying Dobbing profit against D:\\RDB\\Res...")
    
    df_q = pd.DataFrame(qualifiers)
    if df_q.empty: return
    
    results = []
    # Group by date to only open each Excel file once
    for dt, group in df_q.groupby("RaceDate"):
        if dt not in excel_map:
            continue
            
        try:
            rdb_df = pd.read_excel(excel_map[dt])
            rdb_df['Horse_Clean'] = rdb_df['Horse'].apply(clean)
        except Exception as e:
            print(f"Failed to read {excel_map[dt]}: {e}")
            continue
            
        for _, row in group.iterrows():
            horse_matches = rdb_df[rdb_df['Horse_Clean'] == row['horse_clean']]
            if horse_matches.empty:
                continue
                
            match = horse_matches.iloc[0]
            bsp = match.get('BSP', 0)
            min_price = match.get('Min Price', 0)
            
            try: bsp = float(bsp)
            except: bsp = 0
            try: min_price = float(min_price)
            except: min_price = 0
            
            if pd.isna(bsp) or bsp <= 1.01 or pd.isna(min_price):
                continue
                
            # Dobbing logic: Lay at half BSP
            lay_target = bsp / 2.0
            matched = min_price <= lay_target
            
            # PnL: Risk £10. If matched, win £10 * 0.98. If unmatched, lose £10.
            pnl = 9.80 if matched else -10.00
            
            row_dict = row.to_dict()
            row_dict.update({
                "BSP": bsp,
                "Min_Price": min_price,
                "Lay_Target": lay_target,
                "Matched": matched,
                "PnL": pnl
            })
            results.append(row_dict)
            
    df_res = pd.DataFrame(results)
    if df_res.empty:
        print("No trades matched in RDB.")
        return
        
    def print_stats(sub, label):
        if sub.empty: return
        tb = len(sub)
        matched = sub['Matched'].sum()
        pnl = sub['PnL'].sum()
        rsk = tb * 10.0
        r = (pnl / rsk) * 100
        
        print(f"\n  {'='*72}")
        print(f"  {label}  (Trades={tb})")
        print(f"  {'='*72}")
        print(f"  Dobbing Strike Rate: {(matched/tb)*100:.1f}%")
        print(f"  Avg BSP:             {sub['BSP'].mean():.2f}")
        print(f"  Total PnL:           £{pnl:.2f}")
        print(f"  ROI:                 {r:+.2f}%")
        
    print_stats(df_res[df_res["Tier"]=="WIN STAR"], "WIN STAR  (score >= 7)")
    print_stats(df_res[df_res["Tier"]=="Tier1 EW"], "Tier 1 EW (score 4-6)")
    print_stats(df_res[df_res["Tier"]=="Tier2 B2L"], "Tier 2 B2L (score 2+)")
    print_stats(df_res, "ALL TIER SELECTIONS")
    
    out = r"E:\Test\racing-form-system\reports\B2L_True_Dobbing.csv"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df_res.to_csv(out, index=False)
    print(f"\nSaved full trade log to: {out}")

if __name__=="__main__":
    start = sys.argv[1] if len(sys.argv)>1 else "2023-01-01"
    end = sys.argv[2] if len(sys.argv)>2 else "2023-01-31"
    run(start, end)
