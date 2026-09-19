"""Is Ben's recorded odds plausible, or inflated? Compare his odds against the
real Betfair BSP by price band, and check the same for PRODB's early prices."""
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
                     HIR.HIR_BSP_TRUE, HIR.HIR_EveningPrice, HIR.HIR_MorningPrice,
                     HIR.HIR_BreakfastPrice
                     FROM NEW_RH RH JOIN NEW_HIR HIR ON HIR.HIR_RNo=RH.RH_RNo
                     JOIN NEW_H H ON H.H_No=HIR.HIR_HNo
                     WHERE RH.RH_DateTime>='2026-01-01'""", conn)
conn.close()
pro["date"] = pd.to_datetime(pro["RH_DateTime"]).dt.date
pro["horse_k"] = pro["Horse"].map(clean)
m = ben.merge(pro, on=["date", "horse_k"], how="inner").drop_duplicates(
    subset=["Date", "Horse_Name"])
m = m[(m["HIR_BSP_TRUE"] > 1.0) & (m["Odds"] > 1.0)].copy()
m["ratio"] = m["Odds"] / m["HIR_BSP_TRUE"]
print(f"matched with real BSP: {len(m)}")
print("\nBen's odds / real BSP, by his price band:")
bands = [(1, 4), (4, 8), (8, 16), (16, 33), (33, 100), (100, 10000)]
for lo, hi in bands:
    s = m[(m["Odds"] >= lo) & (m["Odds"] < hi)]
    if len(s) < 5:
        continue
    print(f"  his odds {lo:>3}-{hi:<5}: n={len(s):>3}  median ratio {s['ratio'].median():.3f}  "
          f"his longer {(s['ratio']>1).mean()*100:4.0f}%  "
          f"median his {s['Odds'].median():6.1f} vs BSP {s['HIR_BSP_TRUE'].median():6.1f}")

print("\nPRODB early price / real BSP, by BSP band (compression check):")
allp = pro[pro["HIR_BSP_TRUE"] > 1.0].copy()
allp["er"] = allp["HIR_EveningPrice"] / allp["HIR_BSP_TRUE"]
for lo, hi in [(1, 4), (4, 8), (8, 16), (16, 33), (33, 100), (100, 10000)]:
    s = allp[(allp["HIR_BSP_TRUE"] >= lo) & (allp["HIR_BSP_TRUE"] < hi)]
    if len(s) < 20:
        continue
    print(f"  BSP {lo:>3}-{hi:<5}: n={len(s):>6}  median EveningPrice/BSP "
          f"{s['er'].median():.3f}")
