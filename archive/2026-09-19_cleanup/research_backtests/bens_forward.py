"""
BEN'S BETS - FORWARD TEST LEDGER  (settled at the REAL Betfair BSP)
===================================================================
Settles Ben's published selections against the genuine Betfair Starting Price
held in dbo.BFSP (built by scripts/betfair_bsp_backfill.py).

Why this matters: his recorded prices are ~12-17% longer than the BSP, which is
where his entire edge lives.  Settling the SAME picks at the real BSP isolates
that.  It also runs independently of PRODB, so it works for dates PRODB does not
cover (PRODB is frozen at 2026-05-22).

This is the forward-test harness: each day, download the new Betfair file
    python scripts/betfair_bsp_backfill.py <from> <to> --nomap
then re-run this and the ledger updates itself.

USAGE
    python scripts/bens_forward.py                 # all picks
    python scripts/bens_forward.py 2026-08-01      # from a date
"""
import datetime
import os
import re
import sys

import numpy as np
import pandas as pd
import pyodbc

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

CONN_PROFORM = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=(localdb)\MSSQLLocalDB;"
    r"Database=PRODB;"
    r"Trusted_Connection=yes;"
)

PICK_GLOBS = [
    r"D:\RDB DATABASE\database\BensBets_Master_Feb_to_Sep_2026.csv",
    r"D:\RDB DATABASE\database\BensBets_Morning_Master_2026.csv",
    r"D:\RDB DATABASE\database\BensBets_February_2026.csv",
]
COMMISSION = 0.98
REPORT_DIR = r"E:\Test\racing-form-system\reports"


def clean_name(s):
    return "".join(c for c in str(s or "").lower() if c.isalnum())


def clean_course(s):
    s = str(s or "").strip()
    if " / " in s:
        s = s.split(" / ", 1)[1]
    s = re.sub(r"\s+\d{1,2}(st|nd|rd|th)\s+\w+$", "", s, flags=re.IGNORECASE)
    return clean_name(s)


def load_picks():
    """Ben's published picks.  Two layouts exist: a tidy master sheet and a
    month sheet with a header block above the table."""
    frames = []
    for path in PICK_GLOBS:
        if not os.path.exists(path):
            continue
        raw = pd.read_csv(path, low_memory=False, encoding="utf-8-sig",
                          header=None, dtype=str)
        # find the row that looks like a header
        hdr = None
        for i in range(min(15, len(raw))):
            cells = [str(c).strip().lower() for c in raw.iloc[i].tolist()]
            if any(c in ("horse name", "horse_name", "date") for c in cells):
                hdr = i
                break
        if hdr is None:
            continue
        d = pd.read_csv(path, low_memory=False, encoding="utf-8-sig",
                        skiprows=hdr)
        d.columns = [str(c).strip() for c in d.columns]
        low = {c.lower(): c for c in d.columns}
        cdate = low.get("date")
        chor = low.get("horse_name") or low.get("horse name") or low.get("horse")
        if not cdate or not chor:
            continue
        out = pd.DataFrame({
            "Date": pd.to_datetime(d[cdate], errors="coerce", dayfirst=True),
            "Horse": d[chor].astype(str).str.strip(),
            "Track": d[low.get("track", low.get("course", ""))]
                     .astype(str).str.strip() if ("track" in low or "course" in low) else "",
            "Time": d[low["time"]].astype(str).str.strip() if "time" in low else "",
            "BetType": d[low["bet_type"]].astype(str).str.strip()
                       if "bet_type" in low else "",
            "Odds": pd.to_numeric(d[low["odds"]], errors="coerce")
                    if "odds" in low else np.nan,
            "Stake": pd.to_numeric(d[low["stake_units"]], errors="coerce")
                     if "stake_units" in low else 1.0,
            "Src": os.path.basename(path),
        })
        frames.append(out)
    if not frames:
        raise SystemExit("no pick files found")
    d = pd.concat(frames, ignore_index=True)
    d = d.dropna(subset=["Date"])
    d = d[d["Horse"].str.len() > 1]
    bogus = {"short head", "head", "neck", "nose", "distance", "length",
             "dead heat", "half length"}
    d = d[~d["Horse"].str.lower().isin(bogus)]
    d["horse_k"] = d["Horse"].map(clean_name)
    d["course_k"] = d["Track"].map(clean_course)
    d["RaceDate"] = d["Date"].dt.date
    d["Stake"] = d["Stake"].fillna(1.0).replace(0, 1.0)
    d = d.drop_duplicates(subset=["RaceDate", "horse_k", "Src"]).reset_index(drop=True)
    return d


def load_bfsp(d_from, d_to):
    conn = pyodbc.connect(CONN_PROFORM)
    sql = ("SELECT RaceDate, CourseClean, HorseClean, BSP_TRUE, WinLose "
           "FROM dbo.BFSP WHERE RaceDate >= ? AND RaceDate <= ?")
    b = pd.read_sql(sql, conn, params=[d_from, d_to])
    conn.close()
    b["RaceDate"] = pd.to_datetime(b["RaceDate"]).dt.date
    b = b.rename(columns={"CourseClean": "course_k", "HorseClean": "horse_k"})
    return b


