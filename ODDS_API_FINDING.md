# BOOKMAKER ODDS — SOLVED (RacingTV / Oddschecker JSON API)

**Date found:** 2026-09-15
**Status:** working, validated against the raw racecard HTML, storing to SQL.

## 1. The finding

The RacingTV racecard says *"odds courtesy of Oddschecker"* and the price cells
look empty in a headless scrape. That was a **timing artefact, not a bug**: the
feed only publishes prices on the **morning of the race**. At 11:31 on 15/09/2026
all **38/38** of that day's UK/IRE races were priced; the *next* day's card
(Beverley, Yarmouth, Sandown 16/09) returned **11 bookmakers with zero prices**.

The prices are served by a plain JSON API — no browser, no login, no API key.

## 2. The endpoints

All under `https://api.racingtv.com`:

| Endpoint | Returns |
|---|---|
| `/racing/racecards/list/{YYYY-MM-DD}` | whole day, all meetings + race ids |
| `/racing/racecards/{date}/{course}/{HHMM}` | one race + clean runner fields |
| `/racing/runner/odds?runner_ids[]=...` | **every book's price for those runners** |

Required headers (the API returns **403** without them):

```
x-requested-with: racingtv-web/5.6.0   <-- the important one (CSRF guard)
referer:          https://www.racingtv.com/
accept:           application/json
authorization:    (sent EMPTY for anonymous users - no key needed)
User-Agent:       any browser string
```

Odds payload shape (per runner):

```json
{"id": 8807876,
 "odds": [{"price": {"decimal": "13.00", "fractional": "12/1",
                     "moneyline": "+1200"},
           "each_way_places": 3, "each_way_denominator": 5,
           "fluctuation_type": "drifting",     // or "shortening"
           "bookmaker_id": 5}]}
```

`bookmakers` in the same payload maps `bookmaker_id` -> name (bet365=1,
Sky Bet=3, Paddy Power=5, Ladbrokes=6, Coral=7, Betway=9, William Hill=14,
Unibet=24, BOYLE Sports=37, Spreadex=40, Betfred=45, QuinnBet=946).

## 3. Proof it is the same data as the page

Against fifteen runners of Punchestown 13:40 pasted off the live racecard,
the API's **best price** matched the page **exactly on 13/15**, and the page's
"logo" is simply the book holding the best price:

| Page (your paste) | Page logo | API best | Best book |
|---|---|---|---|
| Exceptionally 2.00 | u-icon | 2.10 | Unibet |
| The Good Wife 5.50 | pp | 5.50 | Paddy Power |
| Silver Kiss 6.50 | u-icon | 6.50 | Unibet |
| Wunderschon 8.50 | pp | 8.50 | William Hill |
| Angels Have Wings 15.00 | bet365 | 15.00 | bet365 |
| Bex Noir 251.00 | u-icon | 251.00 | Unibet |
| Soldier's Charm 201.00 | pp | 201.00 | QuinnBet |

`u-icon-black.webp` = **Unibet**. The two non-matches (Runningupthill 17->15,
Beach 51->41) had **moved** between your paste and my fetch - both were flagged
`shortening` by the API.


## 4. What we measure with it

`scripts/book_odds.py` snapshots every price into `PRODB.dbo.BookOdds`
(timestamped, so repeated runs build a price-history), then joins to the
Betfair SP already in `PRODB.dbo.BFSP`.

Measured on **all 38 UK/IRE races on 2026-09-15** (pre-race snapshot 11:31,
4,608 live-runner prices, median per-race overround = bookmaker margin):

| Book | margin | | Book | margin |
|---|---|---|---|---|
| bet365 | **+18.1%** | | Unibet | +22.2% |
| William Hill | +19.4% | | Betfred | +22.6% |
| Paddy Power | +20.3% | | BOYLE Sports | +22.8% |
| Coral / Ladbrokes | +20.3% | | Sky Bet | +23.5% |
| QuinnBet | +21.2% | | Betway | +23.7% |
| Spreadex | +21.6% | | **BEST-OF-MARKET** | **+15.5%** |

Two things fall out of that table:

1. **Shopping all 12 books only gets you to a 15.5% margin.** The gap between
   the best available bookmaker price and a fair/exchange book is ~15% - the
   same order as the -17.3% found for Ben's selections at bookmaker prices.
2. Individual books differ by ~5.5 points (18.1% -> 23.7%). If your accounts
   quote like-for-like, the book you hold is worth ~5% of ROI.

## 5. Usage

```
python scripts/rtv_api.py day  2026-09-15                    # card + prices?
python scripts/rtv_api.py race 2026-09-15 punchestown 1340   # clean runners
python scripts/rtv_api.py odds 2026-09-15 punchestown 1340   # per-book prices

python scripts/book_odds.py snapshot [date] [--limit N] [--purge]
python scripts/book_odds.py report   [date] [--book "Paddy Power"|BEST]
run_book_odds.bat                                            # both, then pause
run_book_odds.bat tomorrow                                   # next day's card

finish_bsp.bat 2026-09-15        # NEXT MORNING: wait for the SP file,
                                 # import it, rebuild the log, print report
python scripts/wait_for_bsp.py 2026-09-15 --check   # is the SP file out yet?
```

`report` prints, per book: median `price/BSP`, % of runners longer than BSP,
% longer by 5%+, and the median margin. `>1.00` means the book beats the SP.

## 6. Caveats / limits

* Prices appear **on the morning of the race** - the evening before is usually
  empty, so the "favourites are better priced the evening before" finding can
  only be re-tested on Betfair, or by polling repeatedly through the morning.
