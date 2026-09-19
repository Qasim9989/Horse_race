"""
5-YEAR MASTER LAY AUDIT — CORRECTED / AUDITABLE REBUILD
=======================================================
Rebuilt from audit_5year_historical.py (which is left untouched for diffing).

FOUR DEFECTS FIXED, EACH PRINTED WITH ITS OWN IMPACT NUMBER
-----------------------------------------------------------
FIX 1 - LOOK-AHEAD FILTER REMOVED (was: AND HIR.HIR_PositionNo BETWEEN 1 AND 50)
        That filter is a POST-RACE outcome filter: it silently deleted every
        runner that did not finish.  Non-finishers are stored as
        HIR_PositionNo 245-255 (254 = most common, ~20.7k rows).  For a LAY
        strategy those are WINS - the horse was defeated - so the original
        script deleted ~27,600 winning outcomes and ranked a field of
        finishers only, while its own header claimed
        "ZERO LOOKAHEAD FULL FIELD EVALUATION".
        The scoring/settlement logic already handles them: PositionNo > 1.
        Impact printed as FIELD COMPLETENESS.

FIX 2 - STRIDE DECAY UNIT / THRESHOLD (was: (LTO_ASL - LTO_SL_Finish) >= 0.20)
        ASL and SL_Finish are in FEET (~23.4), so 0.20 fired a -2 on 77.8% of
        scorable runners - near-constant, and it cannot fire at all on the
        54.3% of runners with no sectional data, handing them a structural +2.
        Now: both values must be present AND the drop must be >= 0.66 ft, and
        the un-scorable population is measured and reported honestly.
        Impact printed as STRIDE COVERAGE.

FIX 3 - SETTLEMENT HONESTY (was: only fixed-liability, BSP >= 1.01)
        The original laid to a fixed GBP 15 LIABILITY, so stake = 15/(BSP-1):
        at BSP 1.01 that is a GBP 1,500 stake, and the headline ROI was a
        return-on-liability leaning on those.  Now BOTH conventions are
        reported side by side (fixed liability AND standard fixed stake),
        plus a price-band breakdown and a liquidity-realistic band.
        Impact printed as SETTLEMENT MODEL COMPARISON.

FIX 4 - CONTROLS ADDED (there were none)
        (a) SELECTION ABLATION: does the rank matter, does the score matter,
            or does neither?  Tier definitions with each component removed.
        (b) RANDOM SAME-RACE CONTROL: for every race, lay a uniformly random
            price-eligible runner instead, repeated over many seeds.  If the
            system cannot beat a random short-priced runner in the same races,
            the selection carries no information.
        This is the test that must be passed before any ROI here is believed.

FIX 5 - THE ACTUAL ROOT CAUSE, MEASURED (not a code bug - a data-label bug)
        HIR_BSP is NOT Betfair Starting Price.  Measured book overround:
            PRODB HIR_BSP, 42,313 handicap races      = 1.213   (21.3% margin)
            TRUE Betfair BSP, 20 races 19/08/2026     = 1.0025  (0.25% margin)
        So HIR_BSP is a bookmaker / tissue style SP.  Settling a LAY against a
        price whose implied probabilities sum to 121.3% hands the layer a
        (1 - 1/1.213) = 17.6% margin BEFORE the 2% commission is even applied.
        That, and nothing else, is where the reported profit comes from.
        At the true Betfair BSP the same lays return roughly -1.0% to -1.4% of
        stake, i.e. you lose the commission exactly as theory says.
        Real BSP is free and verified working at
        https://promo.betfair.com/betfairsp/prices/dwbfpricesukwinDDMMYYYY.csv
        and that is the file bulk_bsp_backfill.py already targets.
        The same file also carries ipmin/ipmax, morningwap, ppwap, iptradedvol
        (liquidity) and win_lose (an independent result check).

Sentinel note: HIR_BSP uses -1 for missing (926,740 rows).  -1 <= 6 is TRUE,
so every query MUST carry a lower bound.  This script uses >= 1.01.
Note also that some older scripts use >= 1.50, which silently discards every
genuine 1.01-1.49 favourite; >= 1.01 is the correct guard.
"""

import sys
import os
import datetime
import warnings

import pyodbc
import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')

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

BOGUS_NAMES = (
    "'short head', 'head', 'neck', 'nose', 'dead heat', 'distance', 'length', "
    "'half length', 'shd', 'hd', 'nk', 'nse', 'dh', 'dist', '1l', '2l', '3l', 'dht'"
)

