"""Probe 8 — retest the RacingTV JSON API from plain Python with the exact
headers the SPA sends (no browser).  If this returns 200 we have a fast poller.

Endpoints under test
  /racing/racecards/list/{date}                 -> whole day, all races
  /racing/racecards/{date}/{course}/{time}      -> one race + runner ids
  /racing/runner/odds?runner_ids[]=...          -> best odds per runner

Writes reports/_api3/*.json and reports/_o8.log
"""
import json
import os
import urllib.request

OUT = r"E:\Test\racing-form-system\reports\_api3"
os.makedirs(OUT, exist_ok=True)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "authorization": "",
    "x-requested-with": "racingtv-web/5.6.0",
    "referer": "https://www.racingtv.com/",
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://www.racingtv.com",
}

TESTS = [
    ("list_today", "https://api.racingtv.com/racing/racecards/list/2026-09-15"),
    ("list_tomorrow", "https://api.racingtv.com/racing/racecards/list/2026-09-16"),
    ("race_today", "https://api.racingtv.com/racing/racecards/"
                   "2026-09-15/punchestown/1340"),
    ("race_tomorrow", "https://api.racingtv.com/racing/racecards/"
                      "2026-09-16/beverley/1408"),
    ("odds_today", "https://api.racingtv.com/racing/runner/odds?"
                   "&".join(f"runner_ids[]={i}" for i in
                            (8807876, 8807877, 8807878, 8807879, 8807880))),
    ("odds_tomorrow", "https://api.racingtv.com/racing/runner/odds?"
                      "&".join(f"runner_ids[]={i}" for i in
                               (8801973, 8801974, 8801976, 8801980, 8801984))),
]


def call(name, url, headers):
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "ignore")
            status = r.status
    except Exception as e:                                     # noqa: BLE001
        return {"name": name, "url": url, "status": f"ERR {e}", "body": ""}
    fn = os.path.join(OUT, name + ".json")
    with open(fn, "w", encoding="utf-8") as f:
        f.write(body)
    return {"name": name, "url": url, "status": status, "len": len(body),
            "body": body, "file": fn}


def main():
    lines = []
    for name, url in TESTS:
        r = call(name, url, HEADERS)
        lines.append(f"-- {name}: status={r['status']} len={r.get('len', 0)}"
                     f" file={r.get('file','')}")
        if r.get("body"):
            try:
                j = json.loads(r["body"])
                lines.append("   JSON keys: " + ", ".join(list(j.keys())[:14]))
                lines.append("   head: " + json.dumps(j)[:700])
            except Exception:                                  # noqa: BLE001
                lines.append("   NON-JSON: " + r["body"][:400])
    txt = "\n".join(lines)
    with open(r"E:\Test\racing-form-system\reports\_o8.log", "w",
              encoding="utf-8") as f:
        f.write(txt)
    print(txt.encode("ascii", "replace").decode("ascii")[:8000])


main()
