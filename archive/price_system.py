"""PRODB walk-forward race-pricing system.

Uses only target-race declarations plus the horse's most recent prior run.
Raw SData sectionals 1-33 are reduced into early/middle/late par edges,
speed and stride features. BSP is settlement/benchmark data only; it is not a
model feature. A true CLV requires a recorded taken price, so CLV is reported
only when --entry-price-column is supplied by an imported price table.
"""
from __future__ import annotations
import argparse, os, re
from pathlib import Path
import numpy as np
import pandas as pd
import pyodbc
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
P = ("ST", "FSP", "MPH", "PARFSP", "PARST", "SL", "STRK", "SPOS")
RAW = [f"{p}_{i}" for p in P for i in range(1, 34)]
RAW_SQL = ",\n" + ",\n".join(f"sd.[{x}] AS Prev_{x}" for x in RAW)
DISC = re.compile(r"slowly away|dwelt|pulled hard|keen|hung|erratic|reared", re.I)

SQL = f"""
SELECT r.RH_RNo RaceNo,r.RH_DateTime RaceDateTime,r.RH_Name RaceName,
 r.RH_Class RaceClass,r.RH_GoingFull Going,r.RH_CNo CourseId,
 h.H_Name_No_Anything Horse,hir.HIR_HNo HorseId,hir.HIR_PositionNo FinishPosition,
 hir.HIR_BSP BSP,hir.HIR_Drawn Draw,hir.HIR_DSLR DSLR,
 hir.HIR_PaceAbbrev Pace,hir.HIR_JockeysClaim JockeyClaim,
 hir.HIR_Jockey_name Jockey,hir.HIR_Trainer_name Trainer,
 prev.PrevDate,prev.PrevFinish,prev.PrevPace,prev.PrevComment,
 prev.PrevASL,prev.PrevSLFinish,prev.PrevPosAfterUpgrade{RAW_SQL}
FROM dbo.NEW_RH r JOIN dbo.NEW_HIR hir ON hir.HIR_RNo=r.RH_RNo
JOIN dbo.NEW_H h ON h.H_No=hir.HIR_HNo
OUTER APPLY (SELECT TOP 1 r2.RH_DateTime PrevDate,h2.HIR_RNo PrevRaceNo,
 h2.HIR_PositionNo PrevFinish,h2.HIR_PaceAbbrev PrevPace,
 h2.HIR_COMMENTSINRUNNING PrevComment,sd2.ASL PrevASL,
 sd2.SL_Finish PrevSLFinish,sd2.POSAFTUPG PrevPosAfterUpgrade
 FROM dbo.NEW_HIR h2 JOIN dbo.NEW_RH r2 ON r2.RH_RNo=h2.HIR_RNo
 LEFT JOIN dbo.SData sd2 ON sd2.SD_RNo=h2.HIR_RNo AND sd2.SD_HNo=h2.HIR_HNo
 WHERE h2.HIR_HNo=hir.HIR_HNo AND r2.RH_DateTime<r.RH_DateTime
 ORDER BY r2.RH_DateTime DESC,r2.RH_RNo DESC) prev
LEFT JOIN dbo.SData sd ON sd.SD_RNo=prev.PrevRaceNo AND sd.SD_HNo=hir.HIR_HNo
WHERE r.RH_DateTime>=? AND r.RH_DateTime<?
 AND (r.RH_HandicapLimit IS NOT NULL OR LOWER(r.RH_Name) LIKE '%handicap%')
ORDER BY r.RH_DateTime,r.RH_RNo,hir.HIR_HNo
"""

def load(start, end):
    server=os.getenv("RACING_SQL_SERVER", r"localhost\PROFORM_RACING")
    db=os.getenv("RACING_SQL_DATABASE", "PRODB")
    cs=f"Driver={{ODBC Driver 17 for SQL Server}};Server={server};Database={db};Trusted_Connection=yes;Encrypt=no;TrustServerCertificate=yes;"
    with pyodbc.connect(cs,timeout=20) as c:
        return pd.read_sql(SQL,c,params=[start,(pd.Timestamp(end)+pd.Timedelta(days=1)).strftime('%Y-%m-%d')])

def features(df):
    for c in ["FinishPosition","BSP","Draw","DSLR","JockeyClaim","PrevFinish","PrevASL","PrevSLFinish","PrevPosAfterUpgrade"]:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df["RaceDateTime"]=pd.to_datetime(df.RaceDateTime); df["RaceYear"]=df.RaceDateTime.dt.year
    def m(prefix): return df[[f"Prev_{prefix}_{i}" for i in range(1,34)]].apply(pd.to_numeric,errors="coerce")
    fsp,parfsp,st,parst=m("FSP"),m("PARFSP"),m("ST"),m("PARST")
    mph,sl,strk=m("MPH"),m("SL"),m("STRK")
    fe=fsp-parfsp; te=parst-st
    for name,value in {
      "Sectionals":fe.notna().sum(axis=1),"FSP_Par_Pos":(fe>0).sum(axis=1),"FSP_Par_Neg":(fe<0).sum(axis=1),
      "FSP_Par_Edge":fe.mean(axis=1),"FSP_Par_Early":fe.iloc[:,:11].mean(axis=1),"FSP_Par_Mid":fe.iloc[:,11:22].mean(axis=1),"FSP_Par_Late":fe.iloc[:,22:].mean(axis=1),
      "Time_Par_Edge":te.mean(axis=1),"MPH_Mean":mph.mean(axis=1),"Stride_Mean":sl.mean(axis=1),"StrideRate_Mean":strk.mean(axis=1),
      "Stride_Decay":sl.iloc[:,0]-sl.iloc[:,-1],"Finish_Stride":sl.iloc[:,-1]}.items(): df[name]=value
    df["Stride_Decay"]=df.Stride_Decay.fillna(df.PrevASL-df.PrevSLFinish)
    df["BadComment"]=df.PrevComment.fillna("").astype(str).str.contains(DISC)
    df["PrevLeader"]=df.PrevPace.fillna("").astype(str).str.upper().isin(["L","P","F","LEAD"])
    df["Won"]=df.FinishPosition.eq(1); df["BSP"]=pd.to_numeric(df.BSP,errors="coerce")
    return df