# Thresholds held as named constants so they are visible and adjustable.
STRIDE_DECAY_FT = 0.66      # FIX 2: 0.20 was 6cm in a feet-scaled column
BSP_FLOOR = 1.01            # sentinel guard: excludes HIR_BSP = -1
BSP_CEILING = 6.00          # Master Lay price band
LIQUIDITY_BSP_FLOOR = 2.00  # FIX 3: band where GBP 15 liability needs a sane stake
STAKE = 15.0
COMMISSION = 0.98
CONTROL_SEEDS = 200
# Which price column to settle on:
#   HIR_BSP      - PRODB's price, a bookmaker SP carrying a 21.3% book margin
#   HIR_BSP_TRUE - real Betfair Starting Price, populated by
#                  scripts/betfair_bsp_backfill.py (overround ~1.00)
# Run with --truebsp to settle on the real exchange price.
PRICE_COL = "HIR_BSP_TRUE" if "--truebsp" in sys.argv else "HIR_BSP"
PRICE_LABEL = ("BETFAIR BSP_TRUE (real)" if PRICE_COL == "HIR_BSP_TRUE"
               else "PRODB HIR_BSP (bookmaker SP)")
# Overround is MEASURED from the data in calibration(); this is only the fallback.
OVERROUND_FALLBACK = 1.212


def load_full_field(conn):
    """FIX 1: the BETWEEN 1 AND 50 look-ahead filter is gone."""
    sql = f"""
    SELECT
      RH.RH_RNo,
      RH.RH_DateTime,
      YEAR(RH.RH_DateTime) AS RaceYear,
      FORMAT(RH.RH_DateTime, 'yyyy-MM') AS RaceMonth,
      C.C_Name AS CourseName,
      RH.RH_Name AS RaceTitle,
      RH.RH_NoOfRunners,
      H.H_No AS HorseID,
      H.H_Name_No_Anything AS HorseName,
      HIR.HIR_PositionNo,
      HIR.{PRICE_COL} AS HIR_BSP,
      HIR.HIR_DSLR,
      HIR.HIR_JockeysClaim,

      -- Prior Race Metrics Strictly Before Jump (R2.RH_DateTime < RH.RH_DateTime)
      Prev.LTO_PositionNo,
      Prev.LTO_POSAFTUPG,
      Prev.LTO_ASL,
      Prev.LTO_SL_Finish,
      Prev.LTO_Comments,
      Prev.LTO_PaceAbbrev

    FROM dbo.NEW_RH RH
    JOIN dbo.NEW_HIR HIR ON HIR.HIR_RNo = RH.RH_RNo
    JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
    LEFT JOIN dbo.NEW_C C ON C.C_ID = RH.RH_CNo
    OUTER APPLY (
      SELECT TOP 1
        SD2.ASL AS LTO_ASL,
        SD2.SL_Finish AS LTO_SL_Finish,
        SD2.POSAFTUPG AS LTO_POSAFTUPG,
        H2.HIR_CommentsInRunning AS LTO_Comments,
        H2.HIR_PaceAbbrev AS LTO_PaceAbbrev,
        H2.HIR_PositionNo AS LTO_PositionNo
      FROM dbo.NEW_HIR H2
      JOIN dbo.NEW_RH R2 ON R2.RH_RNo = H2.HIR_RNo
      LEFT JOIN dbo.SData SD2 ON SD2.SD_RNo = H2.HIR_RNo AND SD2.SD_HNo = H2.HIR_HNo
      WHERE H2.HIR_HNo = HIR.HIR_HNo
        AND R2.RH_DateTime < RH.RH_DateTime
      ORDER BY R2.RH_DateTime DESC
    ) Prev
    WHERE RH.RH_RaceTypeID IN (1, 2, 3, 4)
      AND RH.RH_Name LIKE '%Handicap%'
      AND RH.RH_Results = 1
      AND RH.RH_NoOfRunners >= 5
      AND HIR.HIR_PositionNo IS NOT NULL
      AND HIR.HIR_PositionNo > 0
      AND HIR.{PRICE_COL} >= {BSP_FLOOR}
      AND HIR.{PRICE_COL} IS NOT NULL
      AND LOWER(H.H_Name_No_Anything) NOT IN ({BOGUS_NAMES})
      AND LEN(H.H_Name_No_Anything) > 2
      AND RH.RH_DateTime >= '2021-01-01'
    ORDER BY RH.RH_DateTime ASC;
    """
    print("  [1/6] Connecting to PRODB ...")
    t0 = datetime.datetime.now()
    df = pd.read_sql(sql, conn)
    print(f"        Loaded {len(df):,} runner-rows (finishers AND non-finishers) "
          f"in {(datetime.datetime.now() - t0).total_seconds():.1f}s.")
    return df


