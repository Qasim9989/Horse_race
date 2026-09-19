"""Which OR is trustworthy: the scraped card, PRODB, or Scraped_Results?

Compares today's card OR against each horse's most recent OR in
SCRAPED_PRODB.dbo.Scraped_Results (fresh to 19 Aug 2026) and in PRODB
(only to 22 May 2026).
"""
import pandas as pd
import pyodbc

S = (r"Driver={ODBC Driver 17 for SQL Server};"
     r"Server=(localdb)\MSSQLLocalDB;Database=SCRAPED_PRODB;Trusted_Connection=yes;")
P = (r"Driver={ODBC Driver 17 for SQL Server};"
     r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")
DAY = "2026-09-15"


def norm(s):
    return "".join(c for c in str(s or "").lower() if c.isalnum())


s = pyodbc.connect(S)
card = pd.read_sql("SELECT HorseName, RaceTime, CourseName, Age, Weight, "
                   "OfficialRating FROM dbo.Scraped_Racecards WHERE RaceDate = ?",
                   s, params=[DAY])
res = pd.read_sql("SELECT HorseName, RaceDate, RaceTime, OfficialRating, "
                  "Weight, PosNo, BSP FROM dbo.Scraped_Results "
                  "WHERE RaceDate >= '2026-06-01'", s)
s.close()
card["hk"] = card["HorseName"].map(norm)
res["hk"] = res["HorseName"].map(norm)
card["OR_card"] = pd.to_numeric(card["OfficialRating"], errors="coerce")
res["OR_res"] = pd.to_numeric(res["OfficialRating"], errors="coerce")
res = res[res["OR_res"].notna()]
lto = (res.sort_values(["RaceDate", "RaceTime"])
       .groupby("hk").last().reset_index()[["hk", "OR_res", "RaceDate",
                                            "PosNo", "Weight"]])

p = pyodbc.connect(P)
hist = pd.read_sql("SELECT HIR.HIR_HNo AS hid, RH.RH_DateTime, "
                   "HIR.HIR_OfficialRating AS OR_ FROM NEW_HIR HIR "
                   "JOIN NEW_RH RH ON RH.RH_RNo = HIR.HIR_RNo "
                   "WHERE HIR.HIR_OfficialRating > 0", p)
h = pd.read_sql("SELECT H_No, H_Name FROM NEW_H", p)
p.close()
h["hk"] = h["H_Name"].map(norm)
hist = hist.merge(h[["H_No", "hk"]], left_on="hid", right_on="H_No")
plto = (hist.sort_values("RH_DateTime").groupby("hk")["OR_"].last()
        .rename("OR_prodb").reset_index())

m = (card.merge(lto, on="hk", how="left").merge(plto, on="hk", how="left"))
m = m.dropna(subset=["OR_card"])
have = m.dropna(subset=["OR_res"]).copy()
have["d_card_res"] = (have["OR_card"] - have["OR_res"]).abs()
print(f"card runners {len(m)}, with an OR on the card {len(m)}, "
      f"matched to June+ results {len(have)}")
if len(have):
    print(f"  card OR within 2 lb of freshest result OR : "
          f"{(have['d_card_res'] <= 2).mean() * 100:.0f}%")
    print(f"  card OR within 5 lb                       : "
          f"{(have['d_card_res'] <= 5).mean() * 100:.0f}%")
hp = have.dropna(subset=["OR_prodb"]).copy()
hp["d_card_prodb"] = (hp["OR_card"] - hp["OR_prodb"]).abs()
if len(hp):
    print(f"  card OR within 5 lb of PRODB's last OR    : "
          f"{(hp['d_card_prodb'] <= 5).mean() * 100:.0f}%")
print("\nsample (card vs freshest scraped result vs PRODB):")
print(have.head(15)[["HorseName", "RaceTime", "Age", "Weight", "OR_card",
                     "OR_res", "RaceDate", "OR_prodb"]].to_string(index=False))
print("\ncard OR distribution:")
print(m["OR_card"].describe().round(1).to_string())
