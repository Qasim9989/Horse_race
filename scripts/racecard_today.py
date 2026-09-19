"""
TODAY'S RACECARD SCRAPER
========================
Fetches today's UK/IRE racecards from RacingTV and stores the declared runners
in SCRAPED_PRODB.dbo.Scraped_Racecards.  Reuses the proven selectors from the
old daily_lay_scanner.py.

Usage:  python scripts/racecard_today.py [YYYY-MM-DD]
"""
import asyncio
import contextlib
import datetime
import re
import sys

import pyodbc
from playwright.async_api import async_playwright

CONN = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=SCRAPED_PRODB;"
        r"Trusted_Connection=yes;MultipleActiveResultSets=True;Pooling=True;")

UK_IRE_COURSES = {
    "aintree", "ascot", "ayr", "ballinrobe", "bangor-on-dee", "bath", "beverley",
    "brighton", "carlisle", "cartmel", "catterick", "chelmsford", "chepstow",
    "chester", "cheltenham", "clonmel", "cork", "curragh", "doncaster",
    "downpatrick", "down-royal", "dundalk", "epsom", "exeter", "fairyhouse",
    "fakenham", "ffos-las", "fontwell", "galway", "goodwood", "gowran-park",
    "hamilton", "haydock", "hereford", "hexham", "huntingdon", "kilbeggan",
    "killarney", "laytown", "leicester", "leopardstown", "limerick", "lingfield",
    "listowel", "market-rasen", "musselburgh", "naas", "navan", "newbury",
    "newcastle", "newmarket", "newton-abbot", "nottingham", "perth", "plumpton",
    "pontefract", "punchestown", "redcar", "roscommon", "salisbury", "sandown",
    "sedgefield", "sligo", "southwell", "stratford", "taunton", "thirsk",
    "thurles", "tipperary", "towcester", "tramore", "uttoxeter", "warwick",
    "wetherby", "wexford", "wincanton", "windsor", "wolverhampton", "worcester",
    "yarmouth", "york",
}

WEIGHT_RE = re.compile(r"^\d{1,2}-\d{1,2}$")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
VIEWPORT = {"width": 1400, "height": 900}


DDL = """
IF OBJECT_ID('dbo.Scraped_Racecards') IS NULL
CREATE TABLE dbo.Scraped_Racecards (
    ID INT IDENTITY(1,1) PRIMARY KEY,
    RaceDate DATE NOT NULL, RaceTime VARCHAR(8), CourseName VARCHAR(60),
    RaceTitle VARCHAR(200), HorseName VARCHAR(80), JockeyName VARCHAR(80),
    TrainerName VARCHAR(80), Age VARCHAR(8), Weight VARCHAR(12),
    OfficialRating VARCHAR(8), CreatedDate DATETIME DEFAULT GETDATE()
);
"""


async def block_resources_async(route):
    if route.request.resource_type in ("image", "media", "font") or any(d in route.request.url for d in
             ("googletagmanager", "google-analytics", "doubleclick", "facebook")):
        await route.abort()
    else:
        await route.continue_()


def clean_horse(name):
    return re.sub(r"\s*\([A-Z]{2,4}\)$", "", str(name or "").strip()).strip()


def parse_block(lines, key):
    """Best-effort jockey/trainer/age/weight/OR from the lines near a horse."""
    idx = next((i for i, ln in enumerate(lines)
                if "".join(c for c in ln.lower() if c.isalnum()).startswith(key)),
               None)
    if idx is None:
        return "", "", "", "", ""
    blk = lines[idx:idx + 8]
    weight = next((v for v in blk if WEIGHT_RE.fullmatch(v)), "")
    nums = [v for v in blk if re.fullmatch(r"\d{1,3}", v)]
    age = next((v for v in nums if int(v) < 30), "")
    rating = next((v for v in nums if 30 <= int(v) <= 200), "")
    jock = next((v[2:].strip() for v in blk
                 if v.upper().startswith("J:") and len(v) > 3), "")
    trn = next((v[2:].strip() for v in blk
                if v.upper().startswith("T:") and len(v) > 3), "")
    return jock, trn, age, weight, rating


