r"""
RACEIQ SCRAPER v2 - fixed keys, real parser, staging-first
==========================================================
Replaces the RaceIQ half of scripts\racingtv_db_updater.py.  What was wrong and
what this does instead:

  old                                    new
  -------------------------------------  ------------------------------------
  reads links immediately after           waits for the client-rendered list
  domcontentloaded -> 0 races found       (consent click + poll until links)
  regex "first number before M/MPH"       label+unit parser (raceiq_parse)
  "0-20MPH" stored as speed 20            unit decides the metric
  AvgFrequency regex for a label that     derived: speed(m/s) / stride(m)
  does not exist -> NULL on every row
  no range checks -> 1.74 mph stored      ranges enforced, rejects counted
  keys = url slug + "1405"                 canonical venue + HH:MM
  68% duplicate rows, delete+insert        unique key + upsert, links deduped

Writes to STAGING tables by default; --target live is opt-in.

    python scripts\raceiq_scrape_v2.py --date 2026-09-13 --dry-run
    python scripts\raceiq_scrape_v2.py --date 2026-09-13
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import re

import pyodbc
from playwright.async_api import async_playwright
from raceiq_parse import HorseRaceIQ, parse_by_horse
from racingtv_db_updater import UK_IRE_COURSES

LIVE = (r"Driver={ODBC Driver 17 for SQL Server};"
        r"Server=(localdb)\MSSQLLocalDB;Database=RACINGTV_2023_2026;"
        r"Trusted_Connection=yes;Connection Timeout=300;"
        r"MultipleActiveResultSets=True;")
TARGETS = {"staging": ("Scraped_RaceIQ_v2", "Scraped_RaceIQ_Ranks_v2"),
           "live": ("Scraped_RaceIQ", "Scraped_RaceIQ_Ranks")}
VENTURE_FIXES = {"kempton-park": "Kempton", "chelmsford-city": "Chelmsford City",
                 "lingfield-park": "Lingfield", "ffos-las": "Ffos Las",
                 "newton-abbot": "Newton Abbot", "market-rasen": "Market Rasen",
                 "down-royal": "Down Royal", "gowran-park": "Gowran Park",
                 "catterick-bridge": "Catterick"}
DDL = """
IF OBJECT_ID('dbo.{iq}') IS NULL
CREATE TABLE dbo.{iq} (
    RaceDate date NOT NULL, RaceTime varchar(5) NOT NULL,
    Venue varchar(60) NOT NULL, Horse varchar(120) NOT NULL,
    StrideM float NULL, StrideFt float NULL, TopSpeedMph float NULL,
    FspPct float NULL, Accel0To20S float NULL, AvgFrequencySps float NULL,
    CreatedDate datetime NOT NULL DEFAULT GETDATE(),
    CONSTRAINT UX_{iq} UNIQUE (RaceDate, RaceTime, Venue, Horse));
IF OBJECT_ID('dbo.{rk}') IS NULL
CREATE TABLE dbo.{rk} (
    RaceDate date NOT NULL, RaceTime varchar(5) NOT NULL,
    Venue varchar(60) NOT NULL, Horse varchar(120) NOT NULL,
    Metric varchar(24) NOT NULL, RankPosition int NULL,
    Value float NULL, ValueUnit varchar(6) NULL,
    CONSTRAINT UX_{rk} UNIQUE (RaceDate, RaceTime, Venue, Horse, Metric));
