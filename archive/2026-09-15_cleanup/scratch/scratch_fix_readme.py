"""Rewrite README.md so it describes what actually exists (facts checked
against the folder on 2026-09-15)."""
import io
import os

ROOT = r"E:\Test\racing-form-system"
PATH = os.path.join(ROOT, "README.md")

NEW = """# Racing Form System - price & market tooling

Measures whether **bookmaker prices** can beat the **Betfair exchange**, and
keeps the historical scraper/BSP pipeline running. It is a measurement project:
every selection-based system that was tested here failed at real prices, and
the ones that only looked profitable relied on a bookmaker SP with a 21% margin
(see AUDIT_FINDINGS.md). Those systems are in `archive/`.

## Start here

**`dashboard.bat`** - opens a live dashboard at http://localhost:8501 with the
12 bookmakers' prices, the margin each one takes, who holds the best price, and
- once the SP is in - the price you can get against the real Betfair SP.

---

## Launchers (all in this folder)

| Launcher | What it does | When to run |
|---|---|---|
| `dashboard.bat` | Live dashboard (Streamlit). Optionally refreshes prices first. | Any time |
| `run_book_odds.bat` | Snapshot every bookmaker price -> `PRODB.dbo.BookOdds`, rebuild the price log, print the book-vs-BSP report | Race morning, and again during the day |
| `run_auto_daily_pipeline.bat` | Scrape finished races -> backfill real Betfair BSP -> Ben's card -> book odds snapshot | Once daily |
| `run_price_log.bat` | Scrape today's racecard and build the (now automatic) price-log sheet | Superseded by `run_book_odds.bat` |
| `report.bat` | Analyse every price log and print the "does my price beat Betfair" verdict | Weekly |

---

## What the tooling does

- **`scripts/rtv_api.py`** - client for RacingTV's JSON API, which carries every
  price shown on the racecard ("odds courtesy of Oddschecker"). No browser, no
  API key. Endpoints and required headers are documented in ODDS_API_FINDING.md.
- **`scripts/book_odds.py`** - `snapshot` (12 books x every runner -> SQL),
  `pricelog` (auto-filled log sheet), `report` (price vs BSP, margins),
  `value` (overlay finder vs the de-vigged consensus).
- **`scripts/dashboard.py`** - the visual front-end over the same tables.
- **`scripts/betfair_bsp_backfill.py`** - downloads the true Betfair SP into
  `PRODB.dbo.BFSP` (the old `HIR_BSP` column was a bookmaker SP with a 21.3%
  overround, so every historical ROI computed from it was inflated).
- **`scripts/racingtv_db_updater.py`**, **`scripts/racecard_today.py`** - keep
  `SCRAPED_PRODB` supplied with results, sectionals and declared runners.

## Verified findings at real prices

| Finding | Number |
|---|---|
| Tightest account (bet365) | **+18.2% margin** on the median race |
| Widest (Sky Bet / Betway) | +23.5% to +24.2% |
| Best of all 12 accounts (shopping every runner) | **+15.7% margin** |
| Betfair SP overround | ~0.2% |

- bet365 holds the best price on **37.8%** of runners, Paddy Power on 29.4%;
  bet365's price is worth **+6.7%** over the next-best account.
- Lay systems, B2L, longshot filters, 289 band tests, ML (AUC 0.80) and all
  in-play offsets failed once the bookmaker-margin artifact was removed.
- RacingTV only publishes prices on the **morning of the race**.

Full write-up: `ODDS_API_FINDING.md`.

---

## Database architecture

- **`PRODB`** - `BFSP` (true Betfair SP + WinLose), `BookOdds` (timestamped
  bookmaker snapshot, 12 books per runner), plus the historical form tables
  (`NEW_RH`, `NEW_HIR`, `NEW_H`, `NEW_C`, `SData`).
- **`SCRAPED_PRODB`** - live scraped racecards, results and sectionals
  (`Scraped_Racecards`, `Scraped_Results`, `Scraped_RaceIQ`).

Both are localdb SQL Server instances on this machine.

---

## Documentation

- `ODDS_API_FINDING.md` - how the bookmaker prices are obtained, measured
  margins, the dashboard, and the open questions.
- `AUDIT_FINDINGS.md` - the look-ahead and BSP-margin problems that invalidated
  the earlier results.
- `SYSTEM_NOTES.md` - database schemas and column names.
- `FRESH_NOTE.md` - the "no dashboard, no generated JSON" standard.
- `SYSTEM_SPECIFICATION_AND_AUDIT.md` - original specification.

## Tests

```
python tests\\test_book_odds.py     # price/BSP maths and report formatting
python tests\\test_dashboard.py     # renders the whole dashboard headlessly
```

## Archive

`archive/` holds every removed system (Master Lay, B2L, in-play tools, bias and
robustness analysers, the old web app launchers) plus
`archive/2026-09-15_cleanup/`, which records this tidy-up in `MANIFEST.csv`
(every file that moved, with its old and new path). Nothing was deleted.

`archive/2026-09-15_cleanup/data_generated/` holds ~735 MB of generated JSON
from the old dashboard - safe to delete to reclaim the space.
"""

with io.open(PATH, "w", encoding="utf-8", newline="\r\n") as f:
    f.write(NEW)
print(f"README.md rewritten ({len(NEW)} chars)")
