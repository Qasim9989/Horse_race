"""BACKTEST: the AI System rule ("Power Rank #1") on the real data it uses.

Source: the Racing Post master database (D:\\RacingPost_Horse\\racingpost_master.db)
- that is what the AI rule is built from, together with RacingTV results.

The rule (scripts/sync_results_ledger.py:160-172, 216-218) scores EVERY runner:
    pos_score    30 if last time out won, 20 if 2nd/3rd, else 5
    rating_score min(career_max * 0.4, 40)      <- the "TS 95 vs Fav 82" labels
    recency      20 if <=21 days, 15 if <=45, else 5
and then logs ONE bet per race: the highest scorer. No threshold.

Every feature here is built from a horse's PRIOR runs only, so there is no
look-ahead. Settlement is at the Racing Post SP.
"""

import re
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

RP_DB = r"D:\RacingPost_Horse\racingpost_master.db"
TEST_FROM = "2024-01-01"      # evaluate this window (history before it is used as context)


def parse_sp(value):
    """'3/1', '15/8F', '2.5' -> decimal."""
    s = str(value or "").strip().lower().rstrip("fjc")
    if not s:
        return None
    if s in ("evens", "evs", "1/1"):
        return 2.0
    m = re.match(r"^(\d+)\s*/\s*(\d+)$", s)
    if m:
        try:
            return round(1 + float(m.group(1)) / float(m.group(2)), 2)
        except ZeroDivisionError:
            return None
    try:
        v = float(s)
        return v if v > 1 else None
    except ValueError:
        return None


