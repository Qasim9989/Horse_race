# STRIDE SYSTEM — previous-run SPEED and STRIDE, priced at BSP

**Status: candidate system. Forward test pending.** An earlier version of these
numbers circulated at +12.89% ROI; that figure came from a broken frame and is
superseded by everything in section 3. This document exists so the old and new
numbers are never confused again.

The two rules are **separate systems** and are documented separately throughout:
one selects on last-run **speed**, the other on last-run **stride length**.

---

## 1. The two systems

| | **SPEED system** | **STRIDE system** |
|---|---|---|
| Pick in each race | highest previous-run **top speed** | longest previous-run **stride length** |
| Column (lagged) | `SData.MPH_Finish` | `SData.SL_Finish` |
| Staking | 1 unit per race, win back | 1 unit per race, win back |
| Price | Betfair BSP (see §4 — early prices destroy both) | Betfair BSP |
| Look-ahead | none (audited, §5) | none (audited, §5) |

Both rules read **only the horse's previous run**. The lag is `shift(1)` per
horse ordered by race datetime (`NEW_RH.RH_DateTime`), so at decision time the
figure is last-run-only and public. A horse must carry a previous-run figure to
be eligible, so first-time-out runners are never picked (never bet against them,
just never bet on them).

Coverage: stride figures exist for **43.6% of priced runners**, and inside the
races that carry any at all those figured runners are **88% of the market's
implied probability** — so each covered field also has a slice (usually the
outsiders) with no figure. Both systems only fire in covered races,
≈600 picks/month.

> [!IMPORTANT]
> **The feed's edge is `2026-04-30`.** SData holds no stride rows after that
> date while BFSP runs to `2026-09-15`, so a window of 2026-05-01 → 2026-09-15
> produces **0 picks** (measured, `reports/_stride_holdout.txt`). Until the
> stride feed is refreshed, these systems cannot be produced live at all — and
> for the same reason there is **no untouched forward sample** to test them on.

---

## 2. The OLD system — numbers as they stood (SUPERSEDED, do not quote)

Frame: `SData JOIN BFSP`, then filtered to runners with a previous run.
2024-01-01 → 2026-04-30, **22,704 races / 171,662 runners**. Run at BSP, 1u.

| | SPEED | STRIDE |
|---|---|---|
| ROI at BSP | **+12.89%** (like-for-like subset +11.86%) | **+11.83%** (+13.15%) |
| Strike rate | 20.6% | 18.3% |
| Max drawdown | 195u | 206u |
| Worst losing run | 46 bets | 44 bets |
| Paired excess vs same-race field | +7.24% (t=2.48) | +6.18% (t=1.87) |

### Why these are not usable as absolute returns

| Check | Old frame | A real market |
|---|---|---|
| Field implied probability per race | **0.928** (2021-23: 0.899) | ~1.00 |
| Races with no flagged winner | **1,519 (6.7%)** | ~0 |
| Backing **every** runner at BSP | **+4.02%** | **−2.21%** |

The frame was missing ~7–10% of every field — the horses with no previous run,
and in 1,519 races the *winner* itself was dropped by that filter. Removing
guaranteed losers from a pool lifts every ROI in it, which is exactly what the
+12.89% was. A market cannot pay +4% for backing all runners; that line was the
tell.

### What the old frame *did* establish (these survive the rebuild)

- **Direction of the signal.** Bias-free calibration on that frame: SPEED won
  **20.56%** of its races while its BSP implied **18.78%** (**+1.77pts, 1.16×**);
  STRIDE **18.29%** vs **16.81%** (**+1.48pts, 1.15×**). The control — a random
  runner in the same frame — showed **+0.97pts (1.07×)**, i.e. the frame's own
  bias floor, so the rule-specific edge is the difference.
- **Both rules pick the same horse in 40.0% of races** (they read the same
  previous run), which is why fusing them cannot create a large new edge (§6).
- **Price:** early prices kill both systems (§4).
- **No look-ahead** in the lagged design (§5).

---

## 3. The NEW system — rebuilt on a complete field

The frame is now built the other way round:

```
BFSP (every priced runner, i.e. the whole field)  LEFT JOIN  lagged SData metrics
```

