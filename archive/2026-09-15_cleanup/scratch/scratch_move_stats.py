"""How often do BOOKMAKER prices actually change? (from the stored snapshots)

Compares consecutive snapshot pairs in dbo.BookOdds: what share of
(runner, account) prices changed, how big the moves were, and how that splits
by account and by price band.
"""
import numpy as np
import pandas as pd
import pyodbc

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")
DAY = "2026-09-15"

c = pyodbc.connect(CONN)
d = pd.read_sql("SELECT * FROM dbo.BookOdds WHERE RaceDate = ?", c, params=[DAY])
c.close()
d = d[(d["RunnerStatus"] == "entered") & (d["IsReserve"] == 0)
      & (d["PriceDecimal"] > 1)]

snaps = sorted(d["SnapshotAt"].unique())
print(f"snapshots stored: {len(snaps)}")
for s in snaps:
    n = d[d["SnapshotAt"] == s]
    print(f"   {s}   prices {len(n):5d}   races "
          f"{n.groupby(['CourseClean', 'RaceTime']).ngroups:2d}")

keys = ["RaceDate", "CourseClean", "RaceTime", "HorseClean", "BookmakerName"]
rows = []
for a, b in zip(snaps, snaps[1:]):
    A = d[d["SnapshotAt"] == a].set_index(keys)["PriceDecimal"]
    B = d[d["SnapshotAt"] == b].set_index(keys)["PriceDecimal"]
    common = A.index.intersection(B.index)
    if not len(common):
        continue
    A, B = A.loc[common], B.loc[common]
    mins = (pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() / 60
    ch = A != B
    rows.append({
        "from": str(a), "to": str(b), "mins": mins, "pairs": len(common),
        "changed": int(ch.sum()), "pct": ch.mean() * 100,
        "median_abs_pct": ((B - A).abs() / A)[ch].median() * 100 if ch.any()
        else 0.0,
        "up": int(((B > A) & ch).sum()), "down": int(((B < A) & ch).sum()),
    })

t = pd.DataFrame(rows)
print("\n=== change between consecutive snapshots ===")
print(t[["from", "to", "mins", "pairs", "changed", "pct", "median_abs_pct",
         "up", "down"]].to_string(index=False))

if not t.empty:
    first, last = snaps[0], snaps[-1]
    A = d[d["SnapshotAt"] == first].set_index(keys)["PriceDecimal"]
    B = d[d["SnapshotAt"] == last].set_index(keys)["PriceDecimal"]
    common = A.index.intersection(B.index)
    A, B = A.loc[common], B.loc[common]
    ch = A != B
    hrs = (pd.Timestamp(last) - pd.Timestamp(first)).total_seconds() / 3600
    print(f"\n=== opening ({first}) to latest ({last}) = {hrs:.2f}h ===")
    print(f"  prices compared        {len(common):,}")
    print(f"  changed at all         {ch.sum():,}  ({ch.mean()*100:.1f}%)")
    moved = ((B - A) / A)[ch]
    print(f"  of those, longer (drift)  {(moved > 0).mean()*100:.1f}%")
    print(f"            shorter (in)    {(moved < 0).mean()*100:.1f}%")
    print(f"  median absolute move   {moved.abs().median()*100:.1f}%")
    print(f"  biggest moves          {moved.abs().nlargest(5).round(3).tolist()}")

    # per account
    per = []
    for bk, g in d.groupby("BookmakerName"):
        A = g[g["SnapshotAt"] == first].set_index(keys)["PriceDecimal"]
        B = g[g["SnapshotAt"] == last].set_index(keys)["PriceDecimal"]
        cm = A.index.intersection(B.index)
        if not len(cm):
            continue
        A, B = A.loc[cm], B.loc[cm]
        chg = A != B
        per.append({"account": bk, "prices": len(cm),
                    "changed_pct": chg.mean() * 100,
                    "median_move_pct": (((B - A).abs() / A)[chg].median() * 100
                                        if chg.any() else 0.0)})
    print("\n=== per account (opening -> latest) ===")
    print(pd.DataFrame(per).sort_values("changed_pct", ascending=False)
          .round(2).to_string(index=False))

    # by price band
    bands = [0, 3, 6, 12, 25, 60, 1e9]
    labels = ["<3", "3-6", "6-12", "12-25", "25-60", "60+"]
    band = pd.DataFrame({"A": A, "B": B})
    band["band"] = pd.cut(band["A"], bands, labels=labels)
    band["changed"] = band["A"] != band["B"]
    print("\n=== by opening price band ===")
    g = band.groupby("band", observed=True).agg(
        prices=("changed", "size"), changed_pct=("changed", "mean"))
    g["changed_pct"] *= 100
    print(g.round(1).to_string())