"""


def venue_of(slug: str) -> str:
    return VENTURE_FIXES.get(slug, slug.replace("-", " ").title())


def time_of(seg: str) -> str:
    digits = re.sub(r"\D", "", seg).zfill(4)
    return f"{digits[:2]}:{digits[2:]}"


async def block_resources(route) -> None:
    if route.request.resource_type in ("image", "media", "font"):
        await route.abort()
    else:
        await route.continue_()


async def click_tab(page, name: str) -> bool:
    try:
        return bool(await page.evaluate("""n => {
            const els = [...document.querySelectorAll('button,[role="button"],div,a')];
            const el = els.find(x => x.textContent && x.textContent.trim().toUpperCase() === n.toUpperCase());
            if (!el) return false;
            el.click();
            return true;
        }""", name))
    except Exception:
        return False


async def race_links(page, date_str: str) -> list[tuple[str, str, str]]:
    """(url, canonical venue, HH:MM) for every UK/Irish race that day."""
    out: list[tuple[str, str, str]] = []
    await page.goto(f"https://www.racingtv.com/results/{date_str}",
                    wait_until="domcontentloaded", timeout=45000)
    with contextlib.suppress(BaseException):
        await page.locator("#onetrust-accept-btn-handler").click(timeout=2500)
    for _ in range(30):                     # the list is client-rendered
        links = await page.eval_on_selector_all(
            "a[href*='/results/']", "els => els.map(e => e.href)")
        good = [x for x in sorted(set(links))
                if re.search(r"/\d{4}/?$", x)
                and x.rstrip("/").split("/")[-2] in UK_IRE_COURSES]
        if good:
            break
        await asyncio.sleep(0.5)
    seen: set[tuple[str, str]] = set()
    for url in good:
        parts = url.rstrip("/").split("/")
        slug, seg = parts[-2], parts[-1]
        key = (venue_of(slug), time_of(seg))
        if key in seen:                     # same race under two url spellings
            continue
        seen.add(key)
        out.append((url, key[0], key[1]))
    return out


def ensure_tables(cur, target: str) -> None:
    iq, rk = TARGETS[target]
    cur.execute(DDL.format(iq=iq, rk=rk))
    cur.commit()


UNIT_OF = {"accel_0_20_s": "S", "stride_m": "M", "fsp_pct": "%",
           "top_speed_mph": "MPH"}


def upsert(cur, target: str, date_str: str, venue: str, hhmm: str,
           horses: list[HorseRaceIQ]) -> int:
    """One transaction per race: delete that race's rows, insert the fresh ones."""
    iq, rk = TARGETS[target]
    cur.execute(f"DELETE FROM dbo.{iq} WHERE RaceDate=? AND RaceTime=? "
                "AND Venue=?", (date_str, hhmm, venue))
    cur.execute(f"DELETE FROM dbo.{rk} WHERE RaceDate=? AND RaceTime=? "
                "AND Venue=?", (date_str, hhmm, venue))
    for h in horses:
        cur.execute(
            f"INSERT INTO dbo.{iq} (RaceDate,RaceTime,Venue,Horse,StrideM,"
            "StrideFt,TopSpeedMph,FspPct,Accel0To20S,AvgFrequencySps) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (date_str, hhmm, venue, h.horse, h.values.get("stride_m"),
             h.stride_ft, h.values.get("top_speed_mph"), h.values.get("fsp_pct"),
             h.values.get("accel_0_20_s"), h.avg_frequency_sps))
        for field_name, rank in h.ranks.items():
            cur.execute(
                f"INSERT INTO dbo.{rk} (RaceDate,RaceTime,Venue,Horse,Metric,"
                "RankPosition,Value,ValueUnit) VALUES (?,?,?,?,?,?,?,?)",
                (date_str, hhmm, venue, h.horse, field_name, rank,
                 h.values.get(field_name), UNIT_OF[field_name]))
    cur.commit()
    return len(horses)


async def scrape_race(sem, context, url: str, venue: str, hhmm: str,
                      date_str: str) -> tuple[str, str, list[HorseRaceIQ]]:
    async with sem:
        page = await context.new_page()
        await page.route("**/*", block_resources)
        text = ""
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            with contextlib.suppress(BaseException):
                await page.locator("#onetrust-accept-btn-handler").click(
                    timeout=2000)
            await asyncio.sleep(1.0)
            # best effort: the RaceIQ comparison block is already in the DOM on
            # the results page, so a failed click must not lose the race
            await click_tab(page, "SECTIONALS")
            await click_tab(page, "RACEiQ COMPARISON")
            for _ in range(15):
                text = await page.inner_text("body")
                if "METRIC" in text and "Median" in text:
                    break
                await asyncio.sleep(0.4)
            return venue, hhmm, parse_by_horse(text)
        except Exception as exc:
            print(f"  ! {venue} {hhmm}: {exc}")
            return venue, hhmm, []
        finally:
            with contextlib.suppress(BaseException):
                await page.close()


async def run(a) -> None:
    sem = asyncio.Semaphore(a.workers)
    conn = None
    cur = None
    if not a.dry_run:
        conn = pyodbc.connect(LIVE)
        cur = conn.cursor()
        ensure_tables(cur, a.target)
    races = horses_n = written = rejects = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", block_resources)
        for date_str in a.date:
            links = await race_links(page, date_str)
            print(f"[{date_str}] {len(links)} unique UK/IRE races")
            ctx = await browser.new_context()
            tasks = [scrape_race(sem, ctx, u, v, t, date_str)
                     for u, v, t in links]
            for venue, hhmm, hs in await asyncio.gather(*tasks):
                if not hs:
                    continue
                races += 1
                horses_n += len(hs)
                bad = sum(len(h.rejected) for h in hs)
                rejects += bad
                if cur is not None:
                    written += upsert(cur, a.target, date_str, venue, hhmm, hs)
                if a.dry_run:
                    h0 = hs[0]
                    print(f"  {venue} {hhmm}: {len(hs)} horses, e.g. "
                          f"{h0.horse} stride {h0.values.get('stride_m')} m, "
                          f"speed {h0.values.get('top_speed_mph')} mph, "
                          f"fsp {h0.values.get('fsp_pct')} %, "
                          f"sps {h0.avg_frequency_sps}, rejects {bad}")
            await ctx.close()
        await page.close()
        await browser.close()
    if conn is not None:
        conn.close()
    print(f"\nraces {races}  horses {horses_n}  written {written}  "
          f"rejected values {rejects}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", action="append", required=True,
                    help="YYYY-MM-DD, repeatable")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--target", choices=sorted(TARGETS), default="staging")
    ap.add_argument("--workers", type=int, default=3)
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()

