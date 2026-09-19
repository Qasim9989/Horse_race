import asyncio,re
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(user_agent=UA, viewport={"width":1500,"height":1000})
        pg=await c.new_page()
        await pg.goto("https://www.racingtv.com/racecards/2026-09-16/beverley/1408", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(10)
        txt=await pg.inner_text("body")
        i=txt.find("Al Za")
        print("idx of first horse:", i)
        print(txt[max(0,i-700):i+900])
        # any element whose text looks like a price
        hit=await pg.eval_on_selector_all("*", "els => els.filter(e=>e.children.length===0 && /^\\d+\\/\\d+$/.test((e.innerText||'').trim())).slice(0,15).map(e=>e.innerText.trim()+' @ '+e.className)")
        print("price elements:", hit)
        await b.close()
asyncio.run(main())
