# DATA AUDIT — databases, dead code, and what was removed

*Run 2026-09-15. Re-runnable: `python tools\audit_db.py [--apply]` and
`python tools\audit_project.py [--apply]`. Raw output: `reports\db_audit.txt`.
Removals are listed in `archive\audit_<date>\MANIFEST.csv`.*

---

## 1. There were three databases, and the live one was broken

| Database | Tables | `Scraped_Results` | Weight / OR | State |
|---|---|---|---|---|
| `RACINGTV_2023_2026` | 5 | 229,614 rows, 2023-01-01 → **2026-09-13** | column did **not exist** | **the LIVE scrape DB** |
| `SCRAPED_PRODB` | 6 | 675,833 rows, 2021-01-01 → 2026-08-19 | columns exist, all-NULL | **frozen** |
| `PRODB` | many (form/ratings) | — | — | **frozen 2026-05-22** |

### The bug that stopped everything

`scripts/racingtv_db_updater.py` — the live scraper — INSERTs into

```
(RaceDate, RaceTime, CourseName, RaceTitle, HorseName, PosNo, JockeyClaim,
 SP, Comment, JockeyName, TrainerName, Age, Weight, OfficialRating)
```

but `RACINGTV_2023_2026.dbo.Scraped_Results` had only **14** columns. The last
five did not exist, so `JockeyName`, `TrainerName`, `Age`, `Weight` and
`OfficialRating` were **invalid column name (42S22)** on every race it scraped.

That single mismatch is why:

* no runner weight or official rating exists in the live DB,
* the odd column list looks correct in the code (it is — the table was never
  migrated),
* `selection_today.py` reads the frozen `SCRAPED_PRODB` instead, and had
  ratings for only ~16 of today's 420 runners.

**Fixed on 2026-09-15:** the five missing columns were added to the live table
(it is now 19 columns, identical in shape to `SCRAPED_PRODB.Scraped_Results`),
so the updater can write again.

### Which database is canonical

`RACINGTV_2023_2026` — it is the only one that still grows (results to
2026-09-13). `SCRAPED_PRODB` is frozen at 2026-08-19 and only its
**2021-01-01 → 2022-12-31** tail (~26,500 races) is unique, which is what a
five-year study needs.

---

## 2. Weights backfill (`scripts\backfill_weights.py`)

Results pages carry each runner's weight (`8-13` = 8st 13lb) and have done for
years. This is the one useful field still recoverable for history — the
**official rating is NOT on results pages**, only on racecards.

```bat
python scripts\backfill_weights.py --from 2023-01-01 --to 2026-09-14 --workers 10
python scripts\backfill_weights.py --db SCRAPED_PRODB --from 2021-01-01 --to 2022-12-31
```

* target DB default is the **live** one (was pointed at the frozen DB)
* newest dates first, so current form is filled first
* resumable at **race** level — safe to stop and restart
* waits for the runner rows instead of sleeping a fixed time: ~11 weights per
  race rather than ~3
* `python scripts\verify_weights.py` checks coverage and that weights have not
  collapsed to one repeated value

---

## 2. Weights backfill

### The fast way: `scripts\backfill_weights_api.py` (used for the 5-year rebuild)

The RacingTV **JSON API answers for historical dates** — verified back to
2021-01-05, 11/11 runners with a weight.  `/racing/racecards/{date}/{slug}/{HHMM}`
returns per runner: `weight`, `age`, `jockey`, `trainer`, `starting_price`,
`timeform_rating`, `form`, `days_since_run`.  No browser, no API key.

```
python scripts\backfill_weights_api.py --from 2021-01-01 --to 2026-09-14 --threads 6
```

* 5 years = ~87,000 races, **700+ races/min**, ~9.5 weights per race
* writes to **both** databases in one pass (a race is fetched once and written
  to every target still missing it)
* **`SP` is never touched** — the BSP study depends on the recorded SP

Four things had to be right before it worked, each found by testing:

1. **Index.** `UPDATE ... WHERE RaceDate/Time/Course/HorseName` had no index,
   so every runner update full-scanned 675k rows.  `scripts\add_results_index.py`
   added `IX_Scraped_Results_RaceHorse`; writes went from ~1s to **1.1 ms**.