def base_name(value):
    s = str(value or "").strip().lower()
    s = re.sub(r"\s*\([a-z]{2,4}\)\s*$", "", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def pos_int(value):
    s = re.sub(r"[^0-9]", "", str(value or ""))
    return int(s) if s else None


def load():
    import sqlite3
    cn = sqlite3.connect(RP_DB)
    df = pd.read_sql(
        """SELECT race_id, race_date, meeting, race_time, race_title, race_class,
                  horse_name, finish_pos, weight_lbs, official_rating, topspeed, rpr, sp_odds
           FROM race_results WHERE race_date >= '2022-06-01'""", cn)
    cn.close()
    print(f"  loaded {len(df):,} runs")
    return df


def build_features(df):
    df = df.copy()
    df["hkey"] = df["horse_name"].map(base_name)
    df["rdate"] = pd.to_datetime(df["race_date"], errors="coerce")
    df["sp"] = df["sp_odds"].map(parse_sp)
    for col in ("topspeed", "rpr", "official_rating", "weight_lbs"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["pos"] = df["finish_pos"].map(pos_int)
    df["handicap"] = df["race_title"].fillna("").str.lower().str.contains("handicap")
    df["field"] = df.groupby("race_id")["race_id"].transform("size")
    df = df.sort_values(["hkey", "rdate", "race_time"]).reset_index(drop=True)

    g = df.groupby("hkey", sort=False)
    df["prev_date"] = g["rdate"].shift(1)
    df["lto_pos"] = g["pos"].shift(1)
    df["dslr"] = (df["rdate"] - df["prev_date"]).dt.days
    # career-best figures STRICTLY before this run
    df["career_ts"] = g["topspeed"].apply(lambda s: s.shift(1).cummax()).reset_index(level=0, drop=True)
    df["career_rpr"] = g["rpr"].apply(lambda s: s.shift(1).cummax()).reset_index(level=0, drop=True)

    pos_score = np.where(df["lto_pos"] == 1, 30.0, np.where(df["lto_pos"].isin([2, 3]), 20.0, 5.0))
    rating_ts = np.minimum(df["career_ts"].fillna(0) * 0.4, 40.0)
    rating_rpr = np.minimum(df["career_rpr"].fillna(0) * 0.4, 40.0)
    recency = np.where(df["dslr"] <= 21, 20.0, np.where(df["dslr"] <= 45, 15.0, 5.0))
    df["power_ts"] = pos_score + rating_ts + recency
    df["power_rpr"] = pos_score + rating_rpr + recency
    # only rankable runners: a previous run must exist
    df["rankable"] = df["lto_pos"].notna() & df["sp"].notna()
    return df


def place_fraction(field, handicap):
    """Standard each-way terms: 1/5 for 8+, 1/4 for 16+ handicaps."""
    if field >= 16 and handicap:
        return 0.25
    return 0.20 if field >= 8 else 0.25


def settle(picks):
    p = picks[picks["sp"].notna() & (picks["sp"] > 1)].copy()
    if p.empty:
        return None
    p["frac"] = [place_fraction(f, h) for f, h in zip(p["field"], p["handicap"], strict=False)]
    p["place_odds"] = 1 + (p["sp"] - 1) * p["frac"]
    paid = np.where((p["field"] >= 16) & p["handicap"], 4, np.where(p["field"] >= 8, 3, 2))
    won = p["pos"] == 1
    placed = p["pos"].notna() & (p["pos"] <= paid)
    p["win_pl"] = (p["sp"] - 1).where(won, -1.0)
    p["ew_pl"] = -2.0
    p.loc[won, "ew_pl"] = (p["sp"] - 1) + (p["place_odds"] - 1)
    p.loc[~won & placed, "ew_pl"] = (p["place_odds"] - 1) - 1.0
    n, days = len(p), p["rdate"].nunique()
    return {"bets": n, "days": days, "per_day": n / days if days else 0,
            "win_pct": won.mean() * 100, "place_pct": placed.mean() * 100,
            "avg_sp": p["sp"].mean(), "win_roi": p["win_pl"].sum() / n * 100,
            "ew_roi": p["ew_pl"].sum() / (n * 2) * 100}


def show(label, picks):
    s = settle(picks)
    if s is None:
        print(f"  {label:<40s} no settled bets")
        return
    print(f"  {label:<40s} bets={s['bets']:>6,} {s['per_day']:>5.1f}/day  "
          f"win%={s['win_pct']:>5.1f} plc%={s['place_pct']:>5.1f} avgSP={s['avg_sp']:>6.2f}  "
          f"WIN {s['win_roi']:>+7.2f}%  EW {s['ew_roi']:>+7.2f}%")


def top_per_race(df, pcol):
    ranked = df[df["rankable"]].sort_values(["race_id", pcol], ascending=[True, False])
    picks = ranked.groupby("race_id", as_index=False).head(1).copy()
    ranked = ranked.copy()
    ranked["r"] = ranked.groupby("race_id").cumcount()
    first = ranked[ranked.r == 0].set_index("race_id")[pcol]
    second = ranked[ranked.r == 1].set_index("race_id")[pcol]
    margin = (first - second.reindex(first.index).fillna(first)).rename("margin").reset_index()
    picks = picks.merge(margin, on="race_id", how="left")
    picks["margin"] = picks["margin"].fillna(0.0)
    # SP rank within the race (1 = favourite)
    ranked2 = df.sort_values(["race_id", "sp"], ascending=[True, True]).copy()
    ranked2["sp_rank"] = ranked2.groupby("race_id").cumcount() + 1
    picks = picks.merge(ranked2[["race_id", "horse_name", "sp_rank"]], on=["race_id", "horse_name"], how="left")
    return picks


def favourite_per_race(df):
    r = df[df["sp"].notna()].sort_values(["race_id", "sp"])
    return r.groupby("race_id", as_index=False).head(1).copy()


def main():
    print("=" * 122)
    print("  AI SYSTEM (Power Rank #1) BACKTEST - Racing Post data, features from PRIOR runs only")
    print("=" * 122)
    df = build_features(load())
    window = df[df["rdate"] >= TEST_FROM].copy()
    print(f"  evaluation window: {TEST_FROM} -> {window.rdate.max().date()}  "
          f"({window.race_id.nunique():,} races, {len(window):,} runners)")

    picks_ts = top_per_race(window, "power_ts")
    print(f"  picks made: {len(picks_ts):,} (race had a rankable runner)")
    print()
    show("ALL RACES (current rule: 1 per race)", picks_ts)
    show("  control: back the favourite every race", favourite_per_race(window))

    print()
    print("  --- by year ---")
    for yr, g in picks_ts.groupby(picks_ts["rdate"].dt.year):
        show(f"  {yr}", g)

    print()
    print("  --- race shape (pre-race knowable) ---")
    for lo, hi, lab in ((0, 7, "<8 runners"), (8, 11, "8-11"), (12, 15, "12-15"), (16, 99, "16+")):
        show(f"  {lab}", picks_ts[(picks_ts["field"] >= lo) & (picks_ts["field"] <= hi)])
    show("  handicaps only", picks_ts[picks_ts["handicap"]])
    show("  non-handicaps only", picks_ts[~picks_ts["handicap"]])

    print()
    print("  --- confidence / form filters (all knowable before the off) ---")
    for thr in (0, 5, 10, 15):
        show(f"  power margin > {thr} over 2nd", picks_ts[picks_ts["margin"] > thr])
    show("  pick won last time out", picks_ts[picks_ts["lto_pos"] == 1])
    show("  pick placed LTO (1-3)", picks_ts[picks_ts["lto_pos"].isin([1, 2, 3])])
    for lo, hi, lab in ((0, 21, "DSLR <=21"), (22, 45, "DSLR 22-45"), (46, 9999, "DSLR 46+")):
        show(f"  {lab}", picks_ts[picks_ts["dslr"].between(lo, hi)])
    show("  career TS >= 90", picks_ts[picks_ts["career_ts"] >= 90])

    print()
    print("  --- market position (DIAGNOSTIC ONLY - SP is known only at the off) ---")
    show("  pick is the favourite", picks_ts[picks_ts["sp_rank"] == 1])
    show("  pick is 2nd or 3rd in the market", picks_ts[picks_ts["sp_rank"].isin([2, 3])])
    show("  pick is 4th+ in the market", picks_ts[picks_ts["sp_rank"] >= 4])
    for sp_lo, sp_hi, sp_lab in ((1.0, 3.0, "SP 1-3"), (3.0, 6.0, "SP 3-6"),
                                 (6.0, 12.0, "SP 6-12"), (12.0, 1e9, "SP 12+")):
        show(f"  {sp_lab}", picks_ts[(picks_ts["sp"] >= sp_lo) & (picks_ts["sp"] < sp_hi)])

    print()
    print("  --- best combinations (tradeable parts only) ---")
    show("  margin>5 + handicaps", picks_ts[(picks_ts["margin"] > 5) & picks_ts["handicap"]])
    show("  margin>5 + 8-15 runners", picks_ts[(picks_ts["margin"] > 5) & picks_ts["field"].between(8, 15)])
    show("  LTO win + margin>5", picks_ts[(picks_ts["lto_pos"] == 1) & (picks_ts["margin"] > 5)])
    show("  LTO win + handicap + 8-15", picks_ts[(picks_ts["lto_pos"] == 1) & picks_ts["handicap"]
                                                & picks_ts["field"].between(8, 15)])

    print()
    print("  --- sanity: rank by RPR instead of TS ---")
    show("ALL RACES (power_rpr variant)", top_per_race(window, "power_rpr"))

    print()
    print("  --- how many runners qualify per race (why it is a blanket) ---")
    per_race = picks_ts.groupby("race_id").size()
    print(f"  races: {len(per_race):,} | picks per race: {per_race.mean():.2f} "
          f"(always 1 = a bet in every race)")


if __name__ == "__main__":
    main()

