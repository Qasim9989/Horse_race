"""Cross-check: Betfair API BSP vs the official daily-file BSP for 2026-09-14.

If these agree, tonight's `betfair_api.py sp` is trustworthy for today's races.
Read-only.
"""
import os
import sys

import pandas as pd
import pyodbc

sys.path.insert(0, r"E:\Test\racing-form-system\scripts")
import betfair_api as bf                                          # noqa: E402

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")
DAY = "2026-09-14"

to = bf.login(verbose=False)
mk = bf.markets(DAY, to)
print(f"{len(mk)} markets on {DAY}")

rows = []
for i in range(0, len(mk), 20):
    chunk = mk[i:i + 20]
    for b in bf.books([m["market_id"] for m in chunk], to,
                      price_data=("SP_AVAILABLE", "SP_TRADED"), batch=20):
        if b.get("status") != "CLOSED":
            continue
        meta = next((m for m in chunk
                     if m["market_id"] == b.get("marketId")), None)
        if not meta:
            continue
        for s in b.get("runners") or []:
            if not s.get("bsp"):
                continue
            nm = meta["runners"].get(str(s.get("selectionId")))
            rows.append({"venue": bf.clean_name(meta["venue"]),
                         "horse": bf.clean_name(nm),
                         "api_bsp": float(s["bsp"]),
                         "won": s.get("status") == "WINNER"})
api = pd.DataFrame(rows)
print(f"API rows with BSP: {len(api)}")

c = pyodbc.connect(CONN)
db = pd.read_sql("SELECT CourseClean, HorseClean, BSP_TRUE, WinLose, Source "
                 "FROM dbo.BFSP WHERE RaceDate = ?", c, params=[DAY])
db = db.dropna(subset=["BSP_TRUE"]).drop_duplicates(["CourseClean", "HorseClean"])
c.close()
print(f"file rows        : {len(db)}")

m = api.merge(db, left_on=["venue", "horse"],
              right_on=["CourseClean", "HorseClean"], how="inner")
print(f"matched          : {len(m)}")
if len(m):
    m["diff"] = (m["api_bsp"] - m["BSP_TRUE"]).abs()
    m["reldiff"] = m["diff"] / m["BSP_TRUE"]
    print(f"  identical to 1e-6 : {(m['diff'] < 1e-6).mean()*100:.1f}%")
    print(f"  within 1%         : {(m['reldiff'] < 0.01).mean()*100:.1f}%")
    print(f"  median abs diff   : {m['diff'].median():.4f}")
    print(f"  max abs diff      : {m['diff'].max():.4f}")
    bad = m.nlargest(8, "diff")
    if bad["diff"].max() > 0.01:
        print("\n  worst agreements:")
        print(bad[["HorseClean", "api_bsp", "BSP_TRUE", "diff", "Source"]]
              .to_string(index=False))
    wl = (m["won"].astype(int) == m["WinLose"].astype(int)).mean() * 100
    print(f"  winner flags agree: {wl:.1f}%")
