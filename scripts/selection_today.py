"""
TODAY'S SELECTIONS - the full five-rule system, on today's card
===============================================================
Produces the daily selection sheet the same way every day:

  python scripts/selection_today.py [YYYY-MM-DD]

Pipeline
  1. today's races + exact trip from the RacingTV API (no browser)
  2. each runner's OFFICIAL RATING read off the racecard page, from the
     dedicated "OR 96" cell - the old scraper read nearby numbers instead,
     which is why it produced marks of 163 and -63 lb swings
  3. form history from PRODB: rating last time out, rating at the last win,
     career-high rating, placings over the same exact trip
  4. last-time-out finishing position, preferring the fresher scraped results
     (PRODB stops on 2026-05-22; Scraped_Results runs to 2026-08-19)
  5. the five rules, plus the soft variant, written to
     reports/selections_<date>.csv

Rules (all required for HARD)
  1 mark falling          OR_now < OR last time out
  2 below last win mark   OR_now < OR when it last won
  3 below career best     OR_now < career-high OR
  4 proven at the trip    a prior 1st-3rd over the same exact distance
  5 ran top 4 LTO         finished 1-4 last time out
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import re
import sys
import warnings

import pandas as pd
import pyodbc

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rtv_api

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRO = (r"Driver={ODBC Driver 17 for SQL Server};"
       r"Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;")
SCRAPED = (r"Driver={ODBC Driver 17 for SQL Server};"
           r"Server=(localdb)\MSSQLLocalDB;Database=SCRAPED_PRODB;"
           r"Trusted_Connection=yes;")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# read the runner rows in document order: horse link, then its AGE / WGT / OR
# cells, so each "OR 96" cell belongs to the horse above it
GRAB_JS = r"""
() => {
  const rows = [];
  let cur = null;
  const walk = document.querySelectorAll('a[href*="/profiles/horse/"], div');
  for (const el of walk) {
    if (el.tagName === 'A' && (el.getAttribute('href') || '').includes('/profiles/horse/')) {
      const t = (el.innerText || '').split('\n')[0].trim();
      if (t) { cur = {name: t, or: null, wgt: null}; rows.push(cur); }
      continue;
    }
    if (!cur || el.children.length) continue;
    const t = (el.innerText || '').trim();
    if (!t || t.length > 8) continue;
    const m = /^OR\s*(\d{2,3})$/.exec(t);
    if (m) { cur.or = parseInt(m[1], 10); continue; }
    if (/^\d{1,2}-\d{1,2}$/.test(t) && !cur.wgt) { cur.wgt = t; }
  }
  return rows;
}
"""


def norm(s):
    return "".join(c for c in str(s or "").lower() if c.isalnum())


def strip_country(s):
    return norm(re.sub(r"\s*\([A-Za-z]{2,4}\)\s*$", "", str(s or "")))


def furlongs(text):
    """'2m 4f 5y' / '2m4f' / '7f' -> furlongs, ignoring yards."""
    t = str(text or "").lower().replace(" ", "")
    t = re.sub(r"\d+y", "", t)
    m = re.match(r"^(?:(\d+)m)?(?:(\d+)f)?$", t)
    if not m or (not m.group(1) and not m.group(2)):
        return None
    return int(m.group(1) or 0) * 8 + int(m.group(2) or 0)



async def read_card(date_str, races):
    """Official ratings off each racecard page, keyed by (course, time, horse)."""
    from playwright.async_api import async_playwright
    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(user_agent=UA,
                                  viewport={"width": 1500, "height": 1000})
        page = await ctx.new_page()
        for i, r in enumerate(races, 1):
            url = (f"https://www.racingtv.com/racecards/{date_str}/"
                   f"{r['course_slug']}/{r['hhmm']}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2.0)
                rows = await page.evaluate(GRAB_JS)
            except Exception as e:
                print(f"  ! {r['course_name']} {r['time']}: {e}")
                continue
            got = 0
            for row in rows:
                key = (norm(r["course_name"]), r["time"],
                       strip_country(row["name"]))
                if row["or"] is not None or row["wgt"]:
                    out[key] = (row["or"], row["wgt"])
                    got += 1 if row["or"] is not None else 0
            print(f"  [{i}/{len(races)}] {r['time']} {r['course_name'][:16]:16s} "
                  f"{len(rows):2d} runners, {got} with an OR")
        await b.close()
    return out


def history(conn, names):
    """Per-horse form from PRODB, keyed by normalised name."""
    h = pd.read_sql("SELECT H_No, H_Name FROM NEW_H", conn)
    h["k"] = h["H_Name"].map(strip_country)
    h = h[h["k"].isin(names)]
    if h.empty:
        return pd.DataFrame(), pd.DataFrame()
    ids = ", ".join(str(int(x)) for x in h["H_No"].unique())
    d = pd.read_sql(f"""
        SELECT HIR.HIR_HNo AS hid, RH.RH_DateTime, RH.RH_Exact_Race_Distance AS trip,
               HIR.HIR_OfficialRating AS OR_, HIR.HIR_PositionNo AS pos
        FROM dbo.NEW_HIR HIR JOIN dbo.NEW_RH RH ON RH.RH_RNo = HIR.HIR_RNo
        WHERE HIR.HIR_HNo IN ({ids}) AND HIR.HIR_OfficialRating > 0
    """, conn)
    d = d.merge(h[["H_No", "k"]], left_on="hid", right_on="H_No")
    d = d.sort_values("RH_DateTime")
    g = d.groupby("k")
    agg = pd.DataFrame({
        "LTO_OR": g["OR_"].last(),
        "LTO_POS_prodb": g["pos"].last(),
        "LTO_DT": g["RH_DateTime"].last(),
        "CAREER_MAX": g["OR_"].max(),
        "WIN_OR": g.apply(lambda x: x[x["pos"] == 1]["OR_"].iloc[-1]
                          if (x["pos"] == 1).any() else None),
        "Runs": g.size(),
    }).reset_index()
    return agg, d


def fresh_lto(names):
    """Last-time-out finishing position from the fresher scraped results."""
    try:
        c = pyodbc.connect(SCRAPED)
        d = pd.read_sql("SELECT HorseName, RaceDate, RaceTime, PosNo "
                        "FROM dbo.Scraped_Results "
                        "WHERE RaceDate >= '2026-05-01'", c)
        c.close()
    except pyodbc.Error:
        return pd.DataFrame()
    if d.empty:
        return d
    d["k"] = d["HorseName"].map(strip_country)
    d = d[d["k"].isin(names)].copy()
    d["pos"] = pd.to_numeric(d["PosNo"].astype(str).str.extract(r"(\d+)")[0],
                             errors="coerce")
    d = d.sort_values(["RaceDate", "RaceTime"])
    return (d.groupby("k").agg(LTO_POS_fresh=("pos", "last"),
                               LTO_DATE_fresh=("RaceDate", "last"))
            .reset_index())


def build(date_str, races, card_or, hist_agg, hist_rows, fresh):
    """Assemble today's runners and evaluate the five rules."""
    rows = []
    for r in races:
        try:
            detail = rtv_api.race_detail(date_str, r["course_slug"], r["hhmm"])
        except Exception:
            continue
        race = detail.get("race") or {}
        trip_f = furlongs(race.get("distance_formatted") or race.get("distance"))
        for run in rtv_api.runners_of(detail):
            name = run["horse_name"]
            k = strip_country(name)
            or_now, wgt = card_or.get((norm(r["course_name"]), r["time"], k),
                                      (None, None))
            rows.append({
                "RaceTime": r["time"], "Course": r["course_name"],
                "RaceTitle": str(race.get("title") or r["title"])[:120],
                "Trip": trip_f, "TripText": race.get("distance_formatted"),
                "Horse": name, "k": k, "Age": run["age"],
                "WgtCard": wgt or run["weight"], "OR_now": or_now,
                "Jockey": run["jockey"], "Trainer": run["trainer"],
                "Form": run["form"], "DSLR": run["days_since_run"],
                "Reserve": run["reserve"], "Status": run["status"],
            })
    c = pd.DataFrame(rows)
    if c.empty:
        return c
    c = c.merge(hist_agg, on="k", how="left")
    if fresh is not None and not fresh.empty:
        c = c.merge(fresh, on="k", how="left")
    else:
        c["LTO_POS_fresh"] = None
    c["LTO_POS"] = c["LTO_POS_fresh"].fillna(c.get("LTO_POS_prodb"))

    # --- validity of any mark-based comparison ------------------------------- #
    # A rating comparison is only meaningful when the rating we hold really is
    # the rating from the horse's LAST run.  PRODB stops on 2026-05-22, so for
    # anything that has run since, LTO_OR is stale and must not be used.
    c["LTO_DT"] = pd.to_datetime(c.get("LTO_DT"), errors="coerce")
    c["LTO_DATE_fresh"] = pd.to_datetime(c.get("LTO_DATE_fresh"), errors="coerce")
    ran_since = (c["LTO_DATE_fresh"].notna() & c["LTO_DT"].notna()
                 & (c["LTO_DATE_fresh"] > c["LTO_DT"]))
    # the rating we hold must also be dated when the DSLR says the last run was
    dslr = pd.to_numeric(c.get("DSLR"), errors="coerce")
    expected = pd.Timestamp(date_str) - pd.to_timedelta(dslr, unit="D")
    c["RatingCurrent"] = (~ran_since
                          & ((c["LTO_DT"] - expected).abs()
                             <= pd.Timedelta(days=3)))

    c["LTO_pos_n"] = pd.to_numeric(c["LTO_POS"], errors="coerce")
    c["DSLR_n"] = pd.to_numeric(c["DSLR"], errors="coerce")
    # a winner's mark goes UP: a computed fall on a LTO winner is a data error
    c["LTO_win"] = c["LTO_pos_n"] == 1
    # inside 7 days the horse carries a penalty, not its re-assessed mark
    c["PenaltyWindow"] = c["LTO_win"] & (c["DSLR_n"] <= 7)
    c["MarkValid"] = c["RatingCurrent"] & ~c["LTO_win"]

    proven = {}
    if hist_rows is not None and not hist_rows.empty:
        hr = hist_rows.copy()
        hr["f"] = hr["trip"].map(furlongs)
        hr["pos"] = pd.to_numeric(hr["pos"], errors="coerce")
        for k, grp in hr[hr["pos"].between(1, 3)].groupby("k"):
            proven[k] = dict(grp.groupby("f").size())
    c["TripPlacings"] = [proven.get(k, {}).get(f, 0)
                         for k, f in zip(c["k"], c["Trip"], strict=False)]

    c["R1_mark_falling"] = (c["OR_now"].notna() & c["LTO_OR"].notna()
                            & (c["OR_now"] < c["LTO_OR"]) & c["MarkValid"])
    c["R2_below_win"] = (c["OR_now"].notna() & c["WIN_OR"].notna()
                         & (c["OR_now"] < c["WIN_OR"]) & c["MarkValid"])
    c["R3_below_max"] = (c["OR_now"].notna() & c["CAREER_MAX"].notna()
                         & (c["OR_now"] < c["CAREER_MAX"]) & c["MarkValid"])
    c["R4_trip"] = c["TripPlacings"] > 0
    c["R5_lto_top4"] = pd.to_numeric(c["LTO_POS"], errors="coerce") \
        .between(1, 4).fillna(False)
    c["SEL_HARD"] = (c["R1_mark_falling"] & c["R2_below_win"] & c["R3_below_max"]
                     & c["R4_trip"] & c["R5_lto_top4"])
    c["SEL_SOFT"] = c["R1_mark_falling"] & c["R3_below_max"] & c["R4_trip"]
    c["OR_known"] = c["OR_now"].notna()
    return c.sort_values(["RaceTime", "Course", "SEL_HARD"],
                         ascending=[True, True, False])


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    print(f"=== TODAY'S SELECTIONS {day} ===")
    races = rtv_api.day_races(day)
    print(f"  {len(races)} UK/IRE races")
    card_or = asyncio.run(read_card(day, races))
    print(f"  ratings read off the racecards: {len(card_or)} runner rows")

    names = {k[2] for k in card_or}
    p = pyodbc.connect(PRO)
    agg, hr = history(p, names)
    p.close()
    fresh = fresh_lto(names)
    print(f"  history matched: {len(agg)} horses, fresh LTO positions: "
          f"{len(fresh)}")

    c = build(day, races, card_or, agg, hr, fresh)
    if c.empty:
        print("  no runners assembled")
        return 1
    out = os.path.join(ROOT, "reports", f"selections_{day}.csv")
    c.to_csv(out, index=False)
    known = int(c["OR_known"].sum())
    stale = int((~c["RatingCurrent"]).sum())
    winners = int(c["LTO_win"].sum())
    print(f"\n  runners {len(c):,}   with a rating {known:,} "
          f"({known / max(len(c), 1) * 100:.0f}%)")
    print(f"  rating provably stale (ran after PRODB's last record): "
          f"{stale:,}")
    print(f"  LTO winners (mark must rise, so no mark-based test): {winners:,}")
    print(f"  HARD selections: {int(c['SEL_HARD'].sum())}   "
          f"SOFT: {int(c['SEL_SOFT'].sum())}")
    print(f"  wrote {out}\n")
    for label, col in (("HARD", "SEL_HARD"), ("SOFT", "SEL_SOFT")):
        sel = c[c[col]]
        print(f"--- {label} ({len(sel)}) ---")
        if sel.empty:
            print("   none")
            continue
        show = [x for x in ["RaceTime", "Course", "Horse", "OR_now", "LTO_OR",
                            "WIN_OR", "CAREER_MAX", "TripPlacings", "LTO_POS",
                            "Jockey"] if x in sel]
        print(sel[show].head(30).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

