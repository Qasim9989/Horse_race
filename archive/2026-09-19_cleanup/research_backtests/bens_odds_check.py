"""
Compare Ben's recorded odds (CSV) against PRODB's bookmaker price fields,
to answer: are his odds close to MorningPrice, or are they fake?
"""
import warnings

import pandas as pd
import pyodbc

warnings.filterwarnings("ignore")

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")

CSV = r"D:\RDB DATABASE\database\BensBets_Master_Feb_to_Sep_2026.csv"


def clean(name):
    return "".join(c for c in str(name or "").lower() if c.isalnum())


def main():
    ben = pd.read_csv(CSV, dtype=str)
    ben["date"] = pd.to_datetime(ben["Date"], dayfirst=True, errors="coerce")
    ben["horse_k"] = ben["Horse_Name"].map(clean)
    ben["track_k"] = ben["Track"].map(clean)
    ben["Odds"] = pd.to_numeric(ben["Odds"], errors="coerce")
    print(f"Ben selections: {len(ben)}")

    conn = pyodbc.connect(CONN)
    pro = pd.read_sql("""
        SELECT RH.RH_RNo, RH.RH_DateTime, C.C_Name AS Course,
               H.H_Name AS Horse,
               HIR.HIR_MorningPrice, HIR.HIR_BreakfastPrice, HIR.HIR_EveningPrice,
               HIR.HIR_BSP, HIR.HIR_BSP_TRUE
        FROM NEW_RH RH
        JOIN NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
        JOIN NEW_H H ON H.H_No = HIR.HIR_HNo
        LEFT JOIN NEW_C C ON C.C_ID = RH.RH_CNo
        WHERE RH.RH_DateTime >= '2026-01-01'
    """, conn)
    conn.close()
    pro["date"] = pd.to_datetime(pro["RH_DateTime"]).dt.date
    ben["date"] = ben["date"].dt.date
    pro["horse_k"] = pro["Horse"].map(clean)
    pro["track_k"] = pro["Course"].map(clean)

    m = ben.merge(pro, on=["date", "horse_k"], how="inner", suffixes=("_ben", "_pro"))
    m = m.drop_duplicates(subset=["Date", "Horse_Name"])
    print(f"Matched (date+horse): {len(m)}")

    for col, label in [("HIR_MorningPrice", "MorningPrice"),
                       ("HIR_BreakfastPrice", "BreakfastPrice"),
                       ("HIR_EveningPrice", "EveningPrice"),
                       ("HIR_BSP", "HIR_BSP (bookmaker SP)"),
                       ("HIR_BSP_TRUE", "HIR_BSP_TRUE (real Betfair)")]:
        v = m[[col, "Odds"]].dropna()
        v = v[v[col] > 1.0]
        if len(v) == 0:
            print(f"  {label:<26}: no data")
            continue
        ratio = v["Odds"] / v[col]
        corr = v["Odds"].corr(v[col])
        print(f"  {label:<26}: n={len(v):>4}  hisOdds/price med {ratio.median():.3f}  "
              f"corr {corr:+.3f}  hisOdds>price {(ratio>1).mean()*100:.0f}%")

    # a few examples
    ex = m[["Horse_Name", "Odds", "HIR_MorningPrice", "HIR_BreakfastPrice",
            "HIR_EveningPrice", "HIR_BSP_TRUE"]].dropna(
        subset=["HIR_MorningPrice", "Odds"]).head(10)
    print("\nExamples (his Odds vs Morning/Breakfast/Evening/BSP_TRUE):")
    print(ex.to_string(index=False))


if __name__ == "__main__":
    main()
