# Speed & Stride — repair and honest measurement (2026-09-19)

## ⚠️ Correction

An earlier draft of this file said the tab's figures were "hard-coded strings"
and that "none of that survived measurement". **That was wrong, and it has been
put back.** The figures are measured and documented in `STRIDE_SYSTEM.md` §3,
from a validated frame — `PRODB.dbo.BFSP LEFT JOIN lagged PRODB.dbo.SData`,
2021-01-01 → 2026-04-30, **68,910 races / 646,527 runners** — whose lie-detector
is that backing *every* runner at BSP returns **−2.213%** with a field implied
probability of **1.0033**: that is a real market, not a biased subset. Level
stakes at Betfair BSP, net of 2% commission:

| Rule | Picks | Strike | BSP implied | Gross | **Net 2%** | Held-out era | Max DD | paired t |
|---|---|---|---|---|---|---|---|---|
| SPEED (fastest prev run) | 38,420 | 19.41% | 17.71% | +11.30% | **+9.46%** | +7.12% | 286u | 5.84 |
| STRIDE (longest prev stride) | 38,420 | 17.42% | 16.09% | +8.28% | **+6.47%** | +4.42% | 452u | 4.18 |
| AGREE (fusion §6a) | fewer | 22.0% | — | — | **+12.42%** | not re-run | 165u | 1.93 |

Evidence: `reports/_stride_cleanframe.txt`, `reports/_stride_holdout.txt`,
`reports/_stride_sig.txt`, `reports/_stride_lookahead.txt`. Both rules still
clear costs at 5% commission (+6.70% / +3.74%). **Only AGREE is flagged**: the
note itself says its +12.42% was measured on the *superseded* frame (§6a) and is
still to be re-run on the clean frame. `STRIDE_SYSTEM.md` documents its own
caveats: no forward test, coverage dependency (43.6% of priced runners),
commission modelled rather than measured.

## What my RaceIQ run actually is

The 49-day number below is **not a replication** of that audit and cannot refute
it — it differs on every axis that matters:

| | Audit | My run |
|---|---|---|
| Feed | Proform SData | RacingTV RaceIQ v2 |
| Population | stride-covered races only | every race |
| Window | 2021-01-01 → 2026-04-30 (38,420 picks) | 2026-08-01 → 2026-09-18 (2,195 picks) |
| Price | Betfair BSP, net 2% commission | racecard SP, win-only |
| Prior figure | lagged previous run, complete field | previous run, but clean speeds exist only from 01/08 |

§4 of the note is the decisive one: **early prices kill both systems — the edge
exists at BSP only.** So the only honest reading of my numbers is narrow: *the
RaceIQ feed, wired as it is today, does not reproduce the SData result on a
49-day window at SP.*

| Category | Bets | /day | Win % | Avg SP | WIN ROI at SP |
|---|---|---|---|---|---|
| AGREE (Speed + Stride) | 403 | 9.0 | 12.2 | 27.19 | −47.42% |
| SPEED System Pick | 820 | 17.8 | 17.0 | 19.80 | +0.09% |
| STRIDE System Pick | 972 | 19.8 | 11.9 | 27.24 | −23.16% |
| Control: favourite | 1,801 | 36.8 | 34.2 | 2.97 | −13.86% |

Raw picks: `speed_stride_backtest.csv`.

## The real open question

The note names its own blocker: **SData stops on 2026-04-30**, so there are no
picks for the last 4.5 months and **no untouched forward sample exists**. That is
what today's data work changes — there is now a live stride/speed feed (RaceIQ
v2, 96.5% of runners carrying a top speed). What has *not* been done is test
whether RaceIQ's lagged figures carry SData's signal. That test is:

1. lagged previous-run RaceIQ figures, restricted to stride/speed-covered races;
2. settled at **real Betfair BSP** (`PRODB.dbo.BFSP`, runs to 2026-09-15), net of
   2% commission;
3. with the audit's own control — the pool of covered runners (+3.402% in the
   audit frame) and the all-runner baseline;
4. and compared with SData on the overlap window (RaceIQ starts 2023-01, so
   2023-01 → 2026-04 is the common ground).

Until that runs, the tab shows the audited figure **with its source** (SData,
BSP, 38,420 bets) and the RaceIQ/SP check beside it as a separate line.


## Part 1 — the data (fixed)

`raceiq_scrape_v2.py` (correct parser: TopSpeedMph / StrideM / FspPct, one row
per runner) had been sitting unused in staging since 13/09, while the cloud DB
was fed by the old v1 scraper whose `TopSpeed` is unusable — 35% of its recent
reads are the "0-20MPH" *column label* captured as the number 20, `AvgFrequency`
is NULL on every row, and only ~39–46% of runners got a speed at all.

