import asyncio
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
async def check(pg, url, sel):
    try:
        r=await pg.goto(url, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(6)
        n=await pg.eval_on_selector_all(sel,"e=>e.length")
        txt=await pg.inner_text("body")
        print(f"{url}  status={r.status}  sel_count={n}  textlen={len(txt)}")
        print("   sample:", txt[200:500].replace(chr(10)," | "))
    except Exception as e:
        print(url, "ERR", str(e)[:120])
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(user_agent=UA, viewport={"width":1400,"height":900})
        pg=await c.new_page()
        await check(pg, "https://www.racingtv.com/racecards/2026-09-12", "a[href*=\'/racecards/2026-09-12/\']")
        await check(pg, "https://www.racingtv.com/results/2026-08-19", "a[href*=\'/results/2026-08-19/\']")
        await b.close()
asyncio.run(main())