def measure_overround(df):
    """Measured book overround = mean over races of sum(1/price).

    1.0025 for true Betfair BSP; 1.213 for PRODB HIR_BSP.  That difference is
    the whole story (see FIX 5 in the module docstring).
    """
    return (1.0 / df['HIR_BSP']).groupby(df['RH_RNo']).sum().mean()


def add_fair_settlement(d, overround):
    """FIX 5 empirical test: re-settle the SAME lays at a DE-MARGINED price.

    FairPrice = raw_price * overround (proportional de-margin), which is what
    an exchange price with no bookmaker margin would imply.  Settling here is
    the only version of the backtest that answers "could this be traded?".
    """
    d['FairPrice'] = d['HIR_BSP'] * overround
    d['PL_Fair'] = np.where(
        d['IsLayWin'] == 1,
        STAKE * COMMISSION,
        -STAKE * (d['FairPrice'] - 1.0)
    )
    return d


def score(df, stride_threshold_ft=STRIDE_DECAY_FT, use_score=True, use_rank=True):
    """Pre-race zero-lookahead scoring.  FIX 2 applied to stride decay.

    use_score / use_rank exist only so the ablation control can switch each
    component off independently.
    """
    d = df.copy()

    is_leader = d['LTO_PaceAbbrev'].fillna('').str.upper().isin(
        ['L', 'P', 'F', 'LEAD', 'PROMINENT'])

    # FIX 2: require BOTH sectional values, and measure the drop in feet.
    d['HasStride'] = (d['LTO_ASL'].fillna(0) > 0) & (d['LTO_SL_Finish'].fillna(0) > 0)
    d['StrideDecayFt'] = (d['LTO_ASL'].fillna(0) - d['LTO_SL_Finish'].fillna(0))
    stride_decay = d['HasStride'] & (d['StrideDecayFt'] >= stride_threshold_ft)

    posaftupg_good = d['LTO_POSAFTUPG'] == 1
    posaftupg_ok = d['LTO_POSAFTUPG'] == 2
    posaftupg_bad = d['LTO_POSAFTUPG'].fillna(0) > 1
    quick_or_claim = (d['HIR_DSLR'].fillna(99) <= 7) | (d['HIR_JockeysClaim'].fillna(0) > 0)
    bad_disc = d['LTO_Comments'].fillna('').str.lower().apply(
        lambda c: any(w in c for w in ['slowly away', 'dwelt', 'pulled hard',
                                       'keen', 'hung', 'erratic'])
    )

    if use_score:
        d['MasterScore'] = (
            np.where(is_leader, 3, 0)
            + np.where(posaftupg_good, 3, np.where(posaftupg_ok, 1, 0))
            + np.where(quick_or_claim, 2, 0)
            + np.where(stride_decay, -2, 0)
            + np.where(posaftupg_bad, -2, 0)
            + np.where(bad_disc, -2, 0)
        )
    else:
        d['MasterScore'] = 0

    # Deterministic ranking across the whole field as loaded.
    if use_rank:
        d = d.sort_values(
            ['RH_RNo', 'MasterScore', 'StrideDecayFt', 'HIR_DSLR', 'HorseName'],
            ascending=[True, True, False, False, True]
        ).reset_index(drop=True)
        d['Rank_Worst'] = d.groupby('RH_RNo').cumcount() + 1
    else:
        d['Rank_Worst'] = 1

    # FIX 1: settle the real outcome, non-finishers included.
    # PositionNo == 1 -> the horse WON -> the lay LOSES.
    # PositionNo  > 1 -> defeated (includes 245-255, i.e. PU/F/UR) -> the lay WINS.
    d['IsLayWin'] = (d['HIR_PositionNo'] != 1).astype(int)
    d['DidFinish'] = d['HIR_PositionNo'].between(1, 50).astype(int)

    # FIX 3: two settlement conventions, reported side by side.
    # (A) fixed LIABILITY  - original convention, return on liability
    d['PL_Liab15'] = np.where(
        d['IsLayWin'] == 1,
        (STAKE / (d['HIR_BSP'] - 1.0)) * COMMISSION,
        -STAKE
    )
    # (B) fixed STAKE      - standard backer-style convention, return on stake
    d['PL_Stake15'] = np.where(
        d['IsLayWin'] == 1,
        STAKE * COMMISSION,
        -STAKE * (d['HIR_BSP'] - 1.0)
    )
    d['StakeNeeded_Liab15'] = STAKE / (d['HIR_BSP'] - 1.0)
    return d


