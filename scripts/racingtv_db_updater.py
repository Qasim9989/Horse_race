"""
HIGH-SPEED CONCURRENT RACINGTV SCRAPER & DB UPDATER (PRODUCTION)
================================================================
Features:
- 5 Concurrent Browser Worker Tabs (Async Playwright).
- Event-driven dynamic DOM polling (zero dead sleep).
- Asset blocking (images, fonts, video, trackers blocked).
- Thread-safe batch SQL Server transactions.
- Automatic skip for already-scraped races.
- Full exception tolerance and clean resource disposal.
"""

import asyncio
import datetime
import re
import sys

import pyodbc

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

import contextlib

from playwright.async_api import async_playwright

CONN_STR = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=(localdb)\MSSQLLocalDB;"
    r"Database=RACINGTV_2023_2026;"
    r"Trusted_Connection=yes;"
    r"MultipleActiveResultSets=True;"
    r"Pooling=True;"
)

FORCE_REPAIR = False
MAX_CONCURRENT_WORKERS = 3

UK_IRE_COURSES = {
    "aintree", "ascot", "ayr", "ballinrobe", "bangor-on-dee", "bath",
    "bellewstown", "beverley", "brighton", "carlisle", "cartmel",
    "catterick", "catterick-bridge", "chelmsford-city", "chelmsford", "cheltenham",
    "chepstow", "chester", "clonmel", "cork", "curragh", "doncaster",
    "down-royal", "downpatrick", "dundalk", "epsom", "exeter",
    "fairyhouse", "fakenham", "ffos-las", "folkestone", "fontwell",
    "galway", "goodwood", "gowran-park", "hamilton", "haydock",
    "hereford", "hexham", "huntingdon", "kelso", "kempton",
    "kempton-park", "kilbeggan", "killarney", "laytown", "leicester",
    "leopardstown", "limerick", "lingfield", "lingfield-park", "listowel",
    "ludlow", "market-rasen", "musselburgh", "naas", "navan",
    "newbury", "newcastle", "newmarket", "newton-abbot", "nottingham",
    "perth", "plumpton", "pontefract", "punchestown", "redcar",
    "ripon", "roscommon", "salisbury", "sandown", "sedgefield",
    "sligo", "southwell", "stratford", "taunton", "thirsk",
    "thurles", "tipperary", "towcester", "tramore", "uttoxeter",
    "warwick", "wetherby", "wexford", "wincanton", "windsor",
    "wolverhampton", "worcester", "yarmouth", "york",
}

POSITION_RE = re.compile(
    r"^(?:[1-9]\d*(?:st|nd|rd|th)|"
    r"PU|F|U|UR|BD|RO|CO|SU|NR|DSQ|REF)$", re.IGNORECASE
)
ODDS_RE = re.compile(r"^(?:\d+/\d+[fF]?|Evens?[fF]?)$", re.IGNORECASE)
CLAIM_RE = re.compile(r"^\(\d+\s*lb\)$", re.IGNORECASE)
MARGIN_RE = re.compile(
    r"^(?:Short\s*Head|Short\s*Neck|Neck|Head|Nose|nse|hd|sh\s*hd|sn|nk|dist(?:ance)?|"
    r"\d+(?:\s+\d+/\d+)?\s*l?|Dead\s*Heat|dht?)$",
    re.IGNORECASE,
)
BARE_POSITION_RE = re.compile(r"^\d{1,2}$")
DRAW_RE = re.compile(r"^\(\d+\)$")
COMPARISON_METRICS = {"0-20MPH", "Stride Length", "FSP", "Top Speed"}
RANK_NUM_RE = re.compile(r"^\d{1,2}$")
RANK_SUFFIX_RE = re.compile(r"^(ST|ND|RD|TH)$", re.IGNORECASE)
COMPARISON_VALUE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(S|M|%|MPH)$", re.IGNORECASE)

def clean_lines(text):
    return [line.strip() for line in text.replace("\r", "").split("\n") if line.strip()]

def strip_country_suffix(name):
    return re.sub(r"\s*\([A-Z]{2,4}\)$", "", name).strip()

