r"""
STRIDE ONLY - one month, runners compared WITHIN each race
==========================================================
Every metric is taken from the same race's stride/sectional record, runners
are ranked against each other inside their race, and each rule picks one
runner per race.  Then we measure what that pick would have returned at the
real BSP.

    python scripts\stride_only_month.py --days 31
    python scripts\stride_only_month.py --days 31 --top 2
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd
import pyodbc

PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=180;"
       "MultipleActiveResultSets=True;")
pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 60)

c = pyodbc.connect(PRO)
cur = c.cursor()


def table_cols(t):
    cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION", (t,))
    return [r[0] for r in cur.fetchall()]



args = argparse.Namespace(days=31, top=1)
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=31)
    ap.add_argument("--top", type=int, default=1)
    args = ap.parse_args()

print("BFSP cols      :", table_cols("BFSP"))
hir = table_cols("NEW_HIR")
print("NEW_HIR price  :", [x for x in hir if "BSP" in x.upper()])

# ---- window: the last N days that actually have stride data -------------
cur.execute("SELECT MAX(RecordTime) FROM dbo.NEW_TPD_STRIDE")
end = cur.fetchone()[0]
print("latest stride RecordTime:", end)

# ---------------------------------------------------------------------------
# Pull one month of stride data joined to its result and the real BSP
# ---------------------------------------------------------------------------
d_end = pd.Timestamp(end)
d_start = d_end - pd.Timedelta(days=args.days)
print(f"\nwindow: {d_start:%Y-%m-%d} -> {d_end:%Y-%m-%d}")

q = """
SELECT S.RH_RNo, S.HIR_HNo, RH.RH_DateTime,
       S.AvgSpeed5s, S.AvgSpeed10s, S.HL6_SpeedMS,
       S.HL7_LeaderSpeedAtLocation, S.HL8_ParSpeedAtLocation,
       S.HL9_VelocityFluctuation, S.HL10_VelocityErrorRating,
       S.HL11_StrideFrequency, S.HL12_LeaderStrideFreqAtLocation,
       S.HL13_ParStrideFreqAtLocation, S.HL14_CadenceErrorRating,
       S.HL15_DistanceToFinish, S.HL16_PercentRaceLeft,
       S.HL17_Position, S.HL18_DistanceBehindLeader,
       HIR.HIR_PositionNo, HIR.HIR_BSP_TRUE, HIR.HIR_DSLR