2. **pyodbc waits forever.**  No query timeout by default, so one wedged
   statement held the write lock and stopped every worker — the "burst then
   stall" pattern.  `conn.timeout = 10` turns that into a caught failure.
3. **Names differ between the two databases.**  The old scraper stored
   `BrideysLettuce` where the API says `"Bridey's Lettuce"`, and Irish horses as
   `Amenita (IRE)` where the API says `Amenita`.  Matching now strips
   parenthesised origin tags and all punctuation — before that, Irish races
   contributed almost nothing (3.5 weights/race instead of 9.5).
4. **Long-lived processes wedge** after a few hundred races with no error.
   `scripts\backfill_weights_chunked.py` runs the range in bounded chunks, each
   its own process with a hard timeout, and mops up afterwards.

`scripts\backfill_weights.py` (the browser version, ~30 races/min) still works
as a fallback if the API changes shape.

---

## 3. Removed — stale documents that actively misled

Moved to `archive\audit_2026-09-15\stale_docs\`:

* `AUDIT_FINDINGS.md` — a list of "clean profitability entrypoints" naming four
  scripts that **no longer exist** (`audit_5year_historical.py`,
  `hybrid_racingtv_proform_lay_audit.py`, `custom_date_audit.py`,
  `ml_weight_and_oos_validator.py`), written before the real-BSP correction, so
  every ROI it calls clean is the inflated one.
* `SYSTEM_SPECIFICATION_AND_AUDIT.md` — the Master Lay specification,
  presenting as profitable a system that measured **+0.12% ROI_liab** at real
  prices.

`README.md` now points at `DATA_AUDIT.md` and records where they went.

## 4. Removed — junk, dead tables and stale text

* 12 scratch/probe files (`scratch_*.py`, `reports\_scrub_dry*.txt`, …)
* **19 dead tables scrapped** — every one dumped to CSV first under
  `archive\audit_2026-09-15\dropped_tables\`, so nothing is unrecoverable:
  * output of the archived (failed) lay/B2L systems: `Scored_Racecards_LaySheet`
    (SCRAPED_PRODB 2,312 rows + RACINGTV_2023_2026 670), `Scored_Racecards_B2L`
    (670)
  * `Online_Scraped_Results`, `BetfairSP`, `Notes`, `BF_Prices`, `CategoryLinks`,
    `DailyReport`, `DailyStatistics`, `HorsesToWatch`, `JSH`, `LiveShow`,
    `raSelections`, `raSelectionsTemp`, `SavedSystemsFields`, `SireStats`,
    `TJStats`, `trainer_jockey_onlyrun` — all empty
  * kept: `dtproperties` (legacy SQL 2000 diagram table, nothing to gain)
* stale text in `auto_daily_pipeline.py`: its final summary advertised the
  Lay Sheet and B2L tables and the "filter B2L at odds >= 20.0" advice, and
  named the wrong database for step 1 (`SCRAPED_PRODB`, actually
  `RACINGTV_2023_2026`)

Table counts after the scrap: `PRODB` 53, `RACINGTV_2023_2026` **3**
(`Scraped_Results`, `Scraped_RaceIQ`, `Scraped_RaceIQ_Ranks`),
`SCRAPED_PRODB` **4**.

### Two bugs found in the audit tools themselves

* `audit_db.py` reported a table as **DROPPED** and left it there: `pyodbc`
  connects with autocommit **off**, so `DROP TABLE` without `c.commit()` is
  rolled back when the connection closes. Fixed.
* the "is this table used?" test was plain substring matching, which gave two
  false keeps: `BetfairSP` matched the Betfair URL
  `promo.betfair.com/betfairsp/prices`, and `Notes` matched a docstring
  heading in `book_odds.py`. It now requires real usage (`dbo.X`,
  `FROM/JOIN/INTO/UPDATE X`).

## 5. Kept deliberately (not dead code)

The evidence behind published verdicts — re-running one is how a number gets
checked: `bens_bookmaker_backtest.py`, `bens_drop_pct.py`,
`bens_exchange_move.py`, `bens_odds_check.py`, `bens_odds_fake_check.py`,
`bens_forward.py`, `ben_report.py`, `pick_tracker.py`, `shorten_test.py`.
Manual tools: `export_daily_card.py`, `selection_today.py`,
`backfill_weights.py` (run by `after_backfill.bat`).
`tools\` (security + audits) and `tests\` are never candidates.