def is_position(value):
    return bool(POSITION_RE.fullmatch(value))

def find_position_indexes(section):
    indexes = [i for i, value in enumerate(section) if is_position(value)]
    if indexes:
        return indexes
    return [
        i for i, value in enumerate(section)
        if BARE_POSITION_RE.fullmatch(value)
        and i + 1 < len(section)
        and DRAW_RE.fullmatch(section[i + 1])
    ]

def looks_like_horse(value):
    if not value or len(value) > 150:
        return False
    val_upper = value.upper().strip()
    if val_upper in {
        "J:", "T:", "SP", "RESULT", "SECTIONALS", "CARD VIEW", "POS.",
        "NO./", "DRAW HORSE", "AGE", "WGT.", "OR", "FULL REPLAY",
        "CLOSING STAGES", "RACEIQ COMPARISON", "RACEIQ", "TRACKER",
        "SHORT HEAD", "HEAD", "NECK", "NOSE", "DEAD HEAT", "DISTANCE", "LENGTH"
    }:
        return False
    if ODDS_RE.fullmatch(value) or CLAIM_RE.fullmatch(value):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?(?:M|MPH|SPS|%)?", value, re.IGNORECASE):
        return False
    return bool(re.match(r"^[A-Z\u00C0-\u00D6\u00D8-\u00DE][A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u00FF0-9 .&\'()\-]+$", value))

def find_horse(block):
    for value in block:
        if value.startswith("(") and value.endswith(")"):
            continue
        if value.isdigit() and len(value) <= 2:
            continue
        if MARGIN_RE.fullmatch(value):
            continue
        if looks_like_horse(value):
            return value
    return None

def is_uk_ire_race(url):
    parts = url.rstrip("/").split("/")
    if len(parts) < 2:
        return False
    return parts[-2].lower() in UK_IRE_COURSES

def get_db_conn():
    return pyodbc.connect(CONN_STR, timeout=10)

def already_scraped(date_str, course, rtime):
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        r = cur.execute(
            "SELECT 1 FROM dbo.Scraped_Results WHERE RaceDate = ? AND CourseName = ? AND RaceTime = ?",
            (date_str, course, rtime),
        ).fetchone()
        conn.close()
        return r is not None
    except Exception:
        return False

def save_race_transaction(date_str, course, rtime, result_rows, raceiq_rows, raceiq_rank_rows):
    if not result_rows:
        return
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM dbo.Scraped_Results WHERE RaceDate = ? AND CourseName = ? AND RaceTime = ?", (date_str, course, rtime))
        cur.execute("DELETE FROM dbo.Scraped_RaceIQ WHERE RaceDate = ? AND CourseName = ? AND RaceTime = ?", (date_str, course, rtime))
        cur.execute("DELETE FROM dbo.Scraped_RaceIQ_Ranks WHERE RaceDate = ? AND CourseName = ? AND RaceTime = ?", (date_str, course, rtime))

        if result_rows:
            cur.executemany(
                "INSERT INTO dbo.Scraped_Results (RaceDate, RaceTime, CourseName, RaceTitle, HorseName, PosNo, JockeyClaim, SP, Comment, JockeyName, TrainerName, Age, Weight, OfficialRating) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                result_rows
            )
        if raceiq_rows:
            cur.executemany(
                "INSERT INTO dbo.Scraped_RaceIQ (RaceDate, RaceTime, CourseName, HorseName, StrideLength, AvgFrequency, TopSpeed, FinishingSpeedPct) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                raceiq_rows
            )
        if raceiq_rank_rows:
            cur.executemany(
                "INSERT INTO dbo.Scraped_RaceIQ_Ranks (RaceDate, RaceTime, CourseName, HorseName, Metric, RankPosition, RankSuffix, Value, ValueUnit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                raceiq_rank_rows
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"      [DB error {course} {rtime}]: {e}")
    finally:
        conn.close()

