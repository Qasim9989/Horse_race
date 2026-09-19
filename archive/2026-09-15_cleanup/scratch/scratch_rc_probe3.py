import asyncio
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

async def blk(route):
    if route.request.resource_type in ("image","media","font"):
        await route.abort()
    elif any(d in route.request.url for d in ("googletagmanager","google-analytics","doubleclick","facebook")):
        await route.abort()
    else:
        await route.continue_()

async def t(block):
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(user_agent=UA, viewport={"width":1400,"height":900})
        pg=await c.new_page()
        if block: await pg.route("**/*", blk)
        await pg.goto("https://www.racingtv.com/racecards/2026-09-14", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(6)
        n=await pg.eval_on_selector_all("a[href*=\'/racecards/2026-09-14/\']","e=>e.length")
        txt=await pg.inner_text("body")
        print(f"block={block}: links={n} textlen={len(txt)}")
        await b.close()

async def main():
    await t(True)
    await t(False)
asyncio.run(main())
