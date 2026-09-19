import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36", viewport={"width":1400,"height":900})
        pg = await ctx.new_page()
        await pg.goto("https://www.racingtv.com/racecards/2026-09-14", wait_until="domcontentloaded", timeout=40000)
        for t in (3, 8, 15):
            await asyncio.sleep(t if t==3 else 5)
            txt = await pg.inner_text("body")
            links = await pg.eval_on_selector_all("a[href*='/racecards/']", "els => els.length")
            print(f"after ~{t+ (0 if t==3 else 5)}s: textlen={len(txt)} racecardlinks={links}")
        # look for consent buttons / iframes
        btns = await pg.eval_on_selector_all("button", "els => els.map(e => e.id + '|' + (e.innerText||'').slice(0,30))")
        print("buttons:", btns[:12])
        frames = [f.url for f in pg.frames]
        print("frames:", frames[:8])
        await b.close()

asyncio.run(main())