so the field is complete (winner never missing) and only the *picks* are
restricted to runners carrying a previous-run figure. 2021-01-01 → 2026-04-30,
**68,910 races / 646,527 runners**.

| Frame integrity | New frame | Old frame |
|---|---|---|
| Field implied probability per race | **1.0033** ✅ | 0.928 |
| Races with no flagged winner | **205 (0.3%)** | 1,519 (6.7%) |
| Backing every runner at BSP | **−2.213%** ✅ | +4.02% |

The −2.213% baseline is the whole point: the frame now behaves like a real
market, so the pick ROI below is the first absolute number in this project that
can be trusted.

Runners carrying a previous-run figure return **+3.40%** as a pool. It is worth
knowing that both systems draw from that pool — part of their return is the pool
itself, not the metric (§6).

### 3a. SPEED system — highest previous-run top speed

| Window | Picks | Strike | BSP implied | Gap | Gross ROI | **Net 2%** | Net 5% | Max DD | Worst run | Paired t |
|---|---|---|---|---|---|---|---|---|---|---|
| 2021-2023 *(held out)* | 15,714 | 18.80% | 17.25% | +1.55 | +8.92% | **+7.12%** | +4.41% | 268u | 41 | 3.44 |
| 2024-2026 | 22,706 | 19.83% | 18.03% | +1.80 | +12.94% | **+11.08%** | +8.29% | 286u | 45 | 4.74 |
| **Pooled** | **38,420** | 19.41% | 17.71% | +1.70 | +11.30% | **+9.46%** | +6.70% | 286u | 45 | **5.84** |

### 3b. STRIDE system — longest previous-run stride length

| Window | Picks | Strike | BSP implied | Gap | Gross ROI | **Net 2%** | Net 5% | Max DD | Worst run | Paired t |
|---|---|---|---|---|---|---|---|---|---|---|
| 2021-2023 *(held out)* | 15,714 | 18.07% | 16.55% | +1.52 | +6.19% | **+4.42%** | +1.78% | 278u | 53 | 2.81 |
| 2024-2026 | 22,706 | 16.97% | 15.77% | +1.20 | +9.73% | **+7.88%** | +5.10% | 452u | 53 | 3.15 |
| **Pooled** | **38,420** | 17.42% | 16.09% | +1.33 | +8.28% | **+6.47%** | +3.74% | 452u | 53 | 4.18 |

Notes that apply to both tables:

- *Net 2% / Net 5%* = Betfair commission taken off winning bets (the rate
  depends on your turnover). **Both systems stay positive even at 5%.**
