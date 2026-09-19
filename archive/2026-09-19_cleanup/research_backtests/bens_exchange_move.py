"""For Ben's picks: does the EXCHANGE price itself move (shorten) before the off?
If the exchange 15-min price is much longer than the BSP, a back-to-lay captures it."""
import sqlite3
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

CSV = r"D:\RDB DATABASE\database\BensBets_Master_Feb_to_Sep_2026.csv"
DB = r"D:\RDB\master_rdb.db"


def clean(n):
    return "".join(c for c in str(n or "").lower() if c.isalnum())


ben = pd.read_csv(CSV, dtype=str)
ben["date"] = pd.to_datetime(ben["Date"], dayfirst=True, errors="coerce").dt.date
ben["horse_k"] = ben["Horse_Name"].map(clean)
ben["Odds"] = pd.to_numeric(ben["Odds"], errors="coerce")

con = sqlite3.connect(DB)
cols = [r[1] for r in con.execute("PRAGMA table_info(raw_results_ticks)")]
print("cols:", [c for c in cols if any(k in c.lower() for k in
      ["date", "horse", "bsp", "min", "time", "post", "track"])])
want = [c for c in cols if c.lower() in ("date", "horse", "track name", "bsp",
        "15 mins", "5 mins", "post time", "min price", "max price")]
q = f'SELECT {", ".join(chr(34)+c+chr(34) for c in want)} FROM raw_results_ticks'
d = pd.read_sql(q, con)
con.close()
d.columns = [c.strip().lower().replace(" ", "_") for c in d.columns]
d["date"] = pd.to_datetime(d["date"], errors="coerce").dt.date
d["horse_k"] = d["horse"].map(clean)
print(f"db rows: {len(d):,}")

m = ben.merge(d, on=["date", "horse_k"], how="inner").drop_duplicates(
    subset=["Date", "Horse_Name"])
print(f"matched picks: {len(m)}")
for a, b in (("15_mins", "bsp"), ("5_mins", "bsp"), ("post_time", "bsp")):
    if a not in m.columns or b not in m.columns:
        continue
    v = m[[a, b]].apply(pd.to_numeric, errors="coerce").dropna()
    v = v[(v[a] > 1) & (v[b] > 1)]
    if len(v) < 10:
        continue
    v["r"] = v[b] / v[a]
    print(f"  {a:<10}: n={len(v):>4}  median BSP/{a} {v['r'].median():.3f}  "
          f"shortened {(v['r']<1).mean()*100:4.1f}%  "
          f"median move {(1-v['r'].median())*100:+.1f}%")
