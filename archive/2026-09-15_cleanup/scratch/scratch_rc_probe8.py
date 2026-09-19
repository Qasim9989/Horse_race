import asyncio
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
async def one(pg, d):
    await pg.goto(f"https://www.racingtv.com/racecards/{d}", wait_until="domcontentloaded", timeout=40000)
    await asyncio.sleep(7)
    links=await pg.eval_on_selector_all(f"a[href*=\'/racecards/{d}/\']","e=>e.map(x=>x.getAttribute(\'href\'))")
    txt=await pg.inner_text("body")
    print(f"{d}: race_links={len(set(links))}  textlen={len(txt)}")
    for l in sorted(set(links))[:15]: print("    ", l)
    i=txt.find("AT-A-GLANCE")
    if i>0: print("  glance:", txt[i:i+300].replace(chr(10)," | "))
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(user_agent=UA, viewport={"width":1400,"height":900})
        pg=await c.new_page()
        await one(pg, "2026-09-16")
        await one(pg, "2026-09-17")
        await b.close()
asyncio.run(main())