* Backfilled v2 for all 49 dates, 2026-08-01 → 2026-09-18 (0 rejected values).
* `scripts/sync_results_ledger.py` now treats **v2 as the authority** and keeps
  v1 only as a **stride-only** fallback (v1's stride is trustworthy: mean 7.23 m
  vs v2's 7.25 m). v2 rows are refreshed outright so a bad v1 value cannot survive.
* Junk bands (speed 25–55 mph, stride 5–10 m) are applied once, in
  `cloud_app/speed_stride_rule.py`, and reused everywhere.
* `scripts/build_all_selections_cache.py` and `cloud_app/app.py` now look up
  **each metric separately** — a recent stride-only row used to shadow the
  horse's speed from an earlier run — and match names after stripping the
  country suffix (`"X (IRE)"` silently missed before).
* `scripts/auto_daily_pipeline.py` gained step 1c, which re-scrapes the last
  four days into v2 every morning (idempotent, repairs late-published races).

### Effect (cloud DB telemetry)

| | Before | After |
|---|---|---|
| August rows / with speed | 893 / ~50% | **10,295 / 96.5%** |
| September rows / with speed | 1,051 / ~42% | **6,828 / 95.3%** |
| 18/09 rows / with speed | 48 / **0** | **491 / 450** |
| Today's cache picks | 44 (2 SPEED, 42 STRIDE) | **88 (38 SPEED, 35 STRIDE, 15 AGREE)** |


## Part 2 — one rule (fixed)

There were three implementations, which is why the numbers never matched:

| | Rule before | Picks on 19/09 |
|---|---|---|
| Tab / cloud cache | no threshold at all, up to 2 per race | 44 |
| Settlement ledger | ≥35 mph or ≥6.80 m, exactly 1 per race | 58 |
| The tab's headline | hard-coded +6.47 / +9.46 / +12.42% | — |

Now `cloud_app/speed_stride_rule.py` holds the thresholds, the junk bands and the
category labels, and the app tab, the cache builder and the ledger all call it.
The ledger also stopped using each horse's **all-time best** prior reading (a
two-year-old peak could pick today's runner) and uses the **most recent** reading
per metric — the same "previous run" meaning the tab and the cache use. It logs
one row per pick (AGREE, or SPEED *and* STRIDE), keeping its historical
sub-system labels (`Dual Agree` / `Top Speed #1` / `Top Stride #1`).

## Part 3 — provenance on the ROI figures (fixed)

The three figures in `app.py` and the cache builder were the **audited** results
from `STRIDE_SYSTEM.md` §3 — real measurements, but shown with no source, no
sample size and no hint that two of them come from a feed (SData) that no longer
updates. They now carry their provenance: every row's Edge column reads
`Audit: +9.46% net at BSP (Proform SData, 38,420 bets)`, and each header card
adds the audit's strike, held-out era, drawdown, t-statistic and window.

Alongside it sits the live RaceIQ check, deliberately a *separate* line because
it is a different feed, population and price. `scripts/backtest_speed_and_stride.py`
now measures each category and writes `cloud_app/speed_stride_claims.json`, and
the app and the cache label every row from that file. With no measurement on
file the label reads **"not measured yet"** rather than inventing a number.

Re-run any time (49 days takes ~3 minutes):

    python scripts\backtest_speed_and_stride.py --from 2026-08-01

## Two further bugs found on the way

1. **The daily pipeline's ledger step was crashing.** `sync_results_ledger.py`
   still wrote the old, narrower column set (`early_pl`, `sp_pl`) into
   `system_results_ledger`, which `settle_daily_results.settle_ledger()` (called
   by the app) now rebuilds with different columns — so the sync died with
   "no column named early_pl" *after* the telemetry step. It now detects the live
   schema and steps aside. Settlement is owned by `settle_daily_results.py`.
2. **Country suffixes** broke telemetry matching in the cache builder (above).

## Caveats

* Measured at the **starting price**, not Betfair BSP (the claim said BSP). SP is
  the stricter, more available measure and is what the AI sub-system backtests
  used, so the numbers are comparable across systems.
* Clean top speeds only exist from 2026-08-01, so a speed "prior" is trusted only
  from that date; strides go much further back.
* 11% of SPEED picks (106 of 926) had no price or finishing position and were
  excluded — mostly runners that did not take part. Exclusions are counted in
  the claims file as `unpriced`.
* 49 days is a small window. It is enough to reject +9.46%, not enough to prove
  a long-run −23% for STRIDE.

## Still open

* Decide whether to keep STRIDE at all (it fires in most races and loses 23%).
* `Power Rank #1` is still logged as a bet in the AI ledger despite backtesting
  at −15.32% over 2.6 years.
* `racing_form.db` is 93 MB and committed to the cloud repo; nothing publishes
  it automatically (`publish_cloud_caches.py` only ships the three JSON caches),
  so the app's on-cloud DB can drift from the laptop's.
* `racingtv_db_updater.py` still scrapes the v1 RaceIQ half every morning even
  though v2 is now the source of truth.