async def fetch_card(sem, context, url, course, rtime, date_str):
    async with sem:
        page = await context.new_page()
        try:
            await page.route("**/*", block_resources_async)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except Exception as e:
                print(f"  ! timeout {course} {rtime}: {e}")
                return []
            with contextlib.suppress(Exception):
                await page.wait_for_selector(
                    "a[href*='/profiles/horse/'], a[href*='/horse/'], h1",
                    timeout=6000)
            await asyncio.sleep(0.4)

            title_el = page.locator("h1, h2, .race-title, [class*='raceTitle']").first
            title = await title_el.inner_text() if await title_el.count() > 0 else ""

            horses = await page.eval_on_selector_all(
                "a[href*='/profiles/horse/'], a[href*='/horse/'], "
                ".horse-name, [class*='horseName']",
                "els => els.map(e => e.innerText)")
            text = await page.inner_text("body")
            lines = [ln.strip() for ln in text.replace("\r", "").split("\n")
                     if ln.strip()]

            tfmt = f"{rtime[:2]}:{rtime[2:]}"
            rows, seen = [], set()
            for h in horses:
                if not h:
                    continue
                raw = clean_horse(h.split("\n")[0])
                key = "".join(c for c in raw.lower() if c.isalnum())
                if not key or key in seen or len(key) <= 2:
                    continue
                seen.add(key)
                jock, trn, age, weight, rating = parse_block(lines, key)
                rows.append((date_str, tfmt, course.replace("-", " ").title(),
                             title.strip(), raw, jock, trn, age, weight, rating))
            print(f"  {course.title()} {tfmt}: {len(rows)} runners"
                  f"  ({title[:46]})")
            return rows
        except Exception as e:
            print(f"  ! card error {course} {rtime}: {e}")
            return []
        finally:
            with contextlib.suppress(Exception):
                await page.close()


async def run(date_str):
    race_links = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        page = await ctx.new_page()
        await page.route("**/*", block_resources_async)
        try:
            await page.goto(f"https://www.racingtv.com/racecards/{date_str}",
                            wait_until="domcontentloaded", timeout=40000)
            for _ in range(3):
                await asyncio.sleep(2.5)
                for label in ("Accept", "I Accept", "Accept All"):
                    try:
                        await page.get_by_role("button", name=label,
                                               exact=False).first.click(timeout=1200)
                        break
                    except Exception:
                        pass
                with contextlib.suppress(Exception):
                    await page.locator("#onetrust-accept-btn-handler").click(timeout=1200)
                if await page.eval_on_selector_all(
                        f"a[href*='/racecards/{date_str}/']", "els => els.length"):
                    break
            await asyncio.sleep(2)
            links = await page.eval_on_selector_all(
                f"a[href*='/racecards/{date_str}/']",
                "els => els.map(e => e.href)")
            print(f"  raw links: {len(set(links))}")
            for l in set(links):
                m = re.search(rf"/racecards/{date_str}/([^/]+)/(\d{{4}})", l)
                if m and m.group(1).lower() in UK_IRE_COURSES:
                    race_links.append((l, m.group(1).lower(), m.group(2)))
        except Exception as e:
            print(f" ! racecard list error: {e}")
        print(f"Found {len(set(race_links))} UK/IRE races for {date_str}.")
        if not race_links:
            await browser.close()
            return []
        sem = asyncio.Semaphore(5)
        context = await browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        tasks = [fetch_card(sem, context, u, c, t, date_str)
                 for u, c, t in race_links]
        res = await asyncio.gather(*tasks, return_exceptions=True)
        await context.close()
        await browser.close()
    lists = [r for r in res if isinstance(r, list)]
    return [x for sub in lists for x in sub]


def save(rows, date_str):
    conn = pyodbc.connect(CONN, autocommit=True)
    cur = conn.cursor()
    cur.execute(DDL)
    cur.execute("DELETE FROM dbo.Scraped_Racecards WHERE RaceDate = ?", (date_str,))
    if rows:
        cur.fast_executemany = True
        cur.executemany(
            "INSERT INTO dbo.Scraped_Racecards (RaceDate,RaceTime,CourseName,"
            "RaceTitle,HorseName,JockeyName,TrainerName,Age,Weight,"
            "OfficialRating) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.close()
    print(f"Saved {len(rows)} runner rows for {date_str}.")


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    print(f"=== RACECARD SCRAPE {date_str} ===")
    rows = asyncio.run(run(date_str))
    save(rows, date_str)


if __name__ == "__main__":
    main()

