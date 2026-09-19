import asyncio
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(user_agent=UA, viewport={"width":1400,"height":900})
        pg=await c.new_page()
        u="https://www.racingtv.com/racecards/2026-09-12/doncaster/1310"
        r=await pg.goto(u, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(7)
        horses=await pg.eval_on_selector_all("a[href*=\'/profiles/horse/\']","e=>e.map(x=>x.innerText)")
        txt=await pg.inner_text("body")
        print("status", r.status, "horse links", len(horses), "textlen", len(txt))
        print("horses:", horses[:12])
        i=txt.find("DONCASTER")
        print("--- card text ---")
        print(txt[1400:3400])
        await b.close()
asyncio.run(main())
