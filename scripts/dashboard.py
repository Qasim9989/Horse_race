"""
LIVE BETTING DASHBOARD
======================
Visual front-end for the RacingTV/Oddschecker bookmaker feed
(PRODB.dbo.BookOdds) and the real Betfair SP (PRODB.dbo.BFSP).

    double-click dashboard.bat
    or:  python -m streamlit run scripts/dashboard.py

Tabs
  Live prices    - KPI tiles, book margins, who holds the best price, overlays
  Movement       - how each runner's price moved between snapshots (per race)
  Book vs BSP    - best available price against the real exchange SP
  Data health    - snapshot coverage, gaps, and what to run next

Everything is read-only; the dashboard never writes to the database.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import warnings

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pyodbc
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import itertools

import book_odds as bo_mod
import rtv_api

CONN = bo_mod.CONN
ACCENT = "#e1087e"          # RacingTV pink
GOOD = "#1b998b"
BAD = "#d1495b"

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
warnings.filterwarnings("ignore",
                        message="Thread 'MainThread': missing ScriptRunContext",
                        category=UserWarning)

st.set_page_config(page_title="Racing odds dashboard", layout="wide",
                   initial_sidebar_state="collapsed")


# --------------------------------------------------------------------------- #
#  Data access
# --------------------------------------------------------------------------- #
def clean_name(s):
    """Same key normalisation the rest of the project uses (rtv_api.clean_name)."""
    return rtv_api.clean_name(s)


@st.cache_data(ttl=30, show_spinner=False)
def load_day(day):
    """(book odds, betfair sp) frames for one date."""
    conn = pyodbc.connect(CONN)
    try:
        o = pd.read_sql("SELECT * FROM dbo.BookOdds WHERE RaceDate = ?",
                        conn, params=[day])
        b = pd.read_sql("SELECT CourseClean, HorseClean, RaceTime, BSP_TRUE, "
                        "WinLose FROM dbo.BFSP WHERE RaceDate = ?",
                        conn, params=[day])
    finally:
        conn.close()
    return o, b


@st.cache_data(ttl=30, show_spinner=False)
def load_betfair_live(day):
    """Newest Betfair exchange snapshot for a date (usable runners only)."""
    conn = pyodbc.connect(CONN)
    try:
        ex = pd.read_sql("SELECT * FROM dbo.BetfairLive WHERE RaceDate = ?",
                         conn, params=[day])
    finally:
        conn.close()
    if ex.empty:
        return ex
    fresh = ex.groupby(["VenueClean", "MarketID"])["SnapshotAt"].transform("max")
    ex = ex[ex["SnapshotAt"] == fresh].copy()
    return ex[(ex["RunnerStatus"] == "ACTIVE") & ex["Back1"].notna()]


@st.cache_data(ttl=60, show_spinner=False)
def available_dates():
    conn = pyodbc.connect(CONN)
    try:
        d = pd.read_sql("SELECT DISTINCT RaceDate FROM dbo.BookOdds "
                        "ORDER BY RaceDate", conn)
    finally:
        conn.close()
    return [str(x) for x in d["RaceDate"].tolist()]


def latest_per_race(df, keys=("RaceDate", "CourseClean", "RaceTime")):
    """Keep only the newest snapshot for each race (races are polled unevenly)."""
    if df.empty:
        return df
    k = list(keys)
    fresh = df.groupby(k)["SnapshotAt"].transform("max")
    return df[df["SnapshotAt"] == fresh].copy()


def live_only(df):
    return df[(df["RunnerStatus"] == "entered") & (df["IsReserve"] == 0)
              & (df["PriceDecimal"] > 1)].copy()


def book_margins(cur):
    """Median overround per race, per book."""
    if cur.empty:
        return pd.Series(dtype=float)
    g = (cur.groupby(["RaceDate", "CourseClean", "RaceTime", "BookmakerName"])
         ["PriceDecimal"].apply(lambda s: sum(1.0 / v for v in s if v > 1))
         .reset_index().rename(columns={"PriceDecimal": "Overround"}))
    return g.groupby("BookmakerName")["Overround"].median().sort_values()


def best_share(cur):
    """Per account: how often it holds the best price + edge over next best."""
    if cur.empty:
        return pd.DataFrame()
    keys = ["RaceDate", "CourseClean", "RaceTime", "HorseClean"]
    c = cur.copy()
    c["rank"] = c.groupby(keys)["PriceDecimal"].rank(method="first",
                                                     ascending=False)
    best = c[c["rank"] == 1].set_index(keys)
    second = c[c["rank"] == 2].set_index(keys)["PriceDecimal"]
    df = best[["PriceDecimal", "BookmakerName", "HorseName", "CourseName"]].copy()
    df["Second"] = df.index.map(second)
    df["Edge"] = df["PriceDecimal"] / df["Second"]
    g = (df.groupby("BookmakerName")
         .agg(Best=("PriceDecimal", "size"), Edge=("Edge", "mean"))
         .sort_values("Best", ascending=False))
    g["Share"] = g["Best"] / max(len(df), 1) * 100
    g["EdgePct"] = (g["Edge"] - 1) * 100
    return g



def best_rows(cur):
    """One row per runner: the best price available and the account holding it."""
    if cur.empty:
        return cur
    keys = ["RaceDate", "CourseClean", "RaceTime", "HorseClean"]
    c = cur.copy()
    c["rank"] = c.groupby(keys)["PriceDecimal"].rank(method="first",
                                                     ascending=False)
    best = c[c["rank"] == 1].copy()
    best["Second"] = best.set_index(keys).index.map(
        c[c["rank"] == 2].set_index(keys)["PriceDecimal"])
    best["ShopEdge"] = best["PriceDecimal"] / best["Second"]
    best["NBooks"] = c.groupby(keys)["BookmakerName"].transform("size")
    return best


def best_market_margin(cur):
    """Median, across races, of the best-of-market overround.

    Must be computed per race (sum of 1/best price within a race), never across
    the whole day - that would add up 38 separate books.
    """
    top = best_rows(cur)
    if top.empty:
        return None
    g = (top.groupby(["RaceDate", "CourseClean", "RaceTime"])["PriceDecimal"]
         .apply(lambda s: sum(1.0 / v for v in s if v > 1)))
    return float(g.median())



def fig_margin_bar(marg, best_margin):
    d = marg.reset_index().sort_values("Overround")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=(d["Overround"] - 1) * 100, y=d["BookmakerName"], orientation="h",
        marker_color=[BAD if v > 0.22 else ("#f0a202" if v > 0.20 else GOOD)
                      for v in d["Overround"]],
        text=[f"{(v-1)*100:+.1f}%" for v in d["Overround"]],
        textposition="outside", hovertemplate="%{y}: %{x:.2f}%<extra></extra>"))
    if best_margin:
        fig.add_trace(go.Scatter(
            x=[(best_margin - 1) * 100, (best_margin - 1) * 100],
            y=[-0.5, len(d) - 0.5], mode="lines",
            line={"color": ACCENT, "dash": "dash"},
            name=f"best of market ({(best_margin - 1) * 100:+.1f}%)"))
    fig.update_layout(height=110 + 26 * len(d), margin={"l": 4, "r": 70, "t": 30, "b": 4},
                      xaxis_title="bookmaker margin % (lower = better for you)",
                      yaxis_title="", showlegend=True,
                      legend={"orientation": "h", "y": 1.12})
    return fig


def fig_share_bar(share):
    d = share.reset_index().sort_values("Share")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=d["Share"], y=d["BookmakerName"], orientation="h",
        marker_color=[ACCENT if i >= len(d) - 2 else "#8a8a9e"
                      for i in range(len(d))],
        text=[f"{s:.0f}%  (+{e:.1f}% edge)" for s, e in zip(d["Share"],
                                                            d["EdgePct"], strict=False)],
        textposition="outside",
        hovertemplate="%{y}: best on %{x:.1f}% of runners<extra></extra>"))
    fig.update_layout(height=110 + 26 * len(d),
                      margin={"l": 4, "r": 150, "t": 30, "b": 4},
                      xaxis_title="% of runners where this account has the "
                      "best price", showlegend=False)
    return fig


def fig_ratio_hist(ratio, label):
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=ratio, nbinsx=40, marker_color=ACCENT,
                               hovertemplate="ratio %{x:.2f}: %{y}"
                               "<extra></extra>"))
    fig.add_vline(x=1.0, line_dash="dash", line_color=GOOD,
                  annotation_text="par with Betfair")
    fig.update_layout(height=320, margin={"l": 4, "r": 4, "t": 30, "b": 4},
                      xaxis_title=f"{label} / Betfair SP",
                      yaxis_title="runners", showlegend=False)
def auto_picks(top, exl, min_edge=0.0, min_price=None, max_price=None,
               upcoming_only=False, day=None, exclude_ben=None):
    """Every runner today where the best price you can take beats Betfair.

    `top` is one row per runner (best book price + which account); `exl` is the
    Betfair snapshot.  Only runners in a real, two-sided, traded market count -
    a lay of 1000.0 is Betfair's cap and means nobody is laying.
    """
    if top.empty or exl.empty:
        return pd.DataFrame()
    ex = exl[(exl["RunnerStatus"] == "ACTIVE") & exl["Back1"].notna()
             & exl["Lay1"].notna() & (exl["Lay1"] < 900)
             & exl["LastTraded"].notna()]
    ex = ex.drop_duplicates(["VenueClean", "HorseClean"])
    m = top.merge(ex[["VenueClean", "HorseClean", "Back1", "Lay1",
                      "LastTraded"]],
                  left_on=["CourseClean", "HorseClean"],
                  right_on=["VenueClean", "HorseClean"], how="inner")
    if m.empty:
        return m
    m = m[m["Back1"] > 1].copy()
    m["Edge"] = m["PriceDecimal"] / m["Back1"] - 1
    m = m[m["Edge"] >= min_edge]
    if min_price:
        m = m[m["PriceDecimal"] >= min_price]
    if max_price:
        m = m[m["PriceDecimal"] <= max_price]
    if upcoming_only and day:
        now = dt.datetime.now().strftime("%H:%M:%S")
        m = m[m["RaceTime"].astype(str) > now]
    if exclude_ben is not None and not exclude_ben.empty:
        bk = set(zip(exclude_ben["ck"], exclude_ben["hk"], strict=False))
        m = m[~m.apply(lambda r: (clean_name(r["CourseClean"]),
                                  r["HorseClean"]) in bk, axis=1)]
    m["BenPick"] = m.apply(
        lambda r: (clean_name(r["CourseClean"]), r["HorseClean"]) in _ben_keys,
        axis=1) if _ben_keys else False
    return m.sort_values("Edge", ascending=False)


def log_auto_picks(df, day):
    """Append today's auto-selections to PRODB.dbo.AutoPicks for later review."""
    if df.empty:
        return 0
    conn = pyodbc.connect(CONN, autocommit=True)
    cur = conn.cursor()
    cur.execute("""
    IF OBJECT_ID('dbo.AutoPicks') IS NULL
    CREATE TABLE dbo.AutoPicks (
        ID BIGINT IDENTITY(1,1) PRIMARY KEY,
        LoggedAt DATETIME2(0) NOT NULL,
        RaceDate DATE NOT NULL, RaceTime TIME(0) NULL, CourseClean VARCHAR(64),
        HorseClean VARCHAR(64), HorseName VARCHAR(80), RaceTitle VARCHAR(200),
        BestBook FLOAT, Account VARCHAR(40), BetfairBack FLOAT,
        BetfairLay FLOAT, EdgePct FLOAT, Move VARCHAR(14),
        BenPick BIT, TimeformRating VARCHAR(16)
    );""")
    rows = [(dt.datetime.now().replace(microsecond=0), day,
             str(r["RaceTime"]), r["CourseClean"], r["HorseClean"],
             r["HorseName"], str(r.get("RaceTitle"))[:200],
             float(r["PriceDecimal"]), str(r["BookmakerName"])[:40],
             float(r["Back1"]), float(r["Lay1"]), float(r["Edge"] * 100),
             str(r.get("Fluctuation"))[:14], bool(r.get("BenPick")),
             str(r.get("TimeformRating"))[:16] if "TimeformRating" in r else None)
            for _, r in df.iterrows()]
    cur.fast_executemany = True
    cur.executemany("INSERT INTO dbo.AutoPicks (LoggedAt, RaceDate, RaceTime, "
                    "CourseClean, HorseClean, HorseName, RaceTitle, BestBook, "
                    "Account, BetfairBack, BetfairLay, EdgePct, Move, BenPick, "
                    "TimeformRating) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.close()
    return len(rows)


_ben_keys = set()


def load_system_card():
    """Newest full-system card written by scripts/bens_racecard.py."""
    import glob
    cands = sorted(glob.glob(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reports", "bens_racecard_*.csv")))
    if not cands:
        return None
    path = cands[-1]
    try:
        d = pd.read_csv(path)
    except Exception:
        return None
    for c in ("BEN_flag", "BEN_flag_soft", "RanTop4LTO", "ProvenAtTrip"):
        if c in d:
            d[c] = d[c].astype(str).str.lower().isin(("true", "1"))
    return d, path


def load_selections(day):
    """Today's five-rule selection sheet (scripts/selection_today.py)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "reports", f"selections_{day}.csv")
    if not os.path.isfile(path):
        return None
    try:
        d = pd.read_csv(path)
    except Exception:
        return None
    for c in ("SEL_HARD", "SEL_SOFT", "R1_mark_falling", "R2_below_win",
              "R3_below_max", "R4_trip", "R5_lto_top4"):
        if c in d:
            d[c] = d[c].astype(str).str.lower().isin(("true", "1"))
    return d


def load_bens_card(day):
    """Today's Ben card (reports/bens_today_<date>.csv), de-duplicated per runner."""
    import glob
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "reports", f"bens_today_{day}.csv")
    if not os.path.isfile(path):
        cands = sorted(glob.glob(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "reports", "bens_today_*.csv")))
        if not cands:
            return pd.DataFrame(), None
        path = cands[-1]
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    df["is_pick"] = df["BEN"].astype(str).str.lower().isin(("true", "1"))
    df = df[df["is_pick"]].drop_duplicates(
        subset=["RaceTime", "CourseName", "HorseName"])
    return df, os.path.basename(path)


def load_bens_ledger():
    """Ben's settled forward-test ledger (his price vs the real BSP)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "reports", "bens_forward_ledger.csv")
    if not os.path.isfile(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    for c in ("Odds", "BSP_TRUE", "PL_taken", "PL_bsp", "move_pct", "won",
              "Stake"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Odds"])
    return df.sort_values("Date")


def ben_roi(df):
    """(roi at his recorded odds, roi at BSP, strike rate) as percentages."""
    if df.empty:
        return None, None, None
    stake = float(df["Stake"].fillna(1.0).sum()) or len(df)
    return (df["PL_taken"].sum() / stake * 100,
            df["PL_bsp"].sum() / stake * 100,
            df["won"].fillna(0).mean() * 100)


def fig_ben_cum(led):
    """Cumulative P&L: his recorded odds vs the real Betfair SP."""
    d = (led.groupby("Date")[["PL_taken", "PL_bsp"]].sum().cumsum()
         .reset_index())
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d["Date"], y=d["PL_taken"], name="at his odds",
                             line={"color": ACCENT, "width": 2.5}))
    fig.add_trace(go.Scatter(x=d["Date"], y=d["PL_bsp"], name="at real BSP",
                             line={"color": GOOD, "width": 2.5}))
    fig.add_hline(y=0, line_dash="dot", line_color="#888")
    fig.update_layout(height=380, margin={"l": 4, "r": 4, "t": 30, "b": 4},
                      yaxis_title="cumulative P&L (1 unit per bet)",
                      legend={"orientation": "h", "y": 1.12})
    return fig


def fig_ben_buckets(led, by="odds"):
    """ROI at his odds vs at BSP, split by price band or by price move."""
    d = led.copy()
    if by == "odds":
        bins = [0, 4, 8, 15, 33, 66, 1e9]
        labels = ["<4", "4-8", "8-15", "15-33", "33-66", "66+"]
        d["grp"] = pd.cut(d["Odds"], bins=bins, labels=labels)
    else:
        bins = [-1e9, -20, -5, 5, 20, 1e9]
        labels = ["shortened 20%+", "shortened 5-20%", "flat", "drifted 5-20%",
                  "drifted 20%+"]
        d["grp"] = pd.cut(d["move_pct"], bins=bins, labels=labels)
    g = d.groupby("grp", observed=True).agg(
        Bets=("PL_taken", "size"), Taken=("PL_taken", "mean"),
        BSP=("PL_bsp", "mean")).reset_index()
    g["Taken"] *= 100
    g["BSP"] *= 100
    fig = go.Figure()
    fig.add_trace(go.Bar(x=g["grp"].astype(str), y=g["Taken"], name="his odds",
                         marker_color=ACCENT, text=g["Bets"],
                         textposition="outside"))
    fig.add_trace(go.Bar(x=g["grp"].astype(str), y=g["BSP"], name="real BSP",
                         marker_color=GOOD))
    fig.add_hline(y=0, line_dash="dot", line_color="#888")
    fig.update_layout(height=340, barmode="group",
                      margin={"l": 4, "r": 4, "t": 30, "b": 4},
                      yaxis_title="ROI %", xaxis_title="",
                      legend={"orientation": "h", "y": 1.15})
    return fig

    return fig


def fig_scatter(df, ycol, label, xcol="BSP_TRUE"):
    fig = go.Figure()
    lo = max(1.01, float(min(df[xcol].min(), df[ycol].min())) * 0.9)
    hi = float(max(df[xcol].max(), df[ycol].max())) * 1.1
    fig.add_trace(go.Scatter(x=[lo, hi], y=[lo, hi], mode="lines",
                             line={"color": GOOD, "dash": "dash"}, name="par"))
    fig.add_trace(go.Scatter(
        x=df[xcol], y=df[ycol], mode="markers", text=df["Hover"],
        hovertemplate="%{text}<br>" + label + ": %{y:.2f}<br>Betfair: "
        "%{x:.2f}<extra></extra>",
        marker={"size": 8, "color": df["Ratio"], "colorscale": "RdYlGn",
                    "cmin": 0.7, "cmax": 1.3, "colorbar": {"title": "ratio"}}))
    fig.update_layout(height=440, margin={"l": 4, "r": 4, "t": 30, "b": 4},
                      xaxis={"type": "log", "title": "Betfair SP"},
                      yaxis={"type": "log", "title": label}, showlegend=False)
    return fig



# --------------------------------------------------------------------------- #
#  Page
# --------------------------------------------------------------------------- #
dates = available_dates()
if not dates:
    st.title("Racing odds dashboard")
    st.error("No rows in PRODB.dbo.BookOdds yet. Run:  "
             "python scripts\\book_odds.py snapshot")
    st.stop()

today = dt.date.today().isoformat()
try:
    default_idx = max(i for i, d in enumerate(dates) if d <= today)
except ValueError:
    default_idx = len(dates) - 1

st.sidebar.title("Controls")
day = st.sidebar.selectbox("Race date", dates, index=default_idx)
if st.sidebar.button("Refresh now"):
    st.cache_data.clear()
live_refresh = st.sidebar.checkbox("Auto-refresh every 30s", value=False)
st.sidebar.divider()
min_edge = st.sidebar.slider("Auto picks: minimum edge vs Betfair (%)", 0.0,
                             30.0, 2.0, 0.5) / 100.0
max_price = st.sidebar.number_input("Auto picks: max price (0 = no limit)",
                                    min_value=0.0, value=0.0, step=1.0)
upcoming_only = st.sidebar.checkbox("Auto picks: only races still to run",
                                    value=True)
show_which = st.sidebar.radio("Auto picks: which runners", ["All runners",
                                                            "Ben's picks only",
                                                            "Exclude Ben's"],
                              horizontal=False)
st.sidebar.divider()
st.sidebar.caption("Read-only view of PRODB.dbo.BookOdds (bookmaker prices) "
                   "and PRODB.dbo.BFSP (real Betfair SP).")

if live_refresh:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=30000, key="liverefresh")
    except Exception:
        st.sidebar.warning("streamlit-autorefresh not available.")

raw, bsp = load_day(day)
exl = load_betfair_live(day)
if raw.empty:
    st.title("Racing odds dashboard")
    st.warning(f"No bookmaker prices stored for {day}. "
               "Run:  python scripts\\book_odds.py snapshot " + day)
    st.stop()

live = live_only(raw)
cur = latest_per_race(live)
top = best_rows(cur)
marg = book_margins(cur)
share = best_share(cur)
best_mkt = (top.groupby(["CourseClean", "RaceTime", "HorseClean"])
            ["PriceDecimal"].max())
best_mkt_margin = best_market_margin(cur)
races = cur.groupby(["CourseClean", "RaceTime"]).ngroups
last_snap = pd.to_datetime(raw["SnapshotAt"]).max()
age_min = (dt.datetime.now() - last_snap.to_pydatetime()).total_seconds() / 60

st.title("Racing odds dashboard")
st.caption(f"{day} - {races} races, {len(top)} runners priced by "
           f"{cur['BookmakerName'].nunique()} bookmakers, "
           f"{raw['SnapshotAt'].nunique()} snapshot(s). "
           f"Last price update {age_min:.0f} min ago.")

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Races priced", races)
k2.metric("Runners", len(top))
k3.metric("Snapshots stored", int(raw["SnapshotAt"].nunique()))
if len(marg) and best_mkt_margin:
    k4.metric("Tightest book", str(marg.index[0]),
              f"{(marg.iloc[0] - 1) * 100:+.1f}% margin")
    k5.metric("Best-of-market margin", f"{(best_mkt_margin - 1) * 100:+.1f}%",
              "you still pay this")
else:
    k4.metric("Tightest book", "-")
    k5.metric("Best-of-market margin", "-")

tab_live, tab_auto, tab_move, tab_bsp, tab_sel, tab_health = st.tabs(
    ["Live prices", "Auto picks", "Movement", "Book vs Betfair", "Selections",
     "Data health"])

with tab_live:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("What each book takes")
        st.plotly_chart(
            fig_margin_bar(marg, best_mkt_margin),
            use_container_width=True)
        st.caption("Margin = sum of 1/price across the race, minus 1: the "
                   "book's built-in edge over a fair book. The dashed line is "
                   "what you still pay after shopping all 12 accounts.")
    with c2:
        st.subheader("Who has the best price")
        st.plotly_chart(fig_share_bar(share), use_container_width=True)
        st.caption("'edge' = how much longer that account's price was than the "
                   "next-best account, averaged over the runners it topped.")

    st.divider()
    st.subheader("Where to bet right now (best of your accounts)")
    where = top[["RaceTime", "CourseName", "HorseName", "PriceDecimal",
                 "BookmakerName", "ShopEdge", "Fluctuation", "NBooks"]].copy()
    where.columns = ["Time", "Course", "Horse", "Best price", "Use account",
                     "vs next best", "Move", "Books"]
    where["Time"] = where["Time"].astype(str).str[:5]
    where = where.sort_values(["Time", "Course", "Horse"])
    st.dataframe(where, use_container_width=True, hide_index=True, height=340)

with tab_move:
    st.subheader("How prices moved between snapshots")
    live["Race"] = (live["RaceTime"].astype(str).str[:5] + "  "
                    + live["CourseName"].astype(str) + " - "
                    + live["RaceTitle"].astype(str).str[:44])
    options = list(dict.fromkeys(live["Race"].tolist()))
    pick = st.selectbox("Race", options, key="racepick")
    sel = live[live["Race"] == pick]
    snaps = sorted(pd.to_datetime(sel["SnapshotAt"]).unique())
    if len(snaps) < 2:
        st.info(f"Only {len(snaps)} snapshot for this race so far - run "
                "run_book_odds.bat again later to build the movement history.")
    mv = (sel.groupby(["SnapshotAt", "HorseName"])["PriceDecimal"]
          .max().reset_index())
    fign = px.line(mv, x="SnapshotAt", y="PriceDecimal", color="HorseName",
                   markers=True, log_y=True)
    fign.update_layout(height=460, margin={"l": 4, "r": 4, "t": 30, "b": 4},
                       yaxis_title="best available price",
                       xaxis_title="snapshot time",
                       legend={"font": {"size": 10}})
    st.plotly_chart(fign, use_container_width=True)
    st.caption("Best price available across your accounts, per runner. A "
               "falling line = the horse is being backed (shortening).")

    st.divider()
    st.subheader("How often do the accounts move?")
    st.caption("These are the **12 bookmaker accounts** (PRODB.dbo.BookOdds) - "
               "not the Betfair exchange, which is captured separately in "
               "dbo.BetfairLive and reprices several times faster in much "
               "smaller steps. Every pair of consecutive snapshots is compared "
               "price by price; our polling interval is the limit on what can "
               "be seen, so the true rate is higher than measured.")
    mkeys = ["RaceDate", "CourseClean", "RaceTime", "HorseClean",
             "BookmakerName"]
    lv = live.copy()
    ss = sorted(lv["SnapshotAt"].unique())
    if len(ss) < 2:
        st.info(f"Only {len(ss)} snapshot stored for {day} - a second one is "
                "needed before movement can be measured.")
    else:
        rows = []
        for x, y in itertools.pairwise(ss):
            A = lv[lv["SnapshotAt"] == x].set_index(mkeys)["PriceDecimal"]
            B = lv[lv["SnapshotAt"] == y].set_index(mkeys)["PriceDecimal"]
            cm = A.index.intersection(B.index)
            if not len(cm):
                continue
            A, B = A.loc[cm], B.loc[cm]
            ch = A != B
            rows.append({
                "window": f"{str(x)[11:16]} to {str(y)[11:16]}",
                "minutes": round((pd.Timestamp(y)
                                  - pd.Timestamp(x)).total_seconds() / 60, 1),
                "prices": len(cm),
                "% changed": round(ch.mean() * 100, 1),
                "longer": int(((B > A) & ch).sum()),
                "shorter": int(((B < A) & ch).sum()),
                "median move %": round((((B - A).abs() / A)[ch].median() * 100)
                                       if ch.any() else 0.0, 1)})
        mv = pd.DataFrame(rows)
        st.dataframe(mv, use_container_width=True, hide_index=True)

        A0 = lv[lv["SnapshotAt"] == ss[0]].set_index(mkeys)["PriceDecimal"]
        B0 = lv[lv["SnapshotAt"] == ss[-1]].set_index(mkeys)["PriceDecimal"]
        cm = A0.index.intersection(B0.index)
        if len(cm):
            A0, B0 = A0.loc[cm], B0.loc[cm]
            chg = A0 != B0
            hrs = (pd.Timestamp(ss[-1]) - pd.Timestamp(ss[0])).total_seconds() / 3600
            k1, k2, k3 = st.columns(3)
            k1.metric("Prices that moved", f"{chg.mean() * 100:.0f}%",
                      f"{int(chg.sum()):,} of {len(cm):,}")
            k2.metric("Direction",
                      f"{(B0 - A0)[chg].gt(0).mean() * 100:.0f}% longer",
                      f"{hrs:.1f}h window")
            k3.metric("Median move size",
                      f"{((B0 - A0).abs() / A0)[chg].median() * 100:.1f}%")

            per = []
            for bk, g in lv.groupby("BookmakerName"):
                a = g[g["SnapshotAt"] == ss[0]].set_index(mkeys)["PriceDecimal"]
                b = g[g["SnapshotAt"] == ss[-1]].set_index(mkeys)["PriceDecimal"]
                cc = a.index.intersection(b.index)
                if not len(cc):
                    continue
                a, b = a.loc[cc], b.loc[cc]
                c2 = a != b
                per.append({"account": bk, "prices": len(cc),
                            "changed %": round(c2.mean() * 100, 1),
                            "median move %": round((((b - a).abs() / a)[c2]
                                                    .median() * 100)
                                                   if c2.any() else 0.0, 1)})
            pp = pd.DataFrame(per).sort_values("changed %", ascending=False)
            st.plotly_chart(
                px.bar(pp, x="changed %", y="account", orientation="h",
                       text="median move %", color="changed %",
                       color_continuous_scale="Teal"),
                use_container_width=True)
            st.caption("Which account reprices most. Everything at ~11% median "
                       "move is one rung of the odds ladder, so these are real "
                       "repricings rather than rounding.")

    st.divider()
    st.subheader("Price spread for this race (latest snapshot)")

    last_here = sel["SnapshotAt"].max()
    mat = (sel[sel["SnapshotAt"] == last_here]
           .pivot_table(index="HorseName", columns="BookmakerName",
                        values="PriceDecimal", aggfunc="max"))
    if not mat.empty:
        mat = mat.reindex(columns=mat.max().sort_values(ascending=False).index)
        fig2 = px.imshow(mat, text_auto=".2f", aspect="auto",
                         color_continuous_scale="RdYlGn")
        fig2.update_layout(height=120 + 26 * len(mat),
                           margin={"l": 4, "r": 4, "t": 30, "b": 4},
                           xaxis_title="", yaxis_title="",
                           coloraxis_colorbar={"title": "price"})
        fig2.update_xaxes(tickangle=-45, tickfont={"size": 10})
        st.plotly_chart(fig2, use_container_width=True)
        st.caption("Columns are ordered left-to-right by how often each "
                   "account tops the market. Blank = that account was not "
                   "quoting that runner.")
    st.caption("Tip: the further the rows drift from their opening price, the "
               "more information has arrived - compare this with the Betfair "
               "tab once the SP is in.")

with tab_bsp:
    if bsp.empty:
        pub = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
        st.info(f"Betfair SP for {day} is not in yet.\n\n"
                f"Betfair ships a day's prices in the **next** day's file, so "
                f"{day} arrives in the file dated **{pub}** (usually by "
                "mid-morning). Waiting for it in the background is easiest:\n\n"
                f"        finish_bsp.bat {day}\n\n"
                f"or:  python scripts\\wait_for_bsp.py {day}\n\n"
                "It waits for the file, imports the SP, rebuilds the price log "
                "and prints this report. Then press Refresh now.")
    else:
        b = (bsp.dropna(subset=["BSP_TRUE"])
             .drop_duplicates(["CourseClean", "HorseClean"]))
        m = top.merge(b[["CourseClean", "HorseClean", "BSP_TRUE", "WinLose"]],
                      on=["CourseClean", "HorseClean"], how="inner")
        if m.empty:
            st.info("BSP rows exist but none match this date's runners.")
        else:
            m = m.copy()
            m["Ratio"] = m["PriceDecimal"] / m["BSP_TRUE"]
            m["Hover"] = (m["HorseName"] + "  (" + m["CourseName"] + " "
                          + m["RaceTime"].astype(str).str[:5]
                          + ")  best book: " + m["BookmakerName"])
            st.subheader("Best available price vs the real exchange price")
            q1, q2, q3, q4 = st.columns(4)
            q1.metric("Runners matched", len(m))
            q2.metric("Median price / BSP", f"{m['Ratio'].median():.3f}",
                      "above 1.00 beats the exchange")
            q3.metric("Beat BSP", f"{(m['Ratio'] > 1).mean() * 100:.1f}%")
            q4.metric("Beat BSP by 5%+",
                      f"{(m['Ratio'] >= 1.05).mean() * 100:.1f}%")

            c1, c2 = st.columns([1.15, 1])
            with c1:
                st.plotly_chart(fig_scatter(m, "PriceDecimal",
                                            "best available price"),
                                use_container_width=True)
                st.caption("Above the dashed line = the price you can take is "
                           "longer than Betfair's SP.")
            with c2:
                st.plotly_chart(fig_ratio_hist(m["Ratio"], "best price"),
                                use_container_width=True)

            st.divider()
            st.subheader("Does a bigger edge actually pay?")
            st.caption("Backing EVERY matched runner at the best available "
                       "price, grouped by how far that price beat the SP. "
                       "Betfair SP is a fair price, so low buckets must lose; "
                       "the question is whether the high buckets turn positive.")
            if m["WinLose"].notna().any():
                edges = [(-9, 0.9, "under 0.90"), (0.9, 1.0, "0.90-1.00"),
                         (1.0, 1.05, "1.00-1.05"), (1.05, 1.10, "1.05-1.10"),
                         (1.10, 1.20, "1.10-1.20"), (1.20, 99, "1.20+")]
                rows = []
                for lo, hi, label in edges:
                    sub = m[(m["Ratio"] >= lo) & (m["Ratio"] < hi)]
                    if sub.empty:
                        continue
                    ret = float((sub["PriceDecimal"] * sub["WinLose"]).sum())
                    rows.append({"Edge vs BSP": label, "Runners": len(sub),
                                 "Wins": int(sub["WinLose"].sum()),
                                 "ROI %": (ret / len(sub) - 1) * 100})
                if rows:
                    t = pd.DataFrame(rows)
                    fig3 = px.bar(t, x="Edge vs BSP", y="ROI %", text="Runners",
                                  color="ROI %",
                                  color_continuous_scale="RdYlGn",
                                  range_color=[-40, 40])
                    fig3.add_hline(y=0, line_dash="dash", line_color=GOOD)
                    fig3.update_layout(height=340, showlegend=False,
                                       margin={"l": 4, "r": 4, "t": 20, "b": 4})
                    st.plotly_chart(fig3, use_container_width=True)
                    tot = float((m["PriceDecimal"] * m["WinLose"]).sum())
                    st.metric("Backing every runner at the best price",
                              f"{(tot / len(m) - 1) * 100:+.1f}% ROI",
                              f"{len(m)} bets, no selection")
            else:
                st.info("Results (WinLose) are not present for this date yet.")

            st.divider()
            st.subheader("Per-account view")
            allm = cur.merge(b[["CourseClean", "HorseClean", "BSP_TRUE",
                                "WinLose"]], on=["CourseClean", "HorseClean"],
                             how="inner")
            if allm.empty:
                st.info("No per-account rows matched the SP.")
            else:
                allm = allm.copy()
                allm["Ratio"] = allm["PriceDecimal"] / allm["BSP_TRUE"]
                tbl = (allm.groupby("BookmakerName")
                       .agg(Runners=("Ratio", "size"),
                            MedianRatio=("Ratio", "median"),
                            BeatBSP=("Ratio", lambda s: (s > 1).mean() * 100))
                       .sort_values("MedianRatio"))
                tbl["ROI %"] = [
                    (float((allm[allm["BookmakerName"] == nm]["PriceDecimal"]
                            * allm[allm["BookmakerName"] == nm]["WinLose"]).sum())
                     / max(len(allm[allm["BookmakerName"] == nm]), 1) - 1) * 100
                    for nm in tbl.index]
                st.dataframe(tbl, use_container_width=True, height=430)
                st.caption("ROI % = backing every runner in your price log at "
                           "that one account's price: the 'no skill' baseline "
                           "for that account.")

with tab_sel:
    st.subheader("The system's rules, and the state of the data behind them")
    st.caption("The full rule set is implemented in scripts/bens_racecard.py. "
               "Five conditions, all required (plus a 'soft' three-condition "
               "variant):")
    st.markdown(
        "1. **Mark falling** - `OR_now < LTO_OR` (official rating, = the weight "
        "in a handicap)\n"
        "2. **Below last winning mark** - `OR_now < OR at its last win`\n"
        "3. **Below career best** - `OR_now < career-high OR`\n"
        "4. **Proven at the trip** - a prior 1st-3rd over the same exact "
        "distance (`RH_Exact_Race_Distance`)\n"
        "5. **Ran top 4 last time out**\n"
        "*soft = 1 + 3 + 4 only*")
    syscard = load_system_card()
    if syscard is None:
        st.info("No full-system card yet. Run:  "
                "python scripts\\bens_racecard.py YYYY-MM-DD")
    else:
        d, path = syscard
        hard = d[d["BEN_flag"]]
        soft = d[d["BEN_flag_soft"]]
        s1, s2, s3 = st.columns(3)
        s1.metric("Handicap runners", f"{len(d):,}")
        s2.metric("HARD picks (all 5)", len(hard))
        s3.metric("SOFT picks (3 rules)", len(soft))
        st.caption(f"from {os.path.basename(path)}")
        if len(hard):
            cols = [c for c in ["RH_DateTime", "CourseName", "HorseName",
                                "HIR_Jockey_name", "HIR_OfficialRating",
                                "MarkChange", "OR_last_win", "OR_career_max",
                                "HIR_Pounds", "HIR_DSLR", "RanTop4LTO",
                                "ProvenAtTrip"] if c in hard]
            st.dataframe(hard[cols], use_container_width=True, hide_index=True)
            st.caption("Every HARD pick satisfies all five rules; the columns "
                       "are the evidence for each one.")

    st.warning(
        "**Today's card cannot be trusted yet.** Three data problems, measured "
        "today:\n\n"
        "1. The scraped racecard's `OfficialRating` is missing for 297 of 420 "
        "runners, and where it exists it disagrees with history by >15 lb for "
        "92% of matched horses (e.g. Pike Road says 51, history says 114). It "
        "is not a handicap mark.\n"
        "2. PRODB - the only source with ratings *and* exact trips - stops on "
        "**22 May 2026**, so 'last time out' is ~4 months stale.\n"
        "3. `Scraped_Results` runs to 19 Aug 2026 but its OfficialRating / "
        "Weight / BSP columns are **NULL for all 675,833 rows** (added by the "
        "updater, never backfilled).\n\n"
        "So today's flags are computed from unreliable marks. Fix the OR feed "
        "and the full system becomes evaluable - until then treat today's "
        "selection count as meaningless.")

    st.divider()
    st.subheader("Today's selections - all five rules, priced live")
    sel = load_selections(day)
    if sel is None or sel.empty:
        st.info(f"No selection sheet for {day}. Run:  "
                f"python scripts\\selection_today.py {day}")
    else:
        hard = sel[sel["SEL_HARD"]].copy()
        soft = sel[sel["SEL_SOFT"]].copy()
        n_rated = int(sel["OR_known"].sum()) if "OR_known" in sel else 0
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Runners", len(sel), f"{n_rated} with a rating")
        p2.metric("HARD (all 5 rules)", len(hard))
        p3.metric("SOFT (3 rules)", len(soft))
        p4.metric("Races", sel.groupby(["Course",
                                        "RaceTime"]).ngroups)

        if hard.empty:
            st.warning("No runner satisfies all five rules today.")
        else:
            j = hard.copy()
            j["k"] = j["Horse"].map(clean_name)
            j["ck"] = j["Course"].map(clean_name)
            j = j.merge(top[["CourseClean", "HorseClean", "PriceDecimal",
                             "BookmakerName"]].rename(
                                 columns={"PriceDecimal": "BestBook",
                                          "BookmakerName": "UseAccount"}),
                        left_on=["ck", "k"],
                        right_on=["CourseClean", "HorseClean"], how="left")
            if not exl.empty:
                j = j.merge(exl[["VenueClean", "HorseClean", "Back1", "Lay1"]],
                            left_on=["ck", "k"],
                            right_on=["VenueClean", "HorseClean"], how="left")
            else:
                j["Back1"] = None
            j["BookVsBF"] = j["BestBook"] / j["Back1"]
            renames = {"RaceTime": "Time", "Course": "Course", "Horse": "Horse",
                       "OR_now": "OR now", "LTO_OR": "LTO OR",
                       "WIN_OR": "Win mark", "CAREER_MAX": "Max",
                       "TripPlacings": "Trip pl", "LTO_POS": "LTO",
                       "WgtCard": "Wgt", "BestBook": "Best",
                       "UseAccount": "Use", "Back1": "Betfair",
                       "BookVsBF": "book/BF", "Jockey": "Jockey"}
            show = j[[c for c in renames if c in j]].rename(columns=renames)
            st.dataframe(show.sort_values(["Time", "Course"]),
                         use_container_width=True, hide_index=True, height=460)
            beats = int((j["BookVsBF"] >= 1).sum())
            priced = int(j["BookVsBF"].notna().sum())
            st.caption(f"{len(hard)} selections; {beats} of {priced} priced ones "
                       "are at or above the Betfair back price. Conditions: "
                       "mark falling, below last winning mark, below career "
                       "best, prior 1st-3rd at this exact trip, top-4 last time "
                       "out. Ratings come from the racecard page; form history "
                       "from PRODB, which stops on 2026-05-22.")

    st.divider()
    st.subheader("His recorded bets, priced two ways")
    st.caption("Every settled selection in the forward ledger, priced two ways: "
               "at the odds Ben recorded, and at the real Betfair SP. If the "
               "edge survives the exchange price, it is in the selection; if "
               "it vanishes, it was only ever the price.")
    led = load_bens_ledger()
    if led.empty:
        st.info("No ledger yet (reports/bens_forward_ledger.csv). It is built "
                "by scripts/bens_forward.py.")
    else:
        roi_t, roi_b, strike = ben_roi(led)
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Settled bets", f"{len(led):,}",
                  f"since {led['Date'].min():%d %b %Y}")
        b2.metric("Strike rate", f"{strike:.1f}%")
        b3.metric("ROI at his odds", f"{roi_t:+.1f}%",
                  "what the service advertises")
        b4.metric("ROI at real BSP", f"{roi_b:+.1f}%",
                  "what you would actually get")
        if roi_t > 0 and roi_b <= 0:
            st.warning("The edge is entirely in the recorded prices: the picks "
                       "do not beat Betfair. Treat the advertised ROI as "
                       "unachievable.")
        elif roi_b > 0:
            st.success("The picks do beat Betfair - this is the one result in "
                       "the project that survives real prices.")

        st.plotly_chart(fig_ben_cum(led), use_container_width=True)

        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(fig_ben_buckets(led, "odds"),
                            use_container_width=True)
            st.caption("Number above each bar = bets in that price band. His "
                       "odds (pink) vs real BSP (green).")
        with c2:
            st.plotly_chart(fig_ben_buckets(led, "move"),
                            use_container_width=True)
            st.caption("Same split by how the price moved before the off - "
                       "shortening means money came.")

        st.plotly_chart(
            px.pie(led, names="BetType", hole=0.45, title="Bet type mix"),
            use_container_width=True, )

    st.divider()
    st.subheader("Legacy 2-rule card (superseded by the five rules above)")
    st.caption("scripts/bens_today.py - the earlier two-condition version "
               "(mark falling + below last winning mark), computed from the "
               "scraped card. Kept only for comparison.")
    card, src = load_bens_card(day)
    if card.empty:
        st.info(f"No Ben card for {day} (reports/bens_today_{day}.csv). The "
                "daily pipeline builds it.")
    else:
        st.caption(f"{len(card)} picks from {src}, priced against your "
                   "accounts and the Betfair exchange right now.")
        j = card.copy()
        j["ck"] = (j["CourseName"].str.lower()
                   .str.replace(r"[^a-z0-9]", "", regex=True))
        j["hk"] = (j["HorseName"].str.lower()
                   .str.replace(r"[^a-z0-9]", "", regex=True))
        bk = top[["CourseClean", "HorseClean", "PriceDecimal", "BookmakerName",
                  "Fluctuation"]].rename(columns={
                      "PriceDecimal": "BestBook", "BookmakerName": "UseAccount",
                      "Fluctuation": "Move"})
        j = j.merge(bk, left_on=["ck", "hk"],
                    right_on=["CourseClean", "HorseClean"], how="left")
        if not exl.empty:
            j = j.merge(exl[["VenueClean", "HorseClean", "Back1", "Lay1"]]
                        .rename(columns={"Back1": "BFback", "Lay1": "BFlay"}),
                        left_on=["ck", "hk"],
                        right_on=["VenueClean", "HorseClean"], how="left")
        else:
            # No Betfair file for this day - keep the columns so the merge
            # below and the 'book/BF' maths still work (they show blank).
            j["BFback"] = float("nan")
            j["BFlay"] = float("nan")
        j["BookVsBF"] = j["BestBook"] / j["BFback"]
        beats = int((j["BookVsBF"] >= 1).sum())
        st.caption(f"{beats} of {int(j['BookVsBF'].notna().sum())} priced picks "
                   "are currently at or above the Betfair back price.")
        cols = ["RaceTime", "CourseName", "HorseName", "OR_now", "MarkChange",
                "LTO_POS", "BestBook", "UseAccount", "BFback", "BookVsBF",
                "Move"]
        show = j[[c for c in cols if c in j]].rename(columns={
            "RaceTime": "Time", "CourseName": "Course", "HorseName": "Horse",
            "OR_now": "OR now", "MarkChange": "Mark chg", "LTO_POS": "LTO pos",
            "BestBook": "Best book", "UseAccount": "Use account",
            "BFback": "Betfair", "BookVsBF": "book/BF", "Move": "Move"})
        st.dataframe(show.sort_values(["Time", "Course", "Horse"]),
                     use_container_width=True, hide_index=True, height=420)
        st.caption("'book/BF' above 1.00 means the price you can take is longer "
                   "than Betfair's - that is the only kind of runner worth "
                   "backing. 'Mark chg' is the OR move Ben's rule keys on.")

with tab_auto:
    st.subheader("Auto selection - runners where your price beats Betfair")
    st.caption("Scans every runner on the card, takes the best price across "
               "your accounts, and keeps the ones where that price is longer "
               "than Betfair's back price. Nothing is typed in: it re-finds "
               "them as prices move (tick Auto-refresh).")
    card_b, _src_b = load_bens_card(day)
    _ben_keys = ({(clean_name(r["CourseName"]), clean_name(r["HorseName"]))
                  for _, r in card_b.iterrows()} if not card_b.empty else set())
    ap = auto_picks(top, exl, min_edge=min_edge,
                    max_price=(max_price or None), upcoming_only=upcoming_only,
                    day=day)
    if ap.empty:
        st.info("No runner currently beats Betfair by that much. Races with "
                "no traded money yet cannot qualify - check back closer to the "
                "off, or lower the edge in the sidebar.")
    else:
        if show_which == "Ben's picks only":
            ap = ap[ap["BenPick"]]
        elif show_which == "Exclude Ben's":
            ap = ap[~ap["BenPick"]]
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Runners found", len(ap))
        a2.metric("Races", ap.groupby(["CourseClean",
                                      "RaceTime"]).ngroups)
        a3.metric("Median edge", f"{ap['Edge'].median() * 100:+.1f}%")
        a4.metric("Biggest edge", f"{ap['Edge'].max() * 100:+.0f}%",
                  f"{ap.iloc[0]['HorseName'][:22]}")

        tbl = ap[["RaceTime", "CourseName", "HorseName", "PriceDecimal",
                  "BookmakerName", "Back1", "Lay1", "Edge", "Fluctuation",
                  "NBooks", "BenPick"]].copy()
        tbl["Edge"] = (tbl["Edge"] * 100).round(1)
        tbl = tbl.rename(columns={
            "RaceTime": "Time", "CourseName": "Course", "HorseName": "Horse",
            "PriceDecimal": "Best price", "BookmakerName": "Use account",
            "Back1": "Betfair", "Lay1": "BF lay", "Edge": "Edge %",
            "Fluctuation": "Move", "NBooks": "Books", "BenPick": "Ben pick"})
        tbl["Time"] = tbl["Time"].astype(str).str[:5]
        st.dataframe(tbl, use_container_width=True, hide_index=True,
                     height=460)
        st.caption("Sorted by edge. 'Use account' is where the best price is; "
                   "check the ladder width (Books) - a big edge in a thin "
                   "market is usually the longshot bias, not value.")

        fig = px.bar(ap.head(20).iloc[::-1], x="Edge", y="HorseName",
                     orientation="h",
                     color="Edge", color_continuous_scale="RdYlGn",
                     hover_data=["CourseName", "Back1", "PriceDecimal",
                                 "BookmakerName"])
        fig.update_layout(height=max(300, 22 * min(len(ap), 20)),
                          margin={"l": 4, "r": 4, "t": 30, "b": 4},
                          xaxis_tickformat=".0%", yaxis_title="",
                          xaxis_title="price you can get vs Betfair back",
                          coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

        band = ap.copy()
        band["Price band"] = pd.cut(band["PriceDecimal"],
                                    [0, 8, 20, 50, 1e9],
                                    labels=["<8", "8-20", "20-50", "50+"])
        bt = (band.groupby("Price band", observed=True)
              .agg(Found=("Edge", "size"),
                   MedianEdge=("Edge", "median"),
                   Biggest=("Edge", "max")))
        bt["MedianEdge"] = (bt["MedianEdge"] * 100).round(1)
        bt["Biggest"] = (bt["Biggest"] * 100).round(0)
        st.caption("Where the edges sit - if they are all in the 20+ bands "
                   "that is the longshot bias talking, not value:")
        st.dataframe(bt, use_container_width=True)

        if st.button("Log these to PRODB.dbo.AutoPicks (for the settled record)"):
            n = log_auto_picks(ap, day)
            st.success(f"Logged {n} picks at "
                       f"{dt.datetime.now():%H:%M:%S}. Once race results are in, "
                       "the ratio column tells you whether 'beats Betfair' "
                       "actually pays.")

with tab_health:
    st.subheader("Snapshots stored for " + day)
    snaps = (raw.groupby("SnapshotAt")
             .agg(Prices=("ID", "size"),
                  Races=("RaceTime", lambda s: s.nunique()),
                  Books=("BookmakerName", "nunique"))
             .reset_index().sort_values("SnapshotAt"))
    snaps["Minutes ago"] = ((pd.Timestamp.now()
                             - pd.to_datetime(snaps["SnapshotAt"]))
                            .dt.total_seconds() / 60).round(0)
    st.dataframe(snaps, use_container_width=True, hide_index=True)

    st.subheader("Coverage per account")
    cov = (cur.groupby("BookmakerName")
           .agg(Prices=("PriceDecimal", "size"),
                Runners=("HorseClean", "nunique"))
           .sort_values("Runners", ascending=False))
    cov["% of runners quoted"] = cov["Runners"] / max(len(top), 1) * 100
    st.dataframe(cov, use_container_width=True, height=430)
    st.caption("An account quoting only some runners is using 'no runner "
               "exclusion' rules - normal, but it means the best price is not "
               "always available there.")

    st.subheader("Checks")
    n_snaps = int(raw["SnapshotAt"].nunique())
    msgs = []
    if n_snaps < 2:
        msgs.append(("warn", ("Only one snapshot so far. Run "
                     "run_book_odds.bat again during the day for the Movement "
                     "tab to have anything to plot.")))
    if age_min > 60:
        msgs.append(("warn", (f"Prices are {age_min:.0f} minutes old. Re-run "
                     "run_book_odds.bat to refresh (prices only start being "
                     "published on the morning of the race).")))
    if bsp.empty:
        pub = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
        msgs.append(("info",
                     (f"Betfair SP for {day} lands in the file dated {pub} "
                     f"(Betfair publishes a day's prices the next day). Run "
                     f"finish_bsp.bat {day} to wait for it and import it.")))
    if len(top) and len(share) and share["Share"].iloc[0] > 60:
        msgs.append(("warn", (f"{share.index[0]} holds the best price on "
                     f"{share['Share'].iloc[0]:.0f}% of runners - check the "
                     "other accounts are actually quoting.")))
    for kind, text in msgs:
        (st.warning if kind == "warn" else st.info)(text)
    if not msgs:
        st.success("Everything looks healthy: multiple snapshots, fresh "
                   "prices, SP backfilled, no account dominating.")

    st.divider()
    st.subheader("The daily routine")
    st.code("run_book_odds.bat          # snapshot + price log + report\n"
            "run_book_odds.bat tomorrow # next day's card (often unpriced yet)\n"
            "finish_bsp.bat 2026-09-15  # NEXT morning: wait for the SP, import\n"
            "python scripts\\book_odds.py value 2026-09-15   # overlay finder\n"
            "python scripts\\book_odds.py report 2026-09-15 --book BEST\n"
            "python scripts\\wait_for_bsp.py 2026-09-15 --check  # is it out yet?",
            language="bat")

    st.caption("Data sources: api.racingtv.com (12 bookmakers, best-odds feed "
               "courtesy of Oddschecker) -> PRODB.dbo.BookOdds; Betfair SP -> "
               "PRODB.dbo.BFSP. Betting at the best available price still "
               "leaves the margin shown on the Live prices tab, so treat every "
               "edge as unproven until the 'Does a bigger edge actually pay?' "
               "chart says otherwise.")