async def click_tab_async(page, tab_name):
    try:
        return await page.evaluate("""name => {
            const elements = [...document.querySelectorAll('button,[role="button"],div,a')];
            const el = elements.find(x => x.textContent && x.textContent.trim().toUpperCase() === name.toUpperCase());
            if (!el) return false;
            el.click();
            return true;
        }""", tab_name)
    except Exception:
        return False

async def parse_results_async(page, date_str, course, rtime):
    with contextlib.suppress(BaseException):
        await page.wait_for_selector("text=POS.", timeout=8000)

    text = await page.inner_text("body")
    lines = clean_lines(text)
    start = next((i for i, x in enumerate(lines) if x.lower() == "card view"), None)
    if start is None:
        start = next((i for i, x in enumerate(lines) if x.upper() == "POS."), None)
    if start is None:
        start = 0

    end_markers = {"winning time", "tote:", "1st owner", "comparison tutorial", "by metric", "raceiq comparison"}
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip().lower() in end_markers:
            end = i
            break

    section = lines[start:end]
    position_indexes = find_position_indexes(section)
    race_title = ""
    for i, value in enumerate(lines[:start]):
        if re.match(r"^\d{2}:\d{2}\s+", value) and i + 1 < len(lines):
            race_title = lines[i + 1]
            break

    rows = []
    for n, position_index in enumerate(position_indexes):
        block_end = position_indexes[n + 1] if n + 1 < len(position_indexes) else len(section)
        block = section[position_index + 1:block_end]
        horse = find_horse(block)
        if not horse:
            continue

        claim = 0.0
        for value in block:
            match = re.search(r"(\d+)", value) if CLAIM_RE.fullmatch(value) else None
            if match:
                claim = float(match.group(1))
                break

        sp = next((value for value in block if ODDS_RE.fullmatch(value)), None)
        comment = next(
            (value for value in block
             if len(value) > 20 and not looks_like_horse(value)
             and not value.startswith("J:") and not value.startswith("T:")),
            None,
        )
        # Jockey and trainer ARE on the page as "J: <name>" / "T: <name>" lines;
        # they were previously only excluded from the comment, never captured.
        jockey = next((value[2:].strip() for value in block
                       if value.startswith("J:") and len(value) > 2), "")
        trainer = next((value[2:].strip() for value in block
                        if value.startswith("T:") and len(value) > 2), "")
        # Age / Weight / Official Rating are also on the page (card-view
        # columns) but were never captured.  Weight is unambiguous (X-Y);
        # age is the small bare number, OR is the 30+ bare number.
        weight = next((v for v in block if re.fullmatch(r"\d{1,2}-\d{1,2}", v)), "")
        nums = [v for v in block if re.fullmatch(r"\d{1,3}", v)]
        age = next((v for v in nums if int(v) < 30), "")
        rating = next((v for v in nums if int(v) >= 30), "")

        rows.append((
            date_str, rtime, course, race_title, horse,
            section[position_index], claim, sp, comment, jockey, trainer,
            age, weight, rating,
        ))
    return rows

async def parse_raceiq_async(page, date_str, course, rtime):
    if not await click_tab_async(page, "SECTIONALS"):
        return []

    for _ in range(20):
        text = await page.inner_text("body")
        if "MPH" in text or "Avg Frequency" in text or "SECTIONALS" in text:
            break
        await asyncio.sleep(0.1)

    lines = clean_lines(text)
    start = next((i for i, x in enumerate(lines) if x.upper() == "SECTIONALS"), None)
    if start is None:
        return []

    section = lines[start + 1:]
    positions = find_position_indexes(section)
    rows = []

    for n, position_index in enumerate(positions):
        block_end = positions[n + 1] if n + 1 < len(positions) else len(section)
        block = section[position_index + 1:block_end]
        horse = find_horse(block)
        if not horse:
            continue

        joined = " ".join(block)
        stride_match = re.search(r"(\d+(?:\.\d+)?)\s*M(?:\s|$)", joined, re.IGNORECASE)
        speed_match = re.search(r"(\d+(?:\.\d+)?)\s*MPH", joined, re.IGNORECASE)
        finish_match = re.search(r"(\d+(?:\.\d+)?)\s*%", joined)
        frequency_match = re.search(r"Avg\s+Frequency.*?(\d+(?:\.\d+)?)\s*SPS", joined, re.IGNORECASE)

        stride = float(stride_match.group(1)) if stride_match else None
        speed = float(speed_match.group(1)) if speed_match else None
        finish = float(finish_match.group(1)) if finish_match else None
        frequency = float(frequency_match.group(1)) if frequency_match else None

        if any(value is not None for value in (stride, frequency, speed, finish)):
            rows.append((
                date_str, rtime, course, horse,
                stride, frequency, speed, finish,
            ))
    return rows

