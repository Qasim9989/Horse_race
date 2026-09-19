"""Sanity-check the OfficialRating the scraped card feeds into Ben's rule.

Today's card had MarkChange values of -63, -70 lb. A handicap mark cannot fall
that far between runs, so either the card's OR or the history OR is wrong.
"""
import pandas as pd
import pyodbc

CARD = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=SCRAPED_PRODB;"
        r"Trusted_Connection=yes;")
PRO = (r"Driver={ODBC Driver 17 for SQL Server};"
       r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")
DAY = "2026-09-15"


def norm(s):
    return "".join(c for c in str(s or "").lower() if c.isalnum())


c = pyodbc.connect(CARD)
card = pd.read_sql("SELECT HorseName, CourseName, RaceTime, Age, Weight, "
                   "OfficialRating FROM dbo.Scraped_Racecards WHERE RaceDate = ?",
                   c, params=[DAY])
c.close()
card = card[card["OfficialRating"].notna()]
print(f"scraped card: {len(card)} runners, {card['OfficialRating'].notna().sum()} "
      f"with an OR")
print("sample OR values as scraped:", card["OfficialRating"].head(12).tolist())
card["OR_card"] = pd.to_numeric(card["OfficialRating"], errors="coerce")

p = pyodbc.connect(PRO)
h = pd.read_sql("SELECT H_No, H_Name FROM NEW_H", p)
h["hk"] = h["H_Name"].map(norm)
card["hk"] = card["HorseName"].map(norm)
ids = h[h["hk"].isin(set(card["hk"]))][["H_No", "hk"]]

hist = pd.read_sql(
    "SELECT HIR.HIR_HNo AS hid, RH.RH_DateTime, HIR.HIR_OfficialRating AS OR_ "
    "FROM NEW_HIR HIR JOIN NEW_RH RH ON RH.RH_RNo = HIR.HIR_RNo "
    "WHERE HIR.HIR_OfficialRating > 0", p)
p.close()
lto = (hist.sort_values("RH_DateTime").groupby("hid")["OR_"].last()
       .rename("OR_lto").reset_index())
ids = ids.merge(lto, left_on="H_No", right_on="hid", how="left")
m = card.merge(ids[["hk", "OR_lto"]], on="hk", how="left")
m["diff"] = m["OR_card"] - m["OR_lto"]
m = m.dropna(subset=["OR_card", "OR_lto"])
print(f"\nmatched to PRODB with a prior rating: {len(m)}")
print(f"  identical/±1 lb : {(m['diff'].abs() <= 1).mean() * 100:.1f}%")
print(f"  within ±5 lb    : {(m['diff'].abs() <= 5).mean() * 100:.1f}%")
print(f"  within ±10 lb   : {(m['diff'].abs() <= 10).mean() * 100:.1f}%")
print(f"  IMPLAUSIBLE     : {(m['diff'].abs() > 15).mean() * 100:.1f}%")
print("\nworst offenders (the card OR is not a handicap mark):")
print(m.reindex(m["diff"].abs().sort_values(ascending=False).index)
      .head(12)[["HorseName", "RaceTime", "Age", "Weight", "OR_card", "OR_lto",
                 "diff"]].to_string(index=False))
