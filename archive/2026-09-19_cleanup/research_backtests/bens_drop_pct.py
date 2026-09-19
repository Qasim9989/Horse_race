"""How much do Ben's selections shorten from his recorded price to the BSP?
And is that drop tradeable on the exchange?"""
import warnings

import pandas as pd
import pyodbc

warnings.filterwarnings("ignore")

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")
CSV = r"D:\RDB DATABASE\database\BensBets_Master_Feb_to_Sep_2026.csv"


def clean(n):
    return "".join(c for c in str(n or "").lower() if c.isalnum())


ben = pd.read_csv(CSV, dtype=str)
ben["date"] = pd.to_datetime(ben["Date"], dayfirst=True, errors="coerce").dt.date
ben["horse_k"] = ben["Horse_Name"].map(clean)
ben["Odds"] = pd.to_numeric(ben["Odds"], errors="coerce")

conn = pyodbc.connect(CONN)
pro = pd.read_sql("""SELECT RH.RH_DateTime, H.H_Name AS Horse,
                     HIR.HIR_BSP_TRUE, HIR.HIR_PositionNo,
                     HIR.HIR_EveningPrice, HIR.HIR_BreakfastPrice, HIR.HIR_MorningPrice
                     FROM NEW_RH RH JOIN NEW_HIR HIR ON HIR.HIR_RNo=RH.RH_RNo
                     JOIN NEW_H H ON H.H_No=HIR.HIR_HNo
                     WHERE RH.RH_DateTime>='2026-01-01'""", conn)
conn.close()
pro["date"] = pd.to_datetime(pro["RH_DateTime"]).dt.date
pro["horse_k"] = pro["Horse"].map(clean)
m = ben.merge(pro, on=["date", "horse_k"], how="inner").drop_duplicates(
    subset=["Date", "Horse_Name"])
m = m[(m["HIR_BSP_TRUE"] > 1.0) & (m["Odds"] > 1.0)].copy()

m["ratio"] = m["HIR_BSP_TRUE"] / m["Odds"]          # <1 = shortened
m["drop_pct"] = (1 - m["ratio"]) * 100              # +ve = shortened
print(f"matched: {len(m)}")
print(f"  median BSP/his odds      : {m['ratio'].median():.3f}  "
      f"-> median drop {m['drop_pct'].median():+.1f}%")
print(f"  mean BSP/his odds        : {m['ratio'].mean():.3f}")
print(f"  selections that SHORTENED: {(m['ratio']<1).mean()*100:.1f}%")
print(f"  median drop | shortened  : {m.loc[m['ratio']<1,'drop_pct'].median():+.1f}%")
print(f"  median drift | drifted   : {m.loc[m['ratio']>1,'drop_pct'].median():+.1f}%")

print("\n  distribution of the move (his odds -> BSP):")
for lo, hi, lbl in [(-1000, -50, "drifted 50%+"), (-50, -25, "drifted 25-50%"),
                    (-25, -10, "drifted 10-25%"), (-10, 0, "drifted 0-10%"),
                    (0, 10, "shortened 0-10%"), (10, 25, "shortened 10-25%"),
                    (25, 50, "shortened 25-50%"), (50, 1000, "shortened 50%+")]:
    s = m[(m["drop_pct"] >= lo) & (m["drop_pct"] < hi)]
    print(f"    {lbl:<18} {len(s):>4}  ({len(s)/len(m)*100:4.1f}%)")

print("\n  what would a back-to-lay capture (back at his odds, lay at BSP)?")
# profit on a B2L that completes = (back - lay)/lay ; if it never comes in, you hold
completes = m[m["ratio"] < 1]
pl = (completes["Odds"] - completes["HIR_BSP_TRUE"]) / completes["HIR_BSP_TRUE"]
print(f"    of {len(completes)} that shortened, median locked profit "
      f"{pl.median()*100:+.1f}% of stake")