FROM dbo.NEW_TPD_STRIDE S
JOIN dbo.NEW_HIR HIR ON HIR.HIR_HNo = S.HIR_HNo AND HIR.HIR_RNo = S.RH_RNo
JOIN dbo.NEW_RH  RH  ON RH.RH_RNo  = S.RH_RNo
WHERE RH.RH_DateTime BETWEEN ? AND ?
"""
df = pd.read_sql(q, c, params=[d_start, d_end])
print(f"stride rows with a runner row and a result: {len(df):,}")
if df.empty:
    sys.exit("no stride data in that window")

df["won"] = pd.to_numeric(df["HIR_PositionNo"], errors="coerce").eq(1).astype(int)
df["bsp"] = pd.to_numeric(df["HIR_BSP_TRUE"], errors="coerce")
df = df[df["bsp"].notna() & (df["bsp"] > 1)]
print(f"of those, with a real BSP: {len(df):,}  "
      f"across {df['RH_RNo'].nunique():,} races")

# derived: how the horse compared with the race's par figures
df["stride_vs_par"] = df["HL11_StrideFrequency"] - df["HL13_ParStrideFreqAtLocation"]
df["speed_vs_par"] = df["HL6_SpeedMS"] - df["HL8_ParSpeedAtLocation"]
df["cadence_err"] = pd.to_numeric(df["HL14_CadenceErrorRating"], errors="coerce")
df["vel_err"] = pd.to_numeric(df["HL10_VelocityErrorRating"], errors="coerce")
df["stride_freq"] = pd.to_numeric(df["HL11_StrideFrequency"], errors="coerce")
df["speed5"] = pd.to_numeric(df["AvgSpeed5s"], errors="coerce")
df["pos_in_race"] = pd.to_numeric(df["HL17_Position"], errors="coerce")
c.close()

print("BFSP cols      :", table_cols("BFSP"))
hir = table_cols("NEW_HIR")
print("NEW_HIR price  :", [x for x in hir if "BSP" in x.upper() or "SP" in x.upper()][:8])

# ---------------------------------------------------------------------------
# Compare runners WITHIN each race, then test one-pick-per-race rules
# ---------------------------------------------------------------------------
def rank_in_race(frame, col, ascending):
    return frame.groupby("RH_RNo")[col].rank(ascending=ascending, method="first")


df["r_stride"] = rank_in_race(df, "stride_freq", False)        # highest freq
df["r_stride_par"] = rank_in_race(df, "stride_vs_par", False)  # most above par
df["r_cadence"] = rank_in_race(df, "cadence_err", True)        # lowest error
df["r_vel"] = rank_in_race(df, "vel_err", True)                # lowest error
df["r_speed"] = rank_in_race(df, "speed5", False)              # fastest
df["r_speed_par"] = rank_in_race(df, "speed_vs_par", False)    # most above par

RULES = {
    "stride frequency (highest)":            df["r_stride"] == 1,
    "stride freq vs par (most above)":       df["r_stride_par"] == 1,
    "cadence error (lowest)":                df["r_cadence"] == 1,
    "velocity error (lowest)":               df["r_vel"] == 1,
    "avg speed 5s (fastest)":                df["r_speed"] == 1,
    "speed vs par (most above)":             df["r_speed_par"] == 1,
}
COMBOS = {
    "stride freq vs par + cadence top3":    (df["r_stride_par"] == 1) & (df["r_cadence"] <= 3),
    "speed vs par + cadence top3":          (df["r_speed_par"] == 1) & (df["r_cadence"] <= 3),
    "fastest + cadence top3":               (df["r_speed"] == 1) & (df["r_cadence"] <= 3),
    "fastest + lowest cadence error":       (df["r_speed"] == 1) & (df["r_cadence"] == 1),
    "stride freq + speed both top2":        (df["r_stride"] <= 2) & (df["r_speed"] <= 2),
    "most above par on BOTH speed+stride":  (df["r_stride_par"] == 1) & (df["r_speed_par"] == 1),
}


def evaluate(mask, label, top=1):
    sel = df[mask]
    if top > 1:
        # keep only the top N per race by rank of the first available rank col
        pass
    races = sel["RH_RNo"].nunique()
    if races == 0:
        return {"rule": label, "races": 0}
    profit = (sel["bsp"] * sel["won"] - 1)
    return {"rule": label, "races": races, "bets": len(sel),
                "strike": round(sel["won"].mean() * 100, 1),
                "roi_bsp": round(profit.mean() * 100, 2),
                "avg_bsp": round(sel["bsp"].mean(), 2),
                "exp_wins": round((1 / sel["bsp"]).sum(), 1)}


rows = [evaluate(m, k) for k, m in {**RULES, **COMBOS}.items()]
res = pd.DataFrame(rows).sort_values("roi_bsp", ascending=False)
print("\n=== STRIDE-ONLY RULES, one pick per race, priced at REAL BSP ===")
print(res.to_string(index=False))

# whole-field baseline for the same window
base = pd.DataFrame([{
    "rule": "ALL RUNNERS (baseline)", "races": df["RH_RNo"].nunique(),
    "bets": len(df), "strike": round(df["won"].mean() * 100, 2),
    "roi_bsp": round((df["bsp"] * df["won"] - 1).mean() * 100, 2),
    "avg_bsp": round(df["bsp"].mean(), 2),
    "exp_wins": round((1 / df["bsp"]).sum(), 1)}])
print("\n" + base.to_string(index=False))

best = res.iloc[0]
print(f"\nbest stride-only rule: {best['rule']}  "
      f"({best['bets']} bets, strike {best['strike']}%, "
      f"ROI at BSP {best['roi_bsp']}%)")
print(f"expected winners from BSP for that selection: {best['exp_wins']}  "
      f"(actual {round(best['strike'] / 100 * best['bets'])})")
c.close()