- *Paired t* = t-statistic of (pick return − same race's all-runner average),
  one bet per race, ~38k races — the sample-size-driven confidence measure.
- *Max DD* is in units of one stake, level stakes. 286u over 38,420 bets is
  0.74% of turnover.
- **SPEED is the stronger system on every axis** except worst-losing-run, which
  STRIDE also loses by 8 bets.

### 3c. Which to bet

| | SPEED | STRIDE |
|---|---|---|
| Pooled net ROI at BSP (2% comm.) | **+9.46%** | +6.47% |
| Strike rate | 19.4% | 17.4% |
| Max drawdown | **286u** | 452u |
| Confidence (paired t) | **5.84** | 4.18 |
| Held-out window | +7.12% | +4.42% |

SPEED alone is the system. STRIDE is a weaker confirmation of the same idea,
useful as a filter (§6) rather than as a standalone.

---

## 4. Price execution — BSP only, and this is not negotiable

Same picks, same races, four price series. Measured on the superseded frame, so
**read the ordering, not the levels** (same bets, same outcomes, only the price
differs — that comparison is immune to the frame's bias).

**SPEED system** — 22,670 picks:

| Price | ROI | Max DD | Longest time underwater |
|---|---|---|---|
| **BSP** | **+11.86%** | **195u** | 4,689 bets |
| morning (race-day early) | −1.80% | 1,009u | 22,115 bets — **never recovered** |
| evening (night before) | −0.00% | 804u | 22,115 bets — **never recovered** |
| pre-off (just before the off) | +1.65% | 846u | 17,561 bets |

**STRIDE system** — 22,670 picks:

| Price | ROI | Max DD | Longest time underwater |
|---|---|---|---|
| **BSP** | **+13.15%** | **206u** | 2,423 bets |
| morning | −2.91% | 1,059u | 22,178 bets — **never recovered** |
| evening | −0.88% | 771u | 22,178 bets — **never recovered** |
| pre-off | +2.02% | 625u | 20,134 bets |

Both systems pick mid-to-long priced horses, and those **drift out** through the
day (evening → morning → BSP). So the early prices are the shortest and the
worst: taking them doesn't just shave the edge, it removes it and leaves the
equity underwater for the whole sample, with a **4–5× deeper drawdown**.

**Rule for both systems: bet at BSP. Never morning, never evening.**

---

## 5. Look-ahead audit (both systems, passed)

`scripts/stride_lookahead_check.py`, on the lagged frame:

| Check | Result |
|---|---|
| Lag is by time | `days_since` never negative (0 minimum) |
| Lagged vs the CURRENT race's own figures | lagged SPEED **+11.86% / 20.6% strike** vs current-race column **+289.58% / 54.2% strike** — that absurd version is what look-ahead looks like, and it is not the one used |
| Metric vs market | within-race rank correlation with BSP **+0.135** (a price-copied metric would be ~+1.00) |
| Selection cannot see results | shuffle every result and price → picks **bit-identical** (38,420-row frame: same rows, same order) |
| Chronology | picks in date order; equity is a cumulative sum in that order, never re-sorted by return |
| Self-lag via duplicate rows | 516 duplicate (race, horse) rows; only **1** provably same-day; removing every lag-equals-current row moves ROI +11.86% → +10.92% |

Conclusion: the picks are made from pre-race information only. The one hole
found (duplicate rows) is worth ~1 point and 1 row of genuine self-lag.

---

## 6. Fusion, the pool effect, and what is still unproven

### 6a. Fusing SPEED and STRIDE

Both rules read the same previous run, and they pick the **same horse in 40.0% of
races** — so fusing cannot create a big new edge. Measured on the superseded
frame (**to be re-run on the clean frame**):

| Variant | ROI | Strike | Max DD | Worst run | Paired t | Verdict |
|---|---|---|---|---|---|---|
| SPEED only | +11.86% | 20.6% | 195u | 46 | 2.10 | the baseline |
| STRIDE only | +13.15% | 18.3% | 206u | 44 | 2.44 | the confirmation |
| **agree** (one horse tops both) | +12.42% | **22.0%** | **165u** | **37** | 1.93 (p=0.027) | fewest bets, smallest drawdown, highest strike |
| **union** (both picks, 2u/race) | +12.50% | 19.4% | 256u | 75 | **3.21 (p=0.0007)** | most confident, smoothest curve |
| score (sum of percentile ranks) | +10.18% | 19.3% | 246u | 48 | 1.30 (p=0.097) | **rejected** — dilutes to "2nd on both" |

Practical choice: **SPEED alone** as the system; use the *agree* filter when you
want fewer bets and a shallower drawdown. Never the rank-sum score.

### 6b. How much of the return is the metric, and how much is the pool?

On the clean frame: the pool of all runners carrying a previous-run figure
returns **+3.40%**; the SPEED pick returns **+11.30%**. So roughly **+7.9 points
is the metric**, and **+5.6 points is the pool** ("has run before, in a
TPD-covered race"). Only the first is cleanly attributable to stride, and the
pool number must be treated as partly a coverage artefact.

### 6c. Honest caveats

1. **No forward test yet.** Both windows are historical. Nothing here has been
   traded live.
2. **Coverage dependency** — the systems only fire where SData exists (43.6% of
   priced runners carry a figure). Lose that feed and the systems are gone.
3. **12% of the implied mass inside covered races is unmatched** to a stride row.
   Those runners win 2.13% vs 2.27% implied, i.e. they are ordinary longshots
   rather than only non-finishers, so the pick stream is not purely dodging
   dropped losers — but the effect is not fully separated.
4. **Commission is modelled**, not measured: 2% and 5% flat on winning bets.
   Both systems survive 5%, which is the useful robustness statement.
5. **Multiplicity**: 2 metrics × 4 prices × 5 variants were tested. The pooled
   paired t (5.84 SPEED / 4.18 STRIDE) is what carries the claim.

---

## 7. Reproduction

```
python scripts\stride_clean_frame.py        # the clean frame + BOTH systems, per era, gross and net
python scripts\stride_drawdown_prices.py    # BSP vs morning vs evening vs pre-off, with drawdowns
python scripts\stride_combined.py           # the five fusion variants
python scripts\stride_lookahead_check.py    # the audit in section 5
python scripts\sdata_prev_clean_check.py    # integrity / calibration / paired test on a lagged frame
python scripts\sdata_prev_significance.py   # bootstrap CIs over races
python scripts\sdata_prev_stride_ev.py --from 2021-01-01 --to 2023-12-31   # rebuild a lagged frame window
```

Evidence files (all generated, safe to delete and regenerate):

| File | Contains |
|---|---|
| `reports/_stride_cleanframe.txt` | section 3 — the trustworthy numbers |
| `reports/_stride_dd.txt` | section 4 — price/drawdown comparison |
| `reports/_stride_combined.txt` | section 6a — fusion variants |
| `reports/_stride_lookahead.txt` | section 5 — the audit |
| `reports/_stride_oos.txt` | the 2021-2023 held-out run on the old-style frame |
| `reports/_stride_sig.txt` | bootstrap CIs and episode detail |
| `reports/_sdata_prev.pkl` | lagged frame 2024-2026 (`.pkl` also kept for 2021-2023, and a backup) |

Data sources: `PRODB.dbo.SData` (stride/sectional), `PRODB.dbo.BFSP` (true
Betfair SP + WinLose), `PRODB.dbo.NEW_RH` / `NEW_H` / `NEW_C` (race, horse,
course names). Note `SData.RecordTime` is a **load timestamp, not the race
time** — the race datetime must come from `NEW_RH.RH_DateTime`.

---

## 8. Status and next steps

**Status: candidate system, not funded, not traded.** The claim being made here
is narrow and specific:

> At Betfair BSP, one bet per race on the horse with the best **previous-run top
> speed** (in stride-covered races) has returned **+9.46% net of 2% commission**
> over 38,420 bets, positive in both a held-out window (+7.12%) and the
> discovery window (+11.08%), with a 286u worst drawdown and a 45-bet worst
> losing run. The **stride-length** rule confirms the same signal at a weaker
> +6.47% net.

**Next, in order:**

1. **Refresh the stride feed (blocker).** SData stops at 2026-04-30 while prices
   run to 2026-09-15, so no picks can be produced for the last 4.5 months — and
   no forward sample exists. Nothing else on this list can proceed without it.
2. **Build the daily picks generator** — the repo already has the pattern
   (`selection_today.py`, `bens_today.py`); stride has none, so today the system
   exists only as an analysis script. One command, run before the off, printing
   one runner per race with the BSP instruction.
3. **Build the forward log and settlement** — record picks pre-off, settle from
   the BSP file, and track net-of-commission ROI against the expected +9.46%.
   The existing price-log/BFSP tooling already does this for other systems.
4. **Forward paper-trade** the refreshed feed, staking nothing, until the log
   covers ≥500 settled bets. This is the only thing that converts a candidate
   into a system.
5. **Separate pool from metric** — split the +11.30% into "has run before / TPD
   covered" and "stride speed", then check whether the metric survives on its
   own inside a *fully* matched field.
6. **Chase the 12% unmatched** inside covered races (name matching vs genuine
   missing rows) so the pool number is not a coverage artefact.
7. **Re-run the fusion** (agree / union) on the clean frame — the §6a table still
   comes from the superseded frame.
8. **Size the bank** off the clean 286u drawdown, not the old biased one, and
   decide the operating shape: SPEED only (~600 bets/month), or SPEED with the
   *agree* filter (fewer bets, 165u-class drawdown, 22% strike).

**Falsifiers — what would kill this:**

- a forward sample at BSP net of commission that comes out ≤ 0;
- the metric effect disappearing once the pool is fully matched (i.e. if the
  whole edge was "horses that have run before");
- SData coverage changing enough that the producing population is no longer the
  measured one.



