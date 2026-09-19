import asyncio
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(user_agent=UA, viewport={"width":1400,"height":900})
        pg=await c.new_page()
        await pg.goto("https://www.racingtv.com/racecards/2026-09-12", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(7)
        hrefs=await pg.eval_on_selector_all("a","e=>e.map(x=>x.getAttribute(\'href\'))")
        u=sorted(set(h for h in hrefs if h))
        print("unique hrefs:", len(u))
        for h in u[:60]: print("   ", h)
        txt=await pg.inner_text("body")
        i=txt.find("TODAY")
        print("--- body after nav ---")
        print(txt[500:2200])
        await b.close()
asyncio.run(main())
