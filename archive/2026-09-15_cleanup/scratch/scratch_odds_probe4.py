import asyncio,re
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(user_agent=UA, viewport={"width":1500,"height":1000})
        pg=await c.new_page()
        await pg.goto("https://www.racingtv.com/racecards/2026-09-16/beverley/1408", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(12)
        html=await pg.content()
        print("html len", len(html))
        for kw in ("oddschecker","bookmaker","odds","price","fractional"):
            print(f"  {kw}: {html.lower().count(kw)}")
        m=re.findall(r"[^\"]*oddschecker[^\"]*", html, re.I)[:6]
        for x in m: print("   ODDS:", x[:160])
        # find iframes
        print("iframes:", [f.url for f in pg.frames])
        # look for any element with a data-* attr mentioning odds
        d=await pg.eval_on_selector_all("[data-testid],[class*=odd],[class*=Odd]", "els=>els.slice(0,12).map(e=>e.tagName+'.'+e.className+'=>'+(e.innerText||'').slice(0,40))")
        print("odds-ish els:", d)
        await b.close()
asyncio.run(main())
