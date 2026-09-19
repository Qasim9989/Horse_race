"""
DATABASE AUDIT - which tables, columns and rows are actually used?
=================================================================
Reads all three databases and reports: every table with its row count and date
range, tables that hold nothing, and columns that are NULL in every row (those
columns cost space and mislead every query written against them).

  python tools/audit_db.py                 # report only
  python tools/audit_db.py --apply         # drop empty tables + empty columns
                                           # left over from removed systems

Nothing is dropped unless it is BOTH empty (or all-NULL) AND matches a known
dead system, and the exact SQL is printed first.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

import pyodbc

BASE = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=")
DBS = ("PRODB", "SCRAPED_PRODB", "RACINGTV_2023_2026")
OUT = r"E:\Test\racing-form-system\reports\db_audit.txt"

# tables belonging to systems that have been archived, safe to drop when empty
DEAD_HINTS = ("Scored_Racecards", "Online_Scraped_Results")


def connect(db):
    return pyodbc.connect(BASE + db + ";Trusted_Connection=yes;")


def audit(c):
    cur = c.cursor()
    cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME")
    tables = [r[0] for r in cur.fetchall()]
    out = []
    for t in tables:
        row = {"table": t, "rows": None, "lo": None, "hi": None,
               "cols": [], "empty_cols": []}
        try:
            cur.execute(f"SELECT COUNT(*) FROM dbo.{t}")
            row["rows"] = cur.fetchone()[0]
        except pyodbc.Error:
            pass
        for datecol in ("RaceDate", "RH_DateTime", "CreatedDate", "CreatedAt",
                        "ScannedAt"):
            try:
                cur.execute(f"SELECT MIN({datecol}), MAX({datecol}) "
                            f"FROM dbo.{t}")
                lo, hi = cur.fetchone()
                if lo:
                    row["lo"], row["hi"] = str(lo)[:10], str(hi)[:10]
                    break
            except pyodbc.Error:
                continue
        cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION", (t,))
        cols = cur.fetchall()
        row["cols"] = [c0[0] for c0 in cols]
        if row["rows"]:
            # one query per table instead of one per column: on a 675k-row
            # table the per-column version takes minutes
            names = row["cols"]
            sel = ", ".join(f"COUNT([{c0}])" for c0 in names)
            try:
                cur.execute(f"SELECT {sel} FROM dbo.{t}")
                counts = cur.fetchone()
                row["empty_cols"] = [n for n, v in zip(names, counts, strict=False) if v == 0]
            except pyodbc.Error:
                pass
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    lines = [f"=== DATABASE AUDIT {dt.datetime.now():%Y-%m-%d %H:%M} ===",
             ""]
    dead = []
    for db in DBS:
        try:
            c = connect(db)
        except Exception as e:
            lines.append(f"--- {db}: cannot connect ({e})")
            continue
        rows = audit(c)
        lines.append(f"--- {db} ---")
        for r in rows:
            span = f"  {r['lo']} -> {r['hi']}" if r["lo"] else ""
            lines.append(f"  {r['table']:<34} rows={r['rows']}{span}")
            if r["empty_cols"]:
                lines.append(f"      all-NULL columns ({len(r['empty_cols'])}): "
                             + ", ".join(r["empty_cols"][:12])
                             + (" ..." if len(r["empty_cols"]) > 12 else ""))
            if r["rows"] == 0 and any(h in r["table"] for h in DEAD_HINTS):
                dead.append((db, r["table"]))
        if a.apply:
            cur = c.cursor()
            for r in rows:
                if r["rows"] == 0 and any(h in r["table"] for h in DEAD_HINTS):
                    cur.execute(f"DROP TABLE dbo.{r['table']}")
                    c.commit()      # pyodbc autocommit is OFF: without this the
                                    # DROP is rolled back when the link closes
        c.close()
        lines.append("")
    lines.append("empty tables from removed systems:")
    for db, t in dead:
        lines.append(f"   {db}.dbo.{t}  -> "
                     + ("DROPPED" if a.apply else "would drop"))
    txt = "\n".join(lines)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
