"""Probe 5 — find the RacingTV/Oddschecker odds source.

Goals
  1. Dump the network requests the racecard makes (look for a JSON odds API).
  2. Extract per-runner best-odds cell (price + bookmaker logo) from the DOM.
  3. Compare with the raw (no-JS) HTML to see whether odds are server-rendered.
Writes reports/_o5.json + reports/_o5_dump.html (utf-8) and prints a summary.
"""
import asyncio
import json
import re
import urllib.request

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

URLS = [
    "https://www.racingtv.com/racecards/2026-09-15/punchestown/1340",
    "https://www.racingtv.com/racecards/2026-09-16/beverley/1408",
]

JS_EXTRACT = r"""
() => {
  const LOGO_SEL = 'img[src*="logo"], img[src*="icon"], img[src*="bookmaker"]';
  const out = [];
  document.querySelectorAll('a[href*="/profiles/horse/"]').forEach(a => {
    let n = a, anchor = null, d = 0;
    while (n && d < 15) {
      if (n.querySelector && n.querySelector(LOGO_SEL)) { anchor = n; break; }
      n = n.parentElement; d++;
    }
    const rec = {horse: (a.innerText || '').split('\n')[0].trim(),
                 price: null, logo: null, frac: null};
    if (anchor) {
      const img = anchor.querySelector(LOGO_SEL);
      if (img) rec.logo = (img.getAttribute('src') || '').split('/').pop();
      let q = img, dd = 0;
      while (q && dd < 8) {
        const t = (q.innerText || '').trim();
        if (/^\d+(\.\d+)?$/.test(t)) { rec.price = t; break; }
        if (/^\d+\/\d+f?$/.test(t)) { rec.frac = t; }
        q = q.parentElement; dd++;
      }
    }
    out.push(rec);
  });
  return out;
}
"""


async def probe(page, url, out):
    reqs = []

    async def on_resp(resp):
        u = resp.url
        if any(k in u for k in ("/api/", "graphql", ".json", "odds", "price")):
            reqs.append([resp.status, u])

    page.on("response", on_resp)
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(11)
    odds = await page.evaluate(JS_EXTRACT)
    html = await page.content()
    body = await page.inner_text("body")
    page.remove_listener("response", on_resp)
    return {"url": url, "odds": odds, "requests": reqs,
            "html_len": len(html), "body_len": len(body)}


def raw_html_check(url):
    """Does odds text exist without running any JavaScript?"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "ignore")
    except Exception as e:                                    # noqa: BLE001
        return {"error": str(e)}
    oddschecker = len(re.findall("oddschecker", html, re.I))
    # decimal prices 1.01-999 with a decimal point, anywhere in markup
    prices = re.findall(r">(\d{1,3}\.\d{1,2})<", html)
    return {"status": "ok", "len": len(html), "oddschecker_mentions": oddschecker,
            "decimal_price_literals": len(prices), "sample": prices[:12]}


async def main():
    results = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        c = await b.new_context(user_agent=UA, viewport={"width": 1500, "height": 1000})
        pg = await c.new_page()
        for i, u in enumerate(URLS):
            try:
                res = await probe(pg, u, i)
                res["raw_html"] = raw_html_check(u)
                results.append(res)
            except Exception as e:                            # noqa: BLE001
                results.append({"url": u, "error": str(e)})
        await b.close()

    with open(r"E:\Test\racing-form-system\reports\_o5.json", "w",
              encoding="utf-8") as f:
        json.dump(results, f, indent=1)

    lines = []
    for r in results:
        lines.append("=" * 70)
        lines.append(f"URL {r['url']}")
        if r.get("error"):
            lines.append(f"  ERROR {r['error']}")
            continue
        lines.append(f"  html_len {r['html_len']}  body_len {r['body_len']}")
        lines.append(f"  raw(no-JS): {r['raw_html']}")
        lines.append(f"  runners parsed: {len(r['odds'])}")
        withprice = [o for o in r["odds"] if o["price"]]
        lines.append(f"  runners WITH a price: {len(withprice)}")
        for o in r["odds"][:18]:
            lines.append(f"    {o['horse'][:26]:26s} | {str(o['price']):8s} | "
                         f"{str(o['logo'])[:28]}")
        lines.append("  NETWORK:")
        seen = set()
        for st, u in r["requests"]:
            if u in seen:
                continue
            seen.add(u)
            lines.append(f"    {st} {u[:190]}")
    out = "\n".join(lines)
    with open(r"E:\Test\racing-form-system\reports\_o5.log", "w",
              encoding="utf-8") as f:
        f.write(out)
    print(out.encode("ascii", "replace").decode("ascii"))


asyncio.run(main())