async def parse_raceiq_comparison_async(page, date_str, course, rtime):
    if not await click_tab_async(page, "RACEiQ COMPARISON"):
        return []

    for _ in range(20):
        text = await page.inner_text("body")
        if "by horse" in text.lower() or "METRIC" in text:
            break
        await asyncio.sleep(0.1)

    lines = clean_lines(text)
    start = next((i for i, x in enumerate(lines) if x.lower() == "by horse"), None)
    if start is None:
        return []

    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().lower() == "sectionals tutorial"), len(lines))
    section = lines[start + 1:end]

    rows = []
    i = 0
    while i < len(section):
        if section[i] != "METRIC" or i == 0:
            i += 1
            continue

        horse = section[i - 1]
        i += 3
        while i < len(section) and section[i] != "Median":
            label = section[i]
            if label not in COMPARISON_METRICS:
                i += 1
                continue

            if (
                i + 3 < len(section)
                and RANK_NUM_RE.fullmatch(section[i + 1])
                and RANK_SUFFIX_RE.fullmatch(section[i + 2])
            ):
                value_match = COMPARISON_VALUE_RE.fullmatch(section[i + 3].replace(" ", ""))
                if value_match:
                    rows.append((
                        date_str, rtime, course, horse, label,
                        int(section[i + 1]), section[i + 2].upper(),
                        float(value_match.group(1)), value_match.group(2).upper(),
                    ))
                i += 4
            else:
                i += 1

        if i < len(section) and section[i] == "Median":
            i += 1

    return rows

async def block_resources_async(route):
    if route.request.resource_type in ["image", "media", "font"] or any(d in route.request.url for d in ["googletagmanager", "google-analytics", "doubleclick", "scorecardresearch", "facebook"]):
        await route.abort()
    else:
        await route.continue_()

async def safe_goto(page, url, wait_until="domcontentloaded", timeout=20000, max_retries=2):
    for attempt in range(max_retries):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            return True
        except Exception:
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(1.0)
    return False

async def process_single_race(sem, context, link, date_str):
    parts = link.rstrip("/").split("/")
    course, rtime = parts[-2], parts[-1]

    if not FORCE_REPAIR and already_scraped(date_str, course, rtime):
        print(f"   â© Already in DB: {course} ({rtime})", flush=True)
        return

    async with sem:
        page = await context.new_page()
        await page.route("**/*", block_resources_async)
        try:
            ok = await safe_goto(page, link, wait_until="domcontentloaded", timeout=18000)
            if not ok:
                print(f"   âš  Timeout loading {course} ({rtime})", flush=True)
                return

            await asyncio.sleep(0.3)

            result_rows = await parse_results_async(page, date_str, course, rtime)
            raceiq_rows = await parse_raceiq_async(page, date_str, course, rtime)
            raceiq_rank_rows = await parse_raceiq_comparison_async(page, date_str, course, rtime)

            name_lookup = {strip_country_suffix(row[4]): row[4] for row in result_rows}
            raceiq_rows = [
                (row[0], row[1], row[2], name_lookup.get(row[3], row[3]), *tuple(row[4:]))
                for row in raceiq_rows
            ]
            raceiq_rank_rows = [
                (row[0], row[1], row[2], name_lookup.get(row[3], row[3]), *tuple(row[4:]))
                for row in raceiq_rank_rows
            ]

            if result_rows:
                save_race_transaction(date_str, course, rtime, result_rows, raceiq_rows, raceiq_rank_rows)
                print(f"   âœ“ {course} ({rtime}): {len(result_rows)} runners | {len(raceiq_rows)} RaceIQ | {len(raceiq_rank_rows)} Ranks", flush=True)

        except Exception as e:
            print(f"   âš  Error processing {course} ({rtime}): {e}", flush=True)
        finally:
            with contextlib.suppress(BaseException):
                await page.close()