def attach_bsp(picks, bf):
    """Course key first, then horse+date only (the recorded time is unreliable
    and pre-2022 files use a different venue format)."""
    m = picks.copy().reset_index(drop=True)
    m["pid"] = m.index
    m["BSP_TRUE"] = np.nan
    m["WinLose"] = np.nan
    for keys in (["RaceDate", "course_k", "horse_k"], ["RaceDate", "horse_k"]):
        need = m["BSP_TRUE"].isna()
        if not need.any():
            break
        sub = m.loc[need, ["pid", *keys]].copy()
        right = bf.drop_duplicates(subset=keys)
        hit = (sub.merge(right[[*keys, "BSP_TRUE", "WinLose"]], on=keys, how="left")
                  .drop_duplicates(subset=["pid"]).set_index("pid")
                  .reindex(sub["pid"]))
        m.loc[need, ["BSP_TRUE", "WinLose"]] = hit[["BSP_TRUE", "WinLose"]].values
    return m


def settle(m):
    s = m[m["BSP_TRUE"].notna() & m["WinLose"].notna()].copy()
    s["won"] = (s["WinLose"] == 1).astype(int)
    st = s["Stake"].astype(float).values
    s["PL_taken"] = np.where(s["won"] == 1,
                             (s["Odds"] - 1.0) * COMMISSION * st, -st)
    s["PL_bsp"] = np.where(s["won"] == 1,
                           (s["BSP_TRUE"] - 1.0) * COMMISSION * st, -st)
    s["move_pct"] = (s["BSP_TRUE"] / s["Odds"] - 1.0) * 100
    return s


def _line(label, sub, col):
    if len(sub) == 0:
        return
    n = len(sub)
    staked = sub["Stake"].sum()
    pl = sub[col].sum()
    se = sub[col].std(ddof=1) / np.sqrt(n) if n > 1 else float("nan")
    t = sub[col].mean() / se if se else float("nan")
    print(f"  {label:<34} n={n:>4}  strike {100*sub['won'].mean():5.1f}%  "
          f"staked {staked:>7.2f}u  P/L {pl:>+8.2f}u  ROI {100*pl/staked:>+7.2f}%  "
          f"t {t:>+5.2f}")


def report(s, m):
    print("\n" + "=" * 100)
    print("  BEN'S BETS - SETTLED AGAINST THE REAL BETFAIR BSP")
    print("=" * 100)
    print(f"  picks loaded                  : {len(m):>7,}")
    print(f"  matched to a real BSP         : {len(s):>7,}  "
          f"({len(s)/max(len(m),1)*100:.1f}%)")
    if len(s) == 0:
        print("  nothing to settle - run the BSP backfill for these dates first:")
        print("     python scripts/betfair_bsp_backfill.py <from> <to> --nomap")
        return
    mx = s["move_pct"].median()
    print(f"  median HIS price vs real BSP  : {mx:>+7.1f}%   "
          f"(NEGATIVE = he took a LONGER price than the exchange)")
    print(f"  median his odds {s['Odds'].median():.2f}   "
          f"median BSP {s['BSP_TRUE'].median():.2f}")
    print()
    print("  -- settled at the price HE RECORDED --")
    _line("all matched picks", s, "PL_taken")
    print("  -- settled at the REAL BETFAIR BSP (win only) --")
    _line("all matched picks", s, "PL_bsp")
    print("\n  -- by bet type, at the REAL BSP --")
    for bt, g in s.groupby("BetType"):
        if len(g) >= 20:
            _line(str(bt)[:32], g, "PL_bsp")
    print("\n  -- by year, at the REAL BSP --")
    s["Yr"] = pd.to_datetime(s["RaceDate"]).dt.year
    for y, g in s.groupby("Yr"):
        _line(str(y), g, "PL_bsp")


def main():
    d_from = None
    if len(sys.argv) > 1 and re.match(r"^\d{4}-\d{2}-\d{2}$", sys.argv[1]):
        d_from = datetime.date.fromisoformat(sys.argv[1])
    picks = load_picks()
    if d_from:
        picks = picks[picks["RaceDate"] >= d_from]
    if picks.empty:
        raise SystemExit("no picks in range")
    lo, hi = picks["RaceDate"].min(), picks["RaceDate"].max()
    print(f"picks {len(picks):,}   {lo} -> {hi}")
    bf = load_bfsp(lo, hi)
    print(f"BFSP rows in range: {len(bf):,}")
    m = attach_bsp(picks, bf)
    s = settle(m)
    report(s, m)
    os.makedirs(REPORT_DIR, exist_ok=True)
    out = os.path.join(REPORT_DIR, "bens_forward_ledger.csv")
    s.to_csv(out, index=False)
    print(f"\n  ledger -> {out}")


if __name__ == "__main__":
    main()