# ---------------------------------------------------------------- reporting
def _fmt_tier(name, sub):
    n = len(sub)
    if n == 0:
        print(f"  {name:<44} 0 bets")
        return
    w = int(sub['IsLayWin'].sum())
    l = n - w
    roi_l = sub['PL_Liab15'].sum() / (n * STAKE) * 100
    roi_s = sub['PL_Stake15'].sum() / (n * STAKE) * 100
    roi_f = sub['PL_Fair'].sum() / (n * STAKE) * 100
    print(f"  {name:<44} {n:>8,}  laysLost {l/n*100:6.2f}%  "
          f"ROI_liab {roi_l:+7.2f}%  ROI_stake {roi_s:+7.2f}%  "
          f"ROI_DE-MARGINED {roi_f:+7.2f}%")


def field_completeness(df):
    """FIX 1 evidence: how much of the field the old filter was deleting."""
    print("\n" + "=" * 118)
    print("  FIX 1 - FIELD COMPLETENESS  (was: HIR_PositionNo BETWEEN 1 AND 50)")
    print("=" * 118)
    fin = int(df['DidFinish'].sum())
    non = len(df) - fin
    print(f"  Runners in loaded population : {len(df):>9,}")
    print(f"  Finished (1-50)              : {fin:>9,}  ({fin/len(df)*100:.1f}%)")
    print(f"  NON-FINISHERS (245-255)      : {non:>9,}  ({non/len(df)*100:.1f}%)")
    print("  -> the original script deleted every non-finisher.  For a LAY those are")
    print(f"     WINS (the horse was defeated), so ~{non:,} winning outcomes were removed")
    print("     and Rank_Worst was computed over a field of finishers only.")
    races = df.groupby('RH_RNo')['RH_NoOfRunners'].first()
    per_race = df.groupby('RH_RNo').size()
    full = (per_race == races).mean() * 100
    print(f"  Races where loaded runners == RH_NoOfRunners: {full:.2f}%  "
          f"(this is the real 'full field' number)")


def stride_coverage(df):
    """FIX 2 evidence: threshold sensitivity and the un-scorable population."""
    print("\n" + "=" * 118)
    print("  FIX 2 - STRIDE COVERAGE  (was: (ASL - SL_Finish) >= 0.20, in FEET)")
    print("=" * 118)
    n = len(df)
    has = int(df['HasStride'].sum())
    print(f"  Runners with BOTH ASL and SL_Finish  : {has:>9,}  ({has/n*100:.1f}%)")
    print(f"  Runners with NO usable sectional pair: {n-has:>9,}  ({(n-has)/n*100:.1f}%)")
    print("  -> those runners can never receive the -2, giving them a structural +2")
    print("     against every scorable runner.  The ranking is partly driven by data")
    print("     coverage rather than by form.")
    sub = df[df['HasStride']]
    if len(sub):
        print("\n  Threshold sensitivity (share of scorable runners that fire -2):")
        for t in (0.20, 0.40, 0.66, 1.00):
            fired = (sub['StrideDecayFt'] >= t).sum()
            note = ""
            if t == 0.20:
                note = "   <-- 0.20 ft = 6cm, effectively always true"
            if t == STRIDE_DECAY_FT:
                note = "   <-- IN USE"
            print(f"    >= {t:>4.2f} ft : {fired:>9,}  {fired/len(sub)*100:6.2f}%{note}")


def calibration(df):
    """Measures whether HIR_BSP is a genuine, well-calibrated market price."""
    print("\n" + "=" * 118)
    print("  DATA CALIBRATION CHECK - is HIR_BSP a real market price?")
    print("=" * 118)
    n = len(df)
    w = int((df['HIR_PositionNo'] == 1).sum())
    races = df['RH_RNo'].nunique()
    imp = (1.0 / df['HIR_BSP']).mean() * 100
    obs = w / n * 100
    over = (1.0 / df['HIR_BSP']).groupby(df['RH_RNo']).sum().mean()
    print(f"  Priced runner-rows (ALL prices)  : {n:>9,} across {races:,} races")
    print(f"  Rows with PositionNo == 1        : {w:>9,}  ({w/races*100:.1f}% of races have one)")
    print(f"  Observed win rate                : {obs:>8.2f}%")
    print(f"  mean(1/BSP) raw                  : {imp:>8.2f}%")
    print(f"  Mean race overround              : {over:>8.3f}")
    print(f"  Overround-normalised implied     : {imp/over:>8.2f}%")
    print(f"  Residual gap                     : {obs - imp/over:>+8.2f} pts")
    print("  -> a near-zero residual means HIR_BSP is genuine and well calibrated,")
    print("     and the sentinel handling and PositionNo semantics are sound.")
    return over


