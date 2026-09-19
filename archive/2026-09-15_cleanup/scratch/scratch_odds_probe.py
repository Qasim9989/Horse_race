import asyncio
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(user_agent=UA, viewport={"width":1400,"height":900})
        pg=await c.new_page()
        u="https://www.racingtv.com/racecards/2026-09-16/beverley/1408"
        r=await pg.goto(u, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(8)
        txt=await pg.inner_text("body")
        i=txt.find("Card View")
        if i<0: i=txt.find("POS.")
        print("status",r.status,"textlen",len(txt))
        print(txt[i:i+2200] if i>0 else txt[:2200])
        await b.close()
asyncio.run(main())
