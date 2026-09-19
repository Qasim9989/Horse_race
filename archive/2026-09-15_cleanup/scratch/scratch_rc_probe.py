import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        pg = await b.new_page()
        try:
            r = await pg.goto("https://www.racingtv.com/racecards/2026-09-14", wait_until="domcontentloaded", timeout=30000)
            print("status:", r.status if r else None)
            print("title:", await pg.title())
            txt = await pg.inner_text("body")
            print("text len:", len(txt))
            print("first 300:", txt[:300].replace(chr(10), " | "))
            links = await pg.eval_on_selector_all("a[href*='/racecards/']", "els => els.map(e => e.href)")
            print("racecard links:", len(links))
            for l in list(set(links))[:10]:
                print("   ", l)
        except Exception as e:
            print("ERR", type(e).__name__, str(e)[:200])
        await b.close()

asyncio.run(main())
