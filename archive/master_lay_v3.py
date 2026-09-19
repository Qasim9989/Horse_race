# -*- coding: utf-8 -*-
import os
import sys
import pyodbc
import pandas as pd

def conn_racingtv():
    return pyodbc.connect(
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=(localdb)\\MSSQLLocalDB;"
        "Database=RACINGTV_2023_2026;"
        "Trusted_Connection=yes;"
    )

def conn_prod():
    return pyodbc.connect(
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=(localdb)\\MSSQLLocalDB;"
        "Database=PRODB;"
        "Trusted_Connection=yes;"
    )

def main(target_date_str):
    print("Master Lay v3 (scrap DB + PRODB BSP) for: " + target_date_str)

    c1 = conn_racingtv()
    c2 = conn_prod()

    # PRODB BSP using the exact join from hybrid_racingtv_proform_lay_audit.py
    q_prod = f"""
    SELECT
        CAST(RH.RH_DateTime AS DATE) AS RaceDate,
        C.C_Name AS CourseName,
        H.H_Name_No_Anything AS HorseName,
        HIR.HIR_BSP AS ProdBSP
    FROM dbo.NEW_RH RH
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
    JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
    LEFT JOIN dbo.NEW_C C ON C.C_ID = RH.RH_CNo
    WHERE CAST(RH.RH_DateTime AS DATE) <= '{target_date_str}'
      AND HIR.HIR_BSP >= 1.01
    """
    df_prod = pd.read_sql(q_prod, c2)

    # Scraped results + LTO + sectional
    q_scrap = f"""
    SELECT
        r.RaceDate,
        r.CourseName,
        r.HorseName,
        r.RaceTitle,
        r.SP AS ScrapedSP,
        r.ScrapedBSP,
        iq.LTOFinishPosition,
        iq.SectionalFinishingSpeedEfficiency
    FROM Scraped_Results r
    JOIN Scraped_RaceIQ iq
        ON r.RaceDate = iq.RaceDate
        AND r.CourseName = iq.CourseName
        AND r.HorseName = iq.HorseName
    WHERE r.RaceDate <= '{target_date_str}'
    """
    df_scrap = pd.read_sql(q_scrap, c1)

    c1.close()
    c2.close()

    df = pd.merge(
        df_scrap,
        df_prod,
        on=["RaceDate", "CourseName", "HorseName"],
        how="left"
    )

    df["OfficialBSP"] = (
        df["ProdBSP"].fillna(df["ScrapedBSP"]).fillna(df["ScrapedSP"])
    )

    df["IsHandicap"] = df["RaceTitle"].str.contains("hcp|handicap", case=False, na=False)

    df = df[df["IsHandicap"]]
    df = df[(df["OfficialBSP"] >= 1.50) & (df["OfficialBSP"] <= 6.00)]
    df = df[df["LTOFinishPosition"] >= 4]

    df["RankFSP"] = df.groupby(["RaceDate", "CourseName"])["SectionalFinishingSpeedEfficiency"] \
        .rank(method="first", ascending=False)
    df = df[df["RankFSP"] > 2]

    out = df[[
        "RaceDate", "CourseName", "HorseName",
        "OfficialBSP", "LTOFinishPosition", "SectionalFinishingSpeedEfficiency"
    ]].copy()

    os.makedirs("output", exist_ok=True)
    out.to_csv("output/master_lay_v3.csv", index=False)

    print("Qualifiers: " + str(len(out)))
    print(out.head(10).to_string(index=False))

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/master_lay_v3.py YYYY-MM-DD")
        sys.exit(1)
    main(sys.argv[1])