def price_bands(sub, overround=OVERROUND_FALLBACK):
    """Checks the price-band table that was arithmetically impossible.

    Market-implied probability MUST be mean(1/BSP), not 1/mean(BSP): for BSP
    these differ by ~9x because the price distribution is extremely skewed.
    The 1/mean(BSP) form was a bug in the first run of this script and it made
    the short-price bands look catastrophically mispriced.

    'implied_norm' divides by the race overround, which is the only form that
    is comparable with an observed win rate (sum of observed = 1.0 by
    construction, sum of raw implied = overround).
    """
    print("\n  Price bands (BSP).  A lay LOSES when the horse wins, so laysLost")
    print("  is the observed win rate and must be read against implied_norm:")
    edges = [1.01, 2.00, 3.00, 4.00, 5.00, 6.001]
    band = pd.cut(sub['HIR_BSP'], bins=edges)
    print(f"  {'band':<16} {'n':>8}  {'laysLost':>8}  {'implied':>8}  "
          f"{'implied_norm':>12}  {'gap(pt)':>8}  {'ROI_liab':>9}")
    for lbl, g in sub.groupby(band, observed=False):
        if len(g) == 0:
            continue
        los = (g['IsLayWin'] == 0).mean() * 100
        implied = (1.0 / g['HIR_BSP']).mean() * 100
        norm = implied / overround
        roi_l = g['PL_Liab15'].sum() / (len(g) * STAKE) * 100
        print(f"  {str(lbl):<16} {len(g):>8,}  {los:>7.2f}%  {implied:>7.2f}%  "
              f"{norm:>11.2f}%  {los - norm:>+7.2f}  {roi_l:>+8.2f}%")
    print("  gap = observed win rate MINUS overround-normalised implied.")
    print("  The classic favourite-longshot bias predicts gap > 0 at short")
    print("  prices (favourites win MORE than implied).  gap < 0 at short prices")
    print("  is the opposite of that bias and is the entire source of any profit")
    print("  below - it is an anomaly that must be reproduced on true Betfair BSP.")


def ablation(df):
    """FIX 4a: does the rank matter, does the score matter, or neither?"""
    print("\n" + "=" * 118)
    print("  FIX 4a - SELECTION ABLATION  (each component switched off in turn)")
    print("=" * 118)
    price = df['HIR_BSP'] <= BSP_CEILING
    neg = df['MasterScore'] < 0
    _fmt_tier("Tier 1+2: Rank<=2 AND Score<0 (ORIGINAL)",
              df[price & neg & (df['Rank_Worst'] <= 2)])
    _fmt_tier("Rank<=2 only     (score removed)",
              df[price & (df['Rank_Worst'] <= 2)])
    _fmt_tier("Score<0 only     (rank removed)",
              df[price & neg])
    _fmt_tier("Whole priced field (no selection at all)", df[price])
    print("\n  If 'Whole priced field' is not clearly worse than the tiers, the")
    print("  selection is adding nothing and the ROI is just the longshot bias.")
    return df[price]


def random_control(pool, seeds=CONTROL_SEEDS):
    """FIX 4b: lay a uniformly random price-eligible runner in the same races."""
    print("\n" + "=" * 118)
    print(f"  FIX 4b - RANDOM SAME-RACE CONTROL  ({seeds} seeds)")
    print("=" * 118)
    rng = np.random.default_rng(20260914)
    rois = np.empty(seeds)
    n_pick = 0
    pct_short = 0.0
    for i in range(seeds):
        rk = rng.random(len(pool))
        pick = pool.assign(_rk=rk).sort_values('_rk').drop_duplicates('RH_RNo')
        n_pick = len(pick)
        rois[i] = pick['PL_Liab15'].sum() / (n_pick * STAKE) * 100
        pct_short = (pick['HIR_BSP'] <= 2.0).mean() * 100
    print(f"  One random priced runner drawn per race (avg {n_pick:,} bets/seed,")
    print(f"  {pct_short:.1f}% of them at BSP <= 2.00 - i.e. the random lay is")
    print("  structurally similar to the system's picks by construction).")
    print(f"  Random ROI_liab : mean {rois.mean():+.2f}%   "
          f"5th {np.percentile(rois, 5):+.2f}%   95th {np.percentile(rois, 95):+.2f}%")
    return rois