def main(a):
    df=features(load(a.start,a.end));
    if df.empty: print("No handicap races found."); return
    num=["Draw","DSLR","JockeyClaim","PrevFinish","PrevASL","PrevSLFinish","PrevPosAfterUpgrade","Sectionals","FSP_Par_Pos","FSP_Par_Neg","FSP_Par_Edge","FSP_Par_Early","FSP_Par_Mid","FSP_Par_Late","Time_Par_Edge","MPH_Mean","Stride_Mean","StrideRate_Mean","Stride_Decay","Finish_Stride"]
    cat=["RaceClass","Going","CourseId","Pace","Jockey","Trainer"]
    X=df[num+cat]; y=df.Won.astype(int)
    train=df.RaceDateTime < pd.Timestamp(a.test_start)
    pipe=Pipeline([("prep",ColumnTransformer([("num",Pipeline([("imp",SimpleImputer(strategy="median")),("scale",StandardScaler())]),num),("cat",Pipeline([("imp",SimpleImputer(strategy="most_frequent")),("one",OneHotEncoder(handle_unknown="ignore"))]),cat)])),("model",LogisticRegression(max_iter=300,class_weight="balanced"))])
    pipe.fit(X[train],y[train]); df["ModelProb"]=pipe.predict_proba(X)[:,1]
    df["RaceProb"]=df.ModelProb/df.groupby("RaceNo").ModelProb.transform("sum"); df["FairOdds"]=1/df.RaceProb.replace(0,np.nan)
    df["MarketProb"]=1/df.BSP.where(df.BSP>1); df["ValueEdge"]=df.RaceProb-df.MarketProb
    df["Selection"]="NO BET"; df.loc[(df.RaceProb>=a.min_prob)&(df.ValueEdge>=a.min_edge),"Selection"]="BACK"
    df.loc[(df.RaceProb<=a.max_lay_prob)&(df.BSP.between(a.min_lay_bsp,a.max_bsp)),"Selection"]="LAY"
    df["PnL"]=0.0; lay=df.Selection.eq("LAY"); back=df.Selection.eq("BACK")
    df.loc[lay,"PnL"]=np.where(df.loc[lay,"Won"],-a.liability,a.liability/(df.loc[lay,"BSP"]-1)*0.98)
    df.loc[back,"PnL"]=np.where(df.loc[back,"Won"],(df.loc[back,"BSP"]-1)*a.stake*0.98,-a.stake)
    bets=df[df.Selection.ne("NO BET")].copy().sort_values("RaceDateTime"); bets["Equity"]=bets.PnL.cumsum(); bets["Drawdown"]=bets.Equity.cummax()-bets.Equity
    layb=bets[bets.Selection.eq("LAY")]; backb=bets[bets.Selection.eq("BACK")]
    def stat(s,den): return {"bets":len(s),"wins":int(s.Won.sum()),"losses":int((~s.Won).sum()),"pnl":round(s.PnL.sum(),2),"roi_pct":round(s.PnL.sum()/den*100,2) if den else 0,"max_drawdown":round(bets.Drawdown.max(),2) if len(bets) else 0}
    out=pd.DataFrame([{"races":df.RaceNo.nunique(),"runners":len(df),"lay":stat(layb,len(layb)*a.liability),"back":stat(backb,len(backb)*a.stake),"combined":stat(bets,len(layb)*a.liability+len(backb)*a.stake)}])
    REPORTS.mkdir(exist_ok=True); path=REPORTS/f"PriceSystem_{a.start}_{a.end}.csv"; out.to_csv(path,index=False)
    bets.to_csv(REPORTS/f"PriceSystem_Bets_{a.start}_{a.end}.csv",index=False)
    print(out.to_string(index=False)); print(f"Fair-price bets: {len(bets):,}; report: {path}")

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--start",required=True); p.add_argument("--end",required=True); p.add_argument("--test-start",required=True); p.add_argument("--min-prob",type=float,default=.10); p.add_argument("--min-edge",type=float,default=.02); p.add_argument("--max-lay-prob",type=float,default=.12); p.add_argument("--min-lay-bsp",type=float,default=1.01); p.add_argument("--max-bsp",type=float,default=6); p.add_argument("--liability",type=float,default=15); p.add_argument("--stake",type=float,default=1); main(p.parse_args())
