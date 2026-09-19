"""
B2L PROPER BACKTEST — NO LOOKAHEAD, NO PRE-FILTER
===================================================
Rules:
  1. ALL handicap runners at BSP >= 20 included (no FinPos pre-filter)
  2. Score built ONLY from LTO data with strict RH_DateTime < TargetDateTime
  3. POSAFTUPG removed (may reference future run context)
  4. Score from: PaceAbbrev (LTO), StrideDecay (LTO SData), 
                 Comment (LTO), DSLR (LTO), JockeysClaim (LTO)
  5. PnL calculated only AFTER selection decision — no outcome used in scoring
"""
import sys, os, re, datetime, pyodbc, pandas as pd, warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

CONN = ("Driver={ODBC Driver 17 for SQL Server};"
        "Server=.\\PROFORM_RACING;Database=PRODB;Trusted_Connection=yes;")
MIN_ODDS    = 20.0
SDATA_START = datetime.date(2015, 12, 19)

# FIXED: PRODB HIR_BSP is NOT Betfair Starting Price.  Measured book overround
# on 42,313 handicap races = 1.2130 (21.3% bookmaker margin) versus 1.0025 for
# true Betfair BSP.  For a BACK the mispricing is PESSIMISTIC - fair price =
# BSP * overround - so the true-BSP ROI is HIGHER than the headline.  Both are
# reported.  See scripts/betfair_bsp_backfill.py for the real BSP.
OVERROUND = 1.2130

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
    """
    Score built PURELY from LTO observable signals — NO future data.
      +3  front-runner in LTO (pace L/P/F)
      +2  good speed words in LTO comment
      +2  jockey claim in LTO (claiming jockey = fresh edge)
      +2  quick return <= 7 days DSLR
      +2  clean discipline (no bad words)
      -2  bad discipline words in LTO
      -3  high stride decay >= 0.66ft (tired at finish of LTO)
      -1  very long absence > 120 days
    """
    s = 0
    if str(pace).upper() in ["L","P","F","LEAD","PROMINENT"]: s += 3
    if good_speed:  s += 2
    if jock_claim > 0: s += 2
    if dslr <= 7:   s += 2
    if not bad_disc: s += 2
    if bad_disc:    s -= 2
    # FIXED: (ASL - SL_Finish) is in FEET.  0.20 was 6cm and fired on ~78% of
    # scorable runners.  Require both values present and use 0.66 ft.
    if pd.notna(stride_decay) and stride_decay >= 0.66: s -= 3
    if dslr > 120:  s -= 1
    return s

