"""Probe 7 — (a) capture request headers for the odds API, (b) test in-page
fetch() so we can poll odds without scraping the DOM, (c) find the day-list
endpoint behind /racecards/{date}.

Writes reports/_o7.log + reports/_api2/*.json
"""
import asyncio
import json
import os
import re

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
RACE = "https://www.racingtv.com/racecards/2026-09-15/punchestown/1340"
INDEX = "https://www.racingtv.com/racecards/2026-09-16"
OUT = r"E:\Test\racing-form-system\reports\_api2"
os.makedirs(OUT, exist_ok=True)

ODDS_IDS_TODAY = [8807876, 8807877, 8807878, 8807879, 8807880, 8807881, 8807882]
ODDS_IDS_TOMORROW = [8801973, 8801974, 8801976, 8801980, 8801984, 8801986, 8801988]


def odds_url(ids):
    return ("https://api.racingtv.com/racing/runner/odds?"
            + "&".join(f"runner_ids[]={i}" for i in ids))


async def main():
    log = []
    hdrs_seen = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        c = await b.new_context(user_agent=UA, viewport={"width": 1500, "height": 1000})
        pg = await c.new_page()

        def on_req(req):
            u = req.url
            if "api.racingtv.com/racing" in u:
                hdrs_seen.append({"url": u[:200], "headers": dict(req.headers),
                                  "method": req.method})

        pg.on("request", on_req)
        await pg.goto(RACE, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(12)

        log.append("### API REQUEST HEADERS SEEN ###")
        for h in hdrs_seen[:6]:
            log.append(f"-- {h['method']} {h['url']}")
            for k, v in h["headers"].items():
                log.append(f"     {k}: {v}")

        # ---- in-page fetch on TODAY's race
        for label, ids in (("TODAY", ODDS_IDS_TODAY), ("TOMORROW", ODDS_IDS_TOMORROW)):
            u = odds_url(ids)
            res = await pg.evaluate(
                """async (u) => {
                    try {
                      const r = await fetch(u, {headers: {'Accept': 'application/json'}});
                      const t = await r.text();
                      return {status: r.status, len: t.length, body: t};
                    } catch (e) { return {status: 'ERR', body: String(e)}; }
                }""", u)
            fn = os.path.join(OUT, f"fetch_{label}.json")
            with open(fn, "w", encoding="utf-8") as f:
                f.write(res.get("body", ""))
            log.append(f"### IN-PAGE FETCH {label} -> status={res['status']} "
                       f"len={res.get('len')} file={fn}")
            try:
                j = json.loads(res["body"])
                log.append("   top keys: " + ", ".join(list(j.keys())))
                for r in (j.get("runners") or [])[:6]:
                    log.append("   runner: " + json.dumps(r)[:520])
            except Exception as e:                             # noqa: BLE001
                log.append(f"   not JSON ({e}): {res.get('body','')[:300]}")

        # ---- day index page: which endpoints does it use?
        idx_calls = []

        def on_req2(req):
            if "api.racingtv.com" in req.url and "media" not in req.url:
                idx_calls.append(req.url)

        pg.on("request", on_req2)
        await pg.goto(INDEX, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(10)
        log.append("")
        log.append("### INDEX PAGE API CALLS (dedup) ###")
        for u in sorted(set(idx_calls)):
            log.append("   " + u[:230])
        await b.close()

    txt = "\n".join(log)
    with open(r"E:\Test\racing-form-system\reports\_o7.log", "w",
              encoding="utf-8") as f:
        f.write(txt)
    print(txt.encode("ascii", "replace").decode("ascii")[:9000])


asyncio.run(main())