def main():
    print("=" * 118)
    print("  5-YEAR MASTER LAY AUDIT - CORRECTED (2021-2026)   full field | fixed units | controls")
    print("=" * 118)
    print(f"  SETTLEMENT PRICE: {PRICE_LABEL}")
    conn = pyodbc.connect(CONN_PROFORM)
    raw = load_full_field(conn)
    conn.close()

    # FIX 6 - settle only races whose joined book is clean.  ~14% of races have
    # a partial/missing Betfair market (joined overround ~0.39) and would
    # corrupt the ROI with horses priced against a book that isn't whole.
    ov = (1.0 / raw['HIR_BSP']).groupby(raw['RH_RNo']).sum()
    good = ov[(ov > 0.85) & (ov < 1.15)].index
    n_drop = raw['RH_RNo'].nunique() - len(good)
    raw = raw[raw['RH_RNo'].isin(good)]
    print(f"  [1b] Kept {len(good):,}/{len(good)+n_drop:,} races with clean book "
          f"(overround 0.85-1.15); dropped {n_drop:,} partial-market races.")

    print("  [2/6] Scoring (zero lookahead)...")
    measured_overround = measure_overround(raw)
    df = score(raw)
    df = add_fair_settlement(df, measured_overround)
    field_completeness(df)
    stride_coverage(df)

    print("\n  [3/6] Settling results")
    print("\n" + "=" * 118)
    print("  RESULTS - both settlement conventions")
    print("=" * 118)
    print("  ROI_liab  = return on LIABILITY (original convention, stake = 15/(BSP-1))")
    print("  ROI_stake = return on STAKE     (standard convention, GBP 15 backer stake)")
    print()
    price = df['HIR_BSP'] <= BSP_CEILING
    neg = df['MasterScore'] < 0
    _fmt_tier("TIER 1 (worst #1 in field)", df[price & neg & (df['Rank_Worst'] == 1)])
    _fmt_tier("TIER 2 (worst #2 in field)", df[price & neg & (df['Rank_Worst'] == 2)])
    comb = df[price & neg & (df['Rank_Worst'] <= 2)]
    _fmt_tier("COMBINED TIER 1 & 2", comb)
    over = calibration(df)
    price_bands(comb, over)

    print("\n  Liquidity realism (fixed-liability model - stake needed per bet):")
    for lo, hi in ((1.01, 2.00), (2.00, 4.00), (4.00, 6.001)):
        g = comb[(comb['HIR_BSP'] >= lo) & (comb['HIR_BSP'] < hi)]
        if len(g):
            print(f"    BSP {lo:.2f}-{hi:.2f}: median stake GBP "
                  f"{g['StakeNeeded_Liab15'].median():>9,.0f}   "
                  f"max GBP {g['StakeNeeded_Liab15'].max():>9,.0f}   "
                  f"ROI_liab {g['PL_Liab15'].sum()/(len(g)*STAKE)*100:+7.2f}%")
    _fmt_tier(f"COMBINED restricted to BSP >= {LIQUIDITY_BSP_FLOOR:.2f}",
              comb[comb['HIR_BSP'] >= LIQUIDITY_BSP_FLOOR])

    print("\n  [4/6] Ablation control")
    pool = ablation(df)
    print("\n  [5/6] Random same-race control")
    rois = random_control(pool)
    real = comb['PL_Liab15'].sum() / (len(comb) * STAKE) * 100
    beats = (rois >= real).mean() * 100
    print(f"\n  Real COMBINED ROI_liab {real:+.2f}% vs the random distribution above.")
    print(f"  The random same-race lay matched or beat the system in {beats:.1f}% of seeds.")
    if beats > 5:
        print("  -> NOT SIGNIFICANT: a random priced runner in the same races does as well.")
    else:
        print("  -> selection beats random in >95% of seeds (worth a second look).")

    print("\n  [6/6] Writing exports ...")
    outdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
    os.makedirs(outdir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    csv = os.path.join(outdir, f"master_lay_FIXED_{stamp}.csv")
    comb.to_csv(csv, index=False)
    print(f"        {csv}")
    print(f"        {len(comb):,} combined-tier bets exported.")


if __name__ == "__main__":
    main()
