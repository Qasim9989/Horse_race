"""Probe 6 — capture every api.racingtv.com JSON response, then call the odds
endpoint directly with plain urllib (no browser) to test if it is open.

Writes: reports/_api/*.json (captured bodies), reports/_o6.json, reports/_o6.log
"""
import asyncio
import json
import os
import re
import urllib.request

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
URL = "https://www.racingtv.com/racecards/2026-09-15/punchestown/1340"
OUTDIR = r"E:\Test\racing-form-system\reports\_api"
os.makedirs(OUTDIR, exist_ok=True)


def safe_name(u):
    return re.sub(r"[^A-Za-z0-9]+", "_", u.split("api.racingtv.com/")[-1])[:110]


async def capture():
    captured = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        c = await b.new_context(user_agent=UA, viewport={"width": 1500, "height": 1000})
        pg = await c.new_page()

        async def on_resp(resp):
            u = resp.url
            if "api.racingtv.com" not in u:
                return
            try:
                body = await resp.text()
            except Exception as e:                             # noqa: BLE001
                body = f"<unreadable: {e}>"
            fn = os.path.join(OUTDIR, safe_name(u) + ".json")
            with open(fn, "w", encoding="utf-8") as f:
                f.write(body)
            captured.append({"status": resp.status, "url": u, "file": fn,
                             "len": len(body), "head": body[:400]})

        pg.on("response", on_resp)
        await pg.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(14)
        # also grab the rendered odds text per horse (whole-block text)
        dom = await pg.evaluate(r"""
        () => {
          const LOGO = 'img[src*="logo"], img[src*="icon"], img[src*="bookmaker"]';
          const out = [];
          document.querySelectorAll('a[href*="/profiles/horse/"]').forEach(a => {
            let n = a, block = null, d = 0;
            while (n && d < 15) {
              if (n.querySelector && n.querySelector(LOGO)) { block = n; break; }
              n = n.parentElement; d++;
            }
            const rec = {horse: (a.innerText || '').split('\n')[0].trim(),
                         prices: [], logo: null};
            if (block) {
              const img = block.querySelector(LOGO);
              if (img) rec.logo = (img.getAttribute('src') || '').split('/').pop();
              const txt = block.innerText || '';
              rec.prices = (txt.match(/\b\d{1,3}\.\d{1,2}\b/g) || []).slice(0, 6);
              rec.blockhead = txt.replace(/\n/g, ' | ').slice(0, 160);
            }
            out.push(rec);
          });
          return out;
        }""")
        await b.close()
    return captured, dom


def direct_call(urls):
    results = []
    for u in urls:
        for hdr_name, hdrs in (
            ("plain-UA", {"User-Agent": UA}),
            ("no-headers-at-all", {}),
            ("browser-ish", {"User-Agent": UA,
                             "Referer": URL,
                             "Origin": "https://www.racingtv.com",
                             "Accept": "application/json, text/plain, */*"})):
            try:
                req = urllib.request.Request(u, headers=hdrs)
                with urllib.request.urlopen(req, timeout=25) as r:
                    body = r.read().decode("utf-8", "ignore")
                    results.append({"header_mode": hdr_name, "url": u,
                                    "status": r.status, "len": len(body),
                                    "body": body[:2500]})
            except Exception as e:                             # noqa: BLE001
                results.append({"header_mode": hdr_name, "url": u,
                                "status": f"ERR {e}", "len": 0, "body": ""})
    return results


async def main():
    captured, dom = await capture()
    odds_urls = [c["url"] for c in captured if "/odds" in c["url"]]
    all_urls = sorted({c["url"] for c in captured})
    direct = direct_call(odds_urls[:2]) if odds_urls else []

    payload = {"captured": captured, "dom": dom, "all_api_urls": all_urls,
               "direct": direct}
    with open(r"E:\Test\racing-form-system\reports\_o6.json", "w",
              encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)

    lines = ["### ALL api.racingtv.com URLS ###"]
    for u in all_urls:
        lines.append("  " + u[:220])
    lines.append("")
    lines.append("### CAPTURED BODY HEADS ###")
    for c in captured:
        lines.append(f"-- {c['status']} len={c['len']} {c['url'][:150]}")
        lines.append("   " + c["head"].replace("\n", " ")[:380])
    lines.append("")
    lines.append("### DOM per-runner (prices regex on block text) ###")
    for d in dom[:20]:
        lines.append(f"  {d['horse'][:24]:24s} | {d['logo']} | prices={d['prices']}")
        lines.append(f"      {d.get('blockhead','')[:150]}")
    lines.append("")
    lines.append("### DIRECT (no browser) CALLS ###")
    for r in direct:
        lines.append(f"-- {r['header_mode']} status={r['status']} len={r['len']}")
        lines.append("   " + r["body"].replace("\n", " ")[:900])
    out = "\n".join(lines)
    with open(r"E:\Test\racing-form-system\reports\_o6.log", "w",
              encoding="utf-8") as f:
        f.write(out)
    print(out.encode("ascii", "replace").decode("ascii"))


asyncio.run(main())