async def process_date(browser, date_obj, sem):
    date_str = date_obj.strftime("%Y-%m-%d")
    print(f"\n[{date_str}] Fetching race list...")

    page = await browser.new_page()
    race_links = []
    try:
        await page.route("**/*", block_resources_async)
        ok = await safe_goto(page, f"https://www.racingtv.com/results/{date_str}", timeout=25000)
        if ok:
            with contextlib.suppress(BaseException):
                await page.locator("#onetrust-accept-btn-handler").click(timeout=1500)

            await asyncio.sleep(1.5)
            raw_links = await page.eval_on_selector_all("a[href*='/results/']", "els => els.map(e => e.href)")
            race_links = [l for l in set(raw_links) if is_uk_ire_race(l) and re.search(r"/\d{4}$", l.rstrip("/"))]
    except Exception as e:
        print(f"  âš  Error fetching date page {date_str}: {e}")
    finally:
        with contextlib.suppress(BaseException):
            await page.close()

    if not race_links:
        print(f"[{date_str}] 0 UK/Irish races found.")
        return

    print(f"[{date_str}] {len(race_links)} UK/Irish races found. Processing with {MAX_CONCURRENT_WORKERS} workers...")

    context = await browser.new_context()
    try:
        tasks = [process_single_race(sem, context, link, date_str) for link in race_links]
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        with contextlib.suppress(BaseException):
            await context.close()

def get_latest_db_date():
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        r = cur.execute("SELECT MAX(RaceDate) FROM dbo.Scraped_Results").fetchone()
        conn.close()
        if r and r[0]:
            return r[0]
    except Exception:
        pass
    return None

async def main():
    print("=" * 75)
    print(f"  HIGH-SPEED RACINGTV SCRAPER & DB UPDATER ({MAX_CONCURRENT_WORKERS} Parallel Workers)")
    print("=" * 75)

    today = datetime.date.today()
    latest_in_db = get_latest_db_date()

    global FORCE_REPAIR
    if "--force" in sys.argv:
        FORCE_REPAIR = True
        print("FORCE_REPAIR: re-scraping already-scraped races to backfill jockey/trainer/age/weight/OR.\n")

    if len(sys.argv) > 1:
        try:
            start_date = datetime.datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            print("Error: Invalid start date format. Use YYYY-MM-DD")
            sys.exit(1)
    else:
        start_date = (latest_in_db - datetime.timedelta(days=1)) if latest_in_db else datetime.date(2023, 1, 1)

    if len(sys.argv) > 2:
        try:
            end_date = datetime.datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
        except ValueError:
            print("Error: Invalid end date format. Use YYYY-MM-DD")
            sys.exit(1)
    else:
        end_date = today

    total_days = (end_date - start_date).days + 1
    print(f"Date Range: {start_date} to {end_date} ({total_days} days to check/update)\n")

    sem = asyncio.Semaphore(MAX_CONCURRENT_WORKERS)
    BROWSER_REFRESH_INTERVAL = 50

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        current = start_date
        day_counter = 0

        while current <= end_date:
            if day_counter > 0 and day_counter % BROWSER_REFRESH_INTERVAL == 0:
                with contextlib.suppress(BaseException):
                    await browser.close()
                browser = await p.chromium.launch(headless=True)

            await process_date(browser, current, sem)
            current += datetime.timedelta(days=1)
            day_counter += 1

        with contextlib.suppress(BaseException):
            await browser.close()

    print("\n=== ALL DONE! Scrape completed successfully! ===")

if __name__ == "__main__":
    asyncio.run(main())
