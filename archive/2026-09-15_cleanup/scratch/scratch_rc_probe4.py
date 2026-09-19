import asyncio
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(user_agent=UA, viewport={"width":1400,"height":900})
        pg=await c.new_page()
        await pg.goto("https://www.racingtv.com/racecards/2026-09-14", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(6)
        links=await pg.eval_on_selector_all("a[href*=\'/racecards/\']","e=>e.map(x=>x.href)")
        for l in sorted(set(links))[:25]: print("  ", l)
        print("total:", len(set(links)))
        await b.close()
asyncio.run(main())
