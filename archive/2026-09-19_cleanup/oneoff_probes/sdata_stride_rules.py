r"""
Part 2: rank the runners inside each race on stride metrics, one pick per race,
price it at the real BSP and at the morning price.

Control included: SPOS_Finish = 1 (finishing position) shows what a genuine
look-ahead result looks like, so the stride numbers can be read in context.
"""
import pandas as pd

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 40)

df = pd.read_pickle("reports/_sdata_month.pkl")
df["won"] = pd.to_numeric(df["WinLose"], errors="coerce").fillna(0).astype(int)
df["bsp"] = pd.to_numeric(df["BSP_TRUE"], errors="coerce")
df["morning"] = pd.to_numeric(df["MorningWAP"], errors="coerce")
for col in ("SL_Finish", "STRK_Finish", "FSP_Finish", "MPH_Finish",
            "ST_Finish", "STDIFF_Finish", "SF_Finish", "LBL_Finish",
            "SPOS_Finish", "NOS_Finish", "FSPEFFRK_Finish"):
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

print(f"runners: {len(df):,}   races: {df['SD_RNo'].nunique():,}\n")
print("rank-column direction check (does RK=1 mean the best or the worst?):")
for rk, raw in (("SLRK_Finish", "SL_Finish"), ("MPHRK_Finish", "MPH_Finish"),
                ("SFRK_Finish", "SF_Finish")):
    if rk in df.columns:
        g = df.groupby(df[rk].astype("float"))[raw].mean()
        print(f"   {rk}: raw mean at rank1={g.get(1.0):.2f}  "
              f"rank2={g.get(2.0):.2f}  rank3={g.get(3.0):.2f}")


def rank(frame, col, ascending):
    return frame.groupby("SD_RNo")[col].rank(ascending=ascending, method="first")


# my own within-race ranks (highest raw = rank 1 unless ascending=True)
df["r_sl"] = rank(df, "SL_Finish", False)        # longest stride
df["r_strk"] = rank(df, "STRK_Finish", False)    # most strides
df["r_fsp"] = rank(df, "FSP_Finish", False)      # best finishing speed %
df["r_mph"] = rank(df, "MPH_Finish", False)      # fastest
df["r_st"] = rank(df, "ST_Finish", True)         # fastest sectional time
df["r_stdiff"] = rank(df, "STDIFF_Finish", True)  # most under par
df["r_sf"] = rank(df, "SF_Finish", False)        # best speed figure
df["r_lbl"] = rank(df, "LBL_Finish", True)       # closest to leader

RULES = {
    "longest stride (SL)":                 df["r_sl"] == 1,
    "most strides (STRK)":                 df["r_strk"] == 1,
    "best finishing speed % (FSP)":        df["r_fsp"] == 1,
    "fastest at finish (MPH)":             df["r_mph"] == 1,
    "fastest sectional time (ST)":         df["r_st"] == 1,
    "most under par (STDIFF)":             df["r_stdiff"] == 1,
    "best speed figure (SF)":              df["r_sf"] == 1,
    "closest to leader (LBL)":             df["r_lbl"] == 1,
}
COMBOS = {
    "stride + FSP (both rank1)":           (df["r_sl"] == 1) & (df["r_fsp"] == 1),
    "stride + FSP top3":                   (df["r_sl"] <= 3) & (df["r_fsp"] <= 3),
    "stride top3 + fastest top3":          (df["r_sl"] <= 3) & (df["r_mph"] <= 3),
    "FSP top3 + under par top3":           (df["r_fsp"] <= 3) & (df["r_stdiff"] <= 3),
    "stride + SF both rank1":              (df["r_sl"] == 1) & (df["r_sf"] == 1),
    "most strides + FSP top3":             (df["r_strk"] == 1) & (df["r_fsp"] <= 3),
}
CONTROL = {"CONTROL finishing position = 1": df["SPOS_Finish"] == 1}


def ev(label, mask):
    s = df[mask]
    if s.empty:
        return {"rule": label, "races": 0}
    return {"rule": label, "races": s["SD_RNo"].nunique(), "bets": len(s),
                "strike": round(s["won"].mean() * 100, 1),
                "roi_bsp": round((s["bsp"] * s["won"] - 1).mean() * 100, 1),
                "roi_am": round((s["morning"] * s["won"] - 1).mean() * 100, 1),
                "avg_bsp": round(s["bsp"].mean(), 2),
                "exp_win": round((1 / s["bsp"]).sum(), 1)}


out = pd.DataFrame([ev(k, m) for k, m in {**RULES, **COMBOS}.items()])
out = out.sort_values("roi_bsp", ascending=False)
print("\n=== STRIDE-ONLY RULES (same race - LOOK-AHEAD, upper bound only) ===")
print(out.to_string(index=False))

base = ev("ALL RUNNERS (baseline)", df["bsp"].notna())
print("\nbaseline:", base)
print("control :", ev(*next(iter(CONTROL.items()))))
