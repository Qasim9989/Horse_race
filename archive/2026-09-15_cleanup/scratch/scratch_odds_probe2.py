import asyncio,re
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(user_agent=UA, viewport={"width":1400,"height":900})
        pg=await c.new_page()
        for u in ("https://www.racingtv.com/racecards/2026-09-16/beverley/1408",
                  "https://www.racingtv.com/racecards/2026-09-16"):
            await pg.goto(u, wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(8)
            txt=await pg.inner_text("body")
            odds=re.findall(r"\b\d+/\d+f?\b", txt)
            print("==",u)
            print("  textlen",len(txt),"odds-like tokens",len(odds),odds[:14])
            print("  tail:", txt[-1400:].replace(chr(10)," | ")[:1400])
        await b.close()
asyncio.run(main())
