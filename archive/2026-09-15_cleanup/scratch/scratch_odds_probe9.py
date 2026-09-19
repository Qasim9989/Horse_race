"""Probe 9 — plain-Python RacingTV API: structure of racecard JSON + odds JSON.

Answers:
  * does /racing/racecards/{date}/{course}/{time} already carry best odds?
  * what does /racing/runner/odds return (and does it take >7 ids)?
  * which fields solve the Age/Weight/OR parse problem (from the DB scrape)?
  * are odds published for a race 1-2 days out?

Writes reports/_o9.log (+ reports/_api4/*.json)
"""
import json
import os
import urllib.request

OUT = r"E:\Test\racing-form-system\reports\_api4"
os.makedirs(OUT, exist_ok=True)
BASE = "https://api.racingtv.com"
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


def get(path, tag):
    url = BASE + path
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "ignore")
            status = r.status
    except Exception as e:                                     # noqa: BLE001
        return {"tag": tag, "url": url, "status": f"ERR {e}", "json": None}
    with open(os.path.join(OUT, tag + ".json"), "w", encoding="utf-8") as f:
        f.write(body)
    try:
        return {"tag": tag, "url": url, "status": status, "json": json.loads(body)}
    except Exception:                                          # noqa: BLE001
        return {"tag": tag, "url": url, "status": status, "json": None,
                "raw": body[:500]}


def odds_url(ids):
    q = "&".join("runner_ids[]=" + str(i) for i in ids)
    return "/racing/runner/odds?" + q


def main():
    L = []

    def say(s=""):
        L.append(str(s))

    # 1. racecard detail, today
    r = get("/racing/racecards/2026-09-15/punchestown/1340", "race_pun_1340")
    j = r["json"]
    say(f"== racecard status={r['status']}")
    if j:
        say("  top keys: " + ", ".join(j.keys()))
        race = j.get("race", {})
        say("  race keys: " + ", ".join(list(race.keys())))
        say("  displays_odds = " + repr(race.get("displays_odds")))
        say("  odds/bookmaker-ish race fields: " + json.dumps(
            {k: v for k, v in race.items()
             if any(t in k for t in ("odds", "price", "book", "market"))})[:600])
        runners = j.get("runners", [])
        say(f"  runners: {len(runners)}")
        if runners:
            say("  runner[0] keys: " + ", ".join(list(runners[0].keys())))
            say("  runner[0] odds-ish: " + json.dumps(
                {k: v for k, v in runners[0].items()
                 if any(t in k.lower() for t in ("odd", "price", "book",
                                                 "short", "drift", "move",
                                                 "sp", "trend"))})[:900])
            say("  runner[0] full (trimmed):")
            say("    " + json.dumps(runners[0])[:2600].replace("\\n", " "))

        say("")
        say("  -- per-runner summary (no / name / age / wgt / OR / odds) --")
        ids = []
        for x in runners:
            rr = x.get("runner", x)
            ids.append(rr.get("id"))
            say("   {no!s:>3} {nm:26s} age={ag!s:>3} wgt={wg!s:>6} OR={orv!s:>5}"
                "  odds={od!s:>8} book={bk!s}".format(
                    no=rr.get("number") or rr.get("draw"),
                    nm=str(rr.get("name"))[:26],
                    ag=rr.get("age"), wg=rr.get("weight") or rr.get("weight_lbs"),
                    orv=rr.get("official_rating") or rr.get("rating"),
                    od=(x.get("odds") or {}).get("price")
                    if isinstance(x.get("odds"), dict) else x.get("odds"),
                    bk=(x.get("odds") or {}).get("bookmaker")
                    if isinstance(x.get("odds"), dict) else x.get("bookmaker")))
        say("  runner ids: " + str(ids))

        # 2. odds endpoint with a big batch (test the 7-id cap)
        if ids:
            o = get(odds_url(ids), "odds_batch_all")
            oj = o["json"]
            say("")
            say(f"== odds endpoint ({len(ids)} ids) status={o['status']}")
            if oj:
                say("  keys: " + ", ".join(oj.keys()))
                say("  body trimmed: " + json.dumps(oj)[:3000])

    # 3. is a race further out priced?  (list a day ahead, then read it)
    lst = get("/racing/racecards/list/2026-09-16", "list_0916")
    if lst["json"]:
        for m in lst["json"].get("meetings", [])[:3]:
            for rc in m.get("races", [])[:1]:
                slug = rc.get("slug", "")
                t = (rc.get("start_time_scheduled") or "T00:00")[11:16]
                course = (m.get("track", {}).get("slug") or "").strip("/")
                path = f"/racing/racecards/{m['date']}/{course}/{t.replace(':','')}"
                d = get(path, f"race_{course}_{t.replace(':','')}")
                dj = d["json"]
                say("")
                say(f"== tomorrow card {path} status={d['status']}")
                if dj:
                    say("  displays_odds=" + repr(dj.get("race", {}).get("displays_odds")))
                    for x in dj.get("runners", [])[:5]:
                        say("    " + json.dumps(x)[:420])

    txt = "\n".join(L)
    with open(r"E:\Test\racing-form-system\reports\_o9.log", "w",
              encoding="utf-8") as f:
        f.write(txt)
    print(txt.encode("ascii", "replace").decode("ascii")[:20000])


main()