* **Betfair SP arrives the NEXT day.** The public file
  `dwbfprices{uk,ire}winDDMMYYYY.csv` named for a date contains the *previous*
  day's races, so a race date's SP cannot be imported the same evening - run
  `finish_bsp.bat <date>` the following morning (it waits for the file).
* Feed covers **12 books**: bet365, Sky Bet, Paddy Power, Ladbrokes, Coral,
  Betway, William Hill, Unibet, BOYLE Sports, Spreadex, Betfred, QuinnBet.
  No Betfair Sportsbook, 888, BetUK, Bwin, Virgin, Midnite, BetMGM, CopyBet.
* It is the **win market only**; `each_way_places` / `each_way_denominator` come
  with it, but there are no place-only prices.
* Always exclude `RunnerStatus <> 'entered'`, `IsReserve = 1` and scratched rows
  before computing margins - a non-runner's stale price inflates the book by
  ~15 points (that is exactly what produced a bogus "47% overround" on the
  first pass in `reports/_o10.log`).
* `fluctuation_type` is the move **since the market opened**, per book.
* This gives bookmaker prices only; **Betfair's price still comes from
  `dbo.BFSP`** (after racing) or the Betfair API (live).
## 7. You hold all 12 accounts - so nothing needs typing

Because every feed book is an account you hold, **the best price in the feed is
the price you can actually get**. `book_odds.py pricelog` writes
`price_log/price_log_<date>_auto.csv` with both sides filled:

| Column | Meaning |
|---|---|
| `BookPrice` | best price available across your 12 accounts |
| `BestBook` | which account that was - where to place the bet |
| `ShopEdge` | how much better that is than the *next-best* account |
| `NBooks` | how many accounts quoted (liquidity check) |
| `Move` | shortening / drifting since the market opened |
| `BetfairPrice` | Betfair SP from `dbo.BFSP`, filled once the race has run |
| `Ratio` | `BookPrice / BetfairPrice` - the number that decides everything |
| `Result` | `WinLose` from `dbo.BFSP` |

`scripts/price_log.py report` reads it as-is, so the existing `report.bat`
verdict works with zero manual entry.

### Where the best price actually sits (38 races, 4,608 runner-prices)

| Account | best price share | edge vs 2nd best |
|---|---|---|
| **bet365** | **37.2%** | **+6.8%** |
| Paddy Power | 29.4% | +1.6% |
| Unibet | 7.3% | +1.7% |
| William Hill | 6.5% | +1.9% |
| Coral | 6.0% | 0.0% |
| Spreadex | 4.2% | +0.1% |
| Sky Bet | 3.1% | +2.6% |
| the other five | 1.0-2.1% each | 0-4.7% |

So: **always price bet365 and Paddy Power first** - together they hold the best
price on two-thirds of runners, and bet365's price is worth 6.8% over the
next-best account on average. `shop` = run `report` and read the Share table.

### Timing

`snapshot` is timestamped, so run it repeatedly (morning, early afternoon, near
the off) and `report` will show how bookmaker prices moved - the only way to
test the "better priced the evening before" finding on bookmaker prices rather
than Betfair.



## 8. Overlay mode (`value`)

`python scripts/book_odds.py value` de-vigs every book per race (normalise that
book's implied probabilities to sum to 1), averages them into a consensus fair
probability, then flags runners whose **best available price is longer than the
consensus**:

```
value% = best_price x fair_prob - 1
```

First output (2026-09-15, 384 runners, >= 8 books):

```
any value (>0%)   29 of 384   (7.6%)
5%+               16 of 384   (4.2%)
10%+               6 of 384   (1.6%)
median value     -13.7%
```

**Read this carefully - it is probably not an edge.** Every overlay in the top
25 is a 67.0 to 301.0 outsider and 24 of the 25 are flagged `drifting`. That is
the longshot bias again (the same thing the Lay ex Leader test found: books
price the bottom of the market far too short, so de-vigging makes longshots look
undervalued when they are not). The median runner is -13.7%.

Treat `value` as a **candidate filter to validate**, not a system. Validation is
now possible: after the BSP backfill, compare the positive-value subset's
`BookPrice / BSP` against the rest of the card. If the overlays do not beat BSP
on average, the filter is dead - exactly the test every previous system failed.


## 9. Dashboard (`dashboard.bat`)

A Streamlit app over the same two tables - **read-only**, it never writes.

```
dashboard.bat           # asks whether to refresh prices first, then opens
                        # http://localhost:8501
```

| Tab | What it shows |
|---|---|
| **Live prices** | KPI tiles (races, runners, snapshots, tightest book, best-of-market margin), a margin bar chart per book with the shopping line, a "who has the best price" bar chart, and a **"where to bet right now"** table: runner, best price, which account to use, edge over next-best, move flag |
| **Movement** | best price per runner over time (line chart, log scale) and a horses x accounts price heatmap for any selected race - shows shortening/drifting visually |
| **Book vs Betfair** | best price vs real SP scatter (log-log) with the par line, the price/BSP histogram, and the key chart: **ROI by price-edge bucket** - backing every runner grouped by how far its price beat the SP. Plus a per-account table with each account's no-skill ROI |
| **Data health** | snapshots stored with age, coverage per account, warnings (stale prices, only one snapshot, SP missing, one account dominating) and the daily routine commands |

Verified: renders with no exceptions (`streamlit.testing.v1.AppTest`), all four
tabs, 4 charts, 3 tables, `/health` returns 200.

The dashboard reads *today's* prices, so on race morning it shows the live
market; the Book vs Betfair tab stays empty until the SP backfill has run after
racing.
