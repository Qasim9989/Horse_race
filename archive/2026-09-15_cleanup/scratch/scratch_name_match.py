import re

import pandas as pd
import pyodbc

S = (r"Driver={ODBC Driver 17 for SQL Server};"
     r"Server=(localdb)\MSSQLLocalDB;Database=SCRAPED_PRODB;Trusted_Connection=yes;")
s = pyodbc.connect(S)
card = pd.read_sql("SELECT HorseName FROM dbo.Scraped_Racecards "
                   "WHERE RaceDate='2026-09-15'", s)
res = pd.read_sql("SELECT DISTINCT HorseName FROM dbo.Scraped_Results "
                  "WHERE RaceDate >= '2026-06-01'", s)
s.close()

print("card name samples :", card["HorseName"].head(6).tolist())
print("result name samples:", res["HorseName"].head(6).tolist())


def plain(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def strip_country(s):
    return plain(re.sub(r"\s*\([A-Za-z]{2,4}\)\s*$", "", str(s or "")))


card["a"] = card["HorseName"].map(plain)
res["a"] = res["HorseName"].map(plain)
print(f"\nexact alnum match      : {card['a'].isin(set(res['a'])).mean()*100:.0f}%")
card["b"] = card["HorseName"].map(strip_country)
res["b"] = res["HorseName"].map(strip_country)
print(f"match after stripping "
      f"country suffix : {card['b'].isin(set(res['b'])).mean()*100:.0f}%")
missing = card[~card["b"].isin(set(res["b"]))]
print(f"\nstill unmatched: {len(missing)} of {len(card)}")
print("  e.g.", missing["HorseName"].head(10).tolist())
