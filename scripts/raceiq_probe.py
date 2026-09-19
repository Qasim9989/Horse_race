r"""
RACEIQ PROBE - dump what the SECTIONALS page actually says
==========================================================
The stored RaceIQ values disagree with each other (Top Speed median 29.82 in
Scraped_RaceIQ vs 38.31 in the ranks table), AvgFrequency is NULL on all 83,025
rows, and TopSpeed has rows as low as 1.74 MPH.  Those are parser faults, and
they cannot be fixed by guessing at regexes - this dumps the real page text for
ONE race so the parser can be written against it.

    python scripts\raceiq_probe.py --url https://www.racingtv.com/results/2026-09-13/chelmsford-city/1630
    python scripts\raceiq_probe.py --date 2026-09-13          # takes the first race found

Writes reports/_raceiq_probe.txt and prints the numbers it can see.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path("reports/_raceiq_probe.txt")
TABS = ("SECTIONALS", "RACEiQ COMPARISON")


async def block(route) -> None:
    if route.request.resource_type in ("image", "media", "font"):
        await route.abort()
    else:
        await route.continue_()


async def click(page, name: str) -> bool:
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


async def first_race(page, date_str: str) -> str | None:
    url = f"https://www.racingtv.com/results/{date_str}"
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        print(f"goto failed: {exc}")
        return None
    print(f"status {getattr(resp, 'status', '?')}   title {await page.title()!r}")
    with contextlib.suppress(BaseException):
        await page.locator("#onetrust-accept-btn-handler").click(timeout=2500)
    await asyncio.sleep(5.0)
    every = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    print(f"total links on page: {len(every)}   (waiting 5s, consent accepted)")
    for x in sorted(set(every))[:25]:
        print("   ", x)
    links = await page.eval_on_selector_all(
        "a[href*='/results/']", "els => els.map(e => e.href)")
    raw = sorted(set(links))
    print(f"links containing '/results/': {len(raw)}")
    for x in raw[:12]:
        print("   ", x)
    good = [x for x in raw if re.search(r"/\d{4}/?$", x)]
    print(f"links ending in a 4-digit time: {len(good)}")
    if not good and raw:
        body = (await page.inner_text("body"))[:600]
        print("page text starts:", body.replace("\n", " ")[:400])
    return good[0] if good else None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--date", default="")
    a = ap.parse_args()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", block)

        url = a.url
        if not url:
            url = await first_race(page, a.date or "2026-09-13") or ""
        if not url:
            print("no race url - pass --url")
            await browser.close()
            return
        print("probing:", url)

        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        with contextlib.suppress(BaseException):
            await page.locator("#onetrust-accept-btn-handler").click(timeout=2000)
        await asyncio.sleep(1.5)

        chunks: list[str] = [f"URL: {url}"]
        for tab in TABS:
            ok = await click(page, tab)
            await asyncio.sleep(2.0)
            text = await page.inner_text("body")
            chunks.append(f"\n{'=' * 78}\nTAB {tab}  (clicked={ok})\n{'=' * 78}\n{text}")
            print(f"--- {tab}: {len(text)} chars")
            for pat, label in ((r"[\d.]+\s*M\b", "stride-like 'M'"),
                               (r"[\d.]+\s*MPH", "MPH"),
                               (r"[\d.]+\s*SPS", "SPS"),
                               (r"[\d.]+\s*%", "percent"),
                               (r"Avg\s*Freq\w*", "avg frequency label")):
                found = re.findall(pat, text)
                print(f"    {label:<22} {len(found):>5} hits  e.g. {found[:6]}")
            out = "\n".join(chunks)
            OUT.write_text(out, encoding="utf-8")
        print(f"\nraw text -> {OUT}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
