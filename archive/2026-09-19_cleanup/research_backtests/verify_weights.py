"""Verify the weight backfill: coverage per year, per race, and sanity.

    python scripts/verify_weights.py
"""
from __future__ import annotations

import sys

import pandas as pd
import pyodbc

DBS = ("RACINGTV_2023_2026", "SCRAPED_PRODB")


def main():
    frames = []
    for db in DBS:
        try:
            c = pyodbc.connect(
                "Driver={ODBC Driver 17 for SQL Server};"
                "Server=(localdb)\\MSSQLLocalDB;Database=" + db +
                ";Trusted_Connection=yes;Connection Timeout=30;")
        except Exception as e:
            print(f"{db}: cannot connect ({e})")
            continue
        cur = c.cursor()
        cur.execute("""
            SELECT YEAR(RaceDate) AS year_,
                   COUNT(DISTINCT CONVERT(varchar(10), RaceDate, 120) + '|'
                         + CAST(RaceTime AS varchar(10)) + '|' + CourseName)
                       AS races,
                   COUNT(Weight) AS with_weight,
                   COUNT(DISTINCT Weight) AS distinct_weights
            FROM dbo.Scraped_Results
            GROUP BY YEAR(RaceDate) ORDER BY year_
        """)
        rows = [tuple(r) for r in cur.fetchall()]
        cols = [d[0] for d in (cur.description or [])]
        c.close()
        if not rows:
            print(f"{db}: no rows")
            continue
        f = pd.DataFrame(rows, columns=cols)
        f.columns = ["year_", "races", "with_weight", "distinct_weights"]
        f.insert(0, "db", db)
        frames.append(f)
    if not frames:
        return 1
    d = pd.concat(frames, ignore_index=True)
    d["weights_per_race"] = (d["with_weight"] /
                             d["races"].replace(0, pd.NA)).round(1)
    print("\n=== weight coverage by year ===")
    print(d.to_string(index=False))

    # per-race distribution on the live DB, where the backfill is running
    c = pyodbc.connect(
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=(localdb)\\MSSQLLocalDB;Database=RACINGTV_2023_2026;"
        "Trusted_Connection=yes;Connection Timeout=30;")
    cur = c.cursor()
    cur.execute("""
        SELECT AVG(CAST(n AS float)) AS avg_per_race,
               MIN(n) AS min_per_race, MAX(n) AS max_per_race,
               COUNT(*) AS races_with_weights
        FROM (SELECT COUNT(Weight) AS n FROM dbo.Scraped_Results
              WHERE Weight IS NOT NULL
              GROUP BY RaceDate, RaceTime, CourseName) x
    """)
    avg, lo, hi, n = cur.fetchone()
    cur.execute("SELECT TOP 5 RaceDate, CourseName, HorseName, Weight "
                "FROM dbo.Scraped_Results WHERE Weight IS NOT NULL "
                "ORDER BY RaceDate DESC")
    sample = cur.fetchall()
    c.close()
    print("\n=== per-race check (live DB) ===")
    print(f"races with weights : {n}")
    print(f"weights per race   : avg {avg and round(avg, 1)}, "
          f"min {lo}, max {hi}")
    print("latest rows:")
    for r in sample:
        print(f"   {str(r[0])[:10]}  {r[1]:<18} {r[2]:<24} {r[3]}")
    ok = avg and avg >= 6
    print("\nverdict: " + ("OK - weights look per-runner (not collapsed)"
                           if ok else
                           "SUSPECT - fewer weights per race than expected"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