def run(days_back=365):
    conn = pyodbc.connect(CONN)
    max_d = pd.read_sql("SELECT MAX(CAST(RH_DateTime AS DATE)) AS d FROM dbo.NEW_RH", conn)["d"].iloc[0]
    end_date = max_d.date() if hasattr(max_d,"date") else datetime.date.fromisoformat(str(max_d))
    start_date = max(end_date - datetime.timedelta(days=days_back), SDATA_START)

    print(f"\n{'='*80}")
    print(f"  B2L CLEAN BACKTEST: {start_date} to {end_date}  ({(end_date-start_date).days} days)")
    print(f"  ALL handicap runners BSP >= {MIN_ODDS}  |  No lookahead  |  No outcome filter")
    print(f"  Scoring: PaceCode + SpeedComment + StrideDecay + DSLR + JockeyClaim (LTO only)")
    print(f"{'='*80}")

    # ── Step 1: ALL runners (any outcome) at BSP >= 20 in handicap races ──
    sql = f"""
    SELECT LOWER(H.H_Name_No_Anything) AS horse_clean,
           R.RH_DateTime,
           CAST(R.RH_DateTime AS DATE) AS RaceDate,
           YEAR(R.RH_DateTime) AS Yr,
           HIR.HIR_PositionNo AS FinPos,
           HIR.HIR_BSP AS BSP
    FROM dbo.NEW_H H
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_HNo = H.H_No
    JOIN dbo.NEW_RH R    ON R.RH_RNo    = HIR.HIR_RNo
    WHERE CAST(R.RH_DateTime AS DATE) >= '{start_date}'
      AND CAST(R.RH_DateTime AS DATE) <= '{end_date}'
      AND HIR.HIR_BSP >= {MIN_ODDS}
      AND (R.RH_HandicapLimit IS NOT NULL OR LOWER(R.RH_Name) LIKE '%handicap%')
      AND HIR.HIR_PositionNo IS NOT NULL
    ORDER BY R.RH_DateTime;
    """
    print("Fetching all runners...")
    df = pd.read_sql(sql, conn)
    print(f"Total runners: {len(df)}")
    if df.empty: conn.close(); return

    # ── Step 2: LTO signals — strictly before TargetDateTime, NO POSAFTUPG ──
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
    SELECT T.clean_horse, T.TargetDateTime,
           Prev.Pace, Prev.DSLR, Prev.Claim, Prev.Comment, Prev.ASL, Prev.SLF
    FROM #BT T
    OUTER APPLY (
        SELECT TOP 1
            H2.HIR_PaceAbbrev   AS Pace,
            H2.HIR_DSLR         AS DSLR,
            H2.HIR_JockeysClaim AS Claim,
            H2.HIR_CommentsInRunning AS Comment,
            SD2.ASL             AS ASL,
            SD2.SL_Finish       AS SLF
        FROM dbo.NEW_H H2M
        JOIN dbo.NEW_HIR H2 ON H2.HIR_HNo = H2M.H_No
        JOIN dbo.NEW_RH R2  ON R2.RH_RNo  = H2.HIR_RNo
        LEFT JOIN dbo.SData SD2 ON SD2.SD_RNo = H2.HIR_RNo
                                AND SD2.SD_HNo = H2.HIR_HNo
        WHERE LOWER(H2M.H_Name_No_Anything) = T.clean_horse
          AND R2.RH_DateTime < T.TargetDateTime
        ORDER BY R2.RH_DateTime DESC
    ) Prev;
    """
    print("Fetching LTO signals (strict < TargetDateTime, no POSAFTUPG)...")
    df_lto = pd.read_sql(sql_lto, conn)
    conn.close()
    print(f"Matched {len(df_lto)} LTO records.")

    lto_map = {(r["clean_horse"], pd.Timestamp(r["TargetDateTime"])): r
               for _, r in df_lto.iterrows()}

    rows = []
    for _, w in df.iterrows():
        lto = lto_map.get((w["horse_clean"], pd.Timestamp(w["RH_DateTime"])))
        pace  = lto["Pace"]    if lto is not None and pd.notna(lto["Pace"])    else ""
        dslr  = int(lto["DSLR"])  if lto is not None and pd.notna(lto["DSLR"])  else 99
        jock  = float(lto["Claim"]) if lto is not None and pd.notna(lto["Claim"]) else 0.0
        comm  = str(lto["Comment"]).lower() if lto is not None and pd.notna(lto["Comment"]) else ""
        asl   = float(lto["ASL"]) if lto is not None and pd.notna(lto["ASL"]) else None
        slf   = float(lto["SLF"]) if lto is not None and pd.notna(lto["SLF"]) else None
        sd    = (asl - slf) if asl is not None and slf is not None else 0.0
        bad   = any(x in comm for x in DISC_WORDS)
        spd   = any(x in comm for x in SPEED_WORDS)
        sc    = score(pace, sd, bad, spd, dslr, jock)
        fp    = int(w["FinPos"]) if pd.notna(w["FinPos"]) else 99
        bsp   = float(w["BSP"])  if pd.notna(w["BSP"])  else 0.0
        tier  = ("WIN STAR"  if sc >= 7 else
                 "Tier1 EW"  if sc >= 4 else
                 "Tier2 B2L" if sc >= 2 and spd else "No Bet")
        rows.append({"RaceDate":w["RaceDate"],"Yr":int(w["Yr"]),"horse":w["horse_clean"],
                     "FinPos":fp,"BSP":bsp,"Score":sc,"Tier":tier,
                     "Pace":pace,"Speed":spd,"Disc":bad,"SD":round(sd,3),"DSLR":dslr})

    df_out = pd.DataFrame(rows)

    def pnl_stats(sub, label):
        if sub.empty:
            print(f"\n  {label}: no bets"); return
        n   = len(sub)
        w   = (sub["FinPos"]==1).sum()
        p3  = sub["FinPos"].between(1,3).sum()
        wpnl= pd.Series([(r["BSP"]-1) if r["FinPos"]==1 else -1 for _,r in sub.iterrows()])
        ewp = pd.Series([
            ((r["BSP"]-1)*0.5 if r["FinPos"]==1 else -0.5) +
            (((r["BSP"]-1)/4)*0.5 if r["FinPos"]<=3 else -0.5)
            for _,r in sub.iterrows()])
        roi_w  = wpnl.sum()/n*100
        roi_ew = ewp.sum()/n*100
        # FIXED: settle the SAME selections at a de-margined (true-BSP
        # equivalent) price.  fair = BSP * OVERROUND.
        fair = sub["BSP"] * OVERROUND
        wpnl_f = pd.Series([(f - 1.0) if r["FinPos"] == 1 else -1.0
                            for f, (_, r) in zip(fair, sub.iterrows())])
        ewp_f = pd.Series([
            ((f - 1.0) * 0.5 if r["FinPos"] == 1 else -0.5) +
            (((f - 1.0) / 4) * 0.5 if r["FinPos"] <= 3 else -0.5)
            for f, (_, r) in zip(fair, sub.iterrows())])
        roi_w_f  = wpnl_f.sum()/n*100
        roi_ew_f = ewp_f.sum()/n*100
        gp=wpnl[wpnl>0].sum(); gl=abs(wpnl[wpnl<0].sum())
        pf = gp/gl if gl else float("inf")
        cum=wpnl.cumsum(); maxdd=(cum-cum.cummax()).min()
        lr=ml=0
        for v in wpnl:
            if v<0: lr+=1; ml=max(ml,lr)
            else: lr=0
        avg_w = sub[sub["FinPos"]==1]["BSP"].mean() if w else 0
        p=w/n; b=avg_w-1 if avg_w>1 else 1
        kelly=max(0,(p*(b+1)-1)/b) if b>0 else 0
        print(f"\n  {'='*72}")
        print(f"  {label}  (n={n})")
        print(f"  {'='*72}")
        print(f"  Strike Rate:   WIN {w/n*100:>5.1f}%   PLACE(top3) {p3/n*100:>5.1f}%")
        print(f"  Avg BSP:       All {sub['BSP'].mean():>7.1f}   Winners {avg_w:.1f}")
        print(f"  WIN  P&L: £{wpnl.sum():>+9.2f}  ROI {roi_w:>+7.1f}%  PF {pf:.2f}  MaxDD £{maxdd:+.2f}  LoseRun {ml}")
        print(f"  EW   P&L: £{ewp.sum():>+9.2f}  ROI {roi_ew:>+7.1f}%")
        print(f"  WIN  ROI at DE-MARGINED price (true-BSP equiv): {roi_w_f:>+7.1f}%")
        print(f"  EW   ROI at DE-MARGINED price (true-BSP equiv): {roi_ew_f:>+7.1f}%")
        print(f"  Kelly:   {kelly*100:.1f}% of bank")

    pnl_stats(df_out[df_out["Tier"]=="WIN STAR"],  "WIN STAR  (score >= 7)")
    pnl_stats(df_out[df_out["Tier"]=="Tier1 EW"],  "Tier 1 EW (score 4-6)")
    pnl_stats(df_out[df_out["Tier"]=="Tier2 B2L"], "Tier 2 B2L(score 2+ & speed)")
    sel = df_out[df_out["Tier"]!="No Bet"]
    pnl_stats(sel, "ALL SELECTIONS COMBINED")

    # Year-by-year
    print(f"\n  {'='*80}")
    print(f"  YEAR-BY-YEAR  (All Tiers, £1 WIN)")
    print(f"  {'Year':<6} {'Bets':>6} {'Win':>5} {'SR%':>6} {'Plc%':>6} {'P&L':>10} {'ROI%':>8} {'MaxDD':>8} {'PF':>7}")
    print(f"  {'-'*70}")
    for yr in sorted(sel["Yr"].unique()):
        yd=sel[sel["Yr"]==yr]; n=len(yd)
        wn=(yd["FinPos"]==1).sum(); p3=yd["FinPos"].between(1,3).sum()
        sp=pd.Series([(r["BSP"]-1) if r["FinPos"]==1 else -1 for _,r in yd.iterrows()])
        dd=(sp.cumsum()-sp.cumsum().cummax()).min()
        gp=sp[sp>0].sum(); gl=abs(sp[sp<0].sum())
        pf=gp/gl if gl else float("inf")
        print(f"  {yr:<6} {n:>6} {wn:>5} {wn/n*100:>6.1f}% {p3/n*100:>6.1f}% {sp.sum():>+10.2f} {sp.sum()/n*100:>+8.1f}% {dd:>+8.2f} {pf:>7.2f}")
    n_t=len(sel); w_t=(sel["FinPos"]==1).sum(); p3_t=sel["FinPos"].between(1,3).sum()
    s_t=pd.Series([(r["BSP"]-1) if r["FinPos"]==1 else -1 for _,r in sel.iterrows()])
    gp=s_t[s_t>0].sum(); gl=abs(s_t[s_t<0].sum())
    print(f"  {'-'*70}")
    print(f"  {'TOTAL':<6} {n_t:>6} {w_t:>5} {w_t/n_t*100:>6.1f}% {p3_t/n_t*100:>6.1f}% {s_t.sum():>+10.2f} {s_t.sum()/n_t*100:>+8.1f}% {(s_t.cumsum()-s_t.cumsum().cummax()).min():>+8.2f} {gp/gl if gl else 99:>7.2f}")
    print(f"  {'='*80}")

    out = rf"E:\Test\racing-form-system\reports\B2L_CleanBacktest_{days_back}d.csv"
    df_out.to_csv(out, index=False)
    print(f"\n  Selections CSV: {out}")
    print(f"  Total universe: {len(df_out)} | Selections: {len(sel)} ({len(sel)/len(df_out)*100:.1f}% of all 20+ runners)\n")

if __name__=="__main__":
    days_back = 365
    if len(sys.argv)>1:
        try: days_back=int(sys.argv[1])
        except: pass
    run(days_back)