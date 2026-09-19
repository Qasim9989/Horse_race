r"""
Part 2: previous-run stride profile -> next run, inside-race ranks, one pick
per race, priced at BSP / morning / pre-off.  No look-ahead: every metric is
from the horse's earlier run.
"""
import pandas as pd

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 40)

df = pd.read_pickle("reports/_sdata_prev.pkl")
df["won"] = pd.to_numeric(df["WinLose"], errors="coerce").fillna(0).astype(int)
for col in ("BSP_TRUE", "MorningWAP", "PPWAP", "PPTradedVol"):
    df[col] = pd.to_numeric(df[col], errors="coerce")
df = df[df["BSP_TRUE"] > 1]
df["year"] = pd.to_datetime(df["RaceDate"]).dt.year

# direction: True = bigger is better
DIRS = {
    "p_SL_Finish": True,      # longer stride
    "p_STRK_Finish": False,   # fewer strides (a long stride needs fewer)
    "p_FSP_Finish": True,     # higher finishing speed %
    "p_STDIFF_Finish": False,  # more under par
    "p_MPH_Finish": True,     # faster
    "p_SF_Finish": True,      # better speed figure
    "p_LBL_Finish": False,     # closer to leader last time
    "p_SPOS_Finish": False,    # finished closer to 1st last time
    "p_ST_Finish": False,      # faster sectional time
    "p_NOS_Finish": True,
}


def rk(col, bigger_better):
    return df.groupby("SD_RNo")[col].rank(ascending=not bigger_better,
                                          method="first")


R = {c: rk(c, d) for c, d in DIRS.items() if c in df.columns}

RULES = {
    "prev run: longest stride":        R["p_SL_Finish"] == 1,
    "prev run: fewest strides":        R["p_STRK_Finish"] == 1,
    "prev run: best finishing speed":  R["p_FSP_Finish"] == 1,
    "prev run: most under par":        R["p_STDIFF_Finish"] == 1,
    "prev run: fastest":               R["p_MPH_Finish"] == 1,
    "prev run: best speed figure":     R["p_SF_Finish"] == 1,
    "prev run: won":                   R["p_SPOS_Finish"] == 1,
}
COMBOS = {
    "prev stride len + FSP top3":  (R["p_SL_Finish"] <= 3) & (R["p_FSP_Finish"] <= 3),
    "prev stride len + underpar t3": (R["p_SL_Finish"] <= 3) & (R["p_STDIFF_Finish"] <= 3),
    "prev stride len + speedfig t3": (R["p_SL_Finish"] <= 3) & (R["p_SF_Finish"] <= 3),
    "prev FSP + underpar both 1":  (R["p_FSP_Finish"] == 1) & (R["p_STDIFF_Finish"] == 1),
    "prev fewest strides + FSP t3": (R["p_STRK_Finish"] <= 3) & (R["p_FSP_Finish"] <= 3),
}


def ev(label, mask, frame=None):
    f = df if frame is None else frame
    s = f[mask.reindex(f.index).fillna(False)] if frame is not None else f[mask]
    if s.empty:
        return {"rule": label, "races": 0}
    return {"rule": label, "races": s["SD_RNo"].nunique(), "bets": len(s),
                "strike": round(s["won"].mean() * 100, 1),
                "roi_bsp": round((s["BSP_TRUE"] * s["won"] - 1).mean() * 100, 2),
                "roi_am": round((s["MorningWAP"] * s["won"] - 1).mean() * 100, 2),
                "roi_po": round((s["PPWAP"] * s["won"] - 1).mean() * 100, 2),
                "avg_bsp": round(s["BSP_TRUE"].mean(), 2),
                "exp_win": round((1 / s["BSP_TRUE"]).sum(), 1)}


out = pd.DataFrame([ev(k, m) for k, m in {**RULES, **COMBOS}.items()])
out = out.sort_values("roi_bsp", ascending=False)
print("\n=== PREVIOUS-RUN STRIDE RULES (no look-ahead) ===")
print(f"window {df['RaceDate'].min()} .. {df['RaceDate'].max()}   "
      f"{len(df):,} runners, {df['SD_RNo'].nunique():,} races\n")
print(out.to_string(index=False))

print("\nbaseline:", ev("ALL RUNNERS", df["BSP_TRUE"].notna()))

print("\n=== year by year for the two most sensible rules ===")
for label, mask in [("prev longest stride", RULES["prev run: longest stride"]),
                    ("prev stride len + FSP top3",
                     COMBOS["prev stride len + FSP top3"])]:
    rows = []
    for y, g in df.groupby("year"):
        m = mask.reindex(g.index).fillna(False)
        s = g[m]
        if len(s) < 50:
            continue
        rows.append({"year": y, "bets": len(s),
                     "strike%": round(s["won"].mean() * 100, 1),
                     "ROI BSP": round((s["BSP_TRUE"] * s["won"] - 1).mean() * 100, 2),
                     "ROI morning": round((s["MorningWAP"] * s["won"] - 1).mean() * 100, 2)})
    print(f"\n{label}:")
    print(pd.DataFrame(rows).to_string(index=False))
