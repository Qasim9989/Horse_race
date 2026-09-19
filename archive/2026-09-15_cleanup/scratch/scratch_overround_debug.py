"""Debug the overround: print the raw per-book price matrix for one race and
show duplicates / stale entries per bookmaker.

Writes reports/_o11.log
"""
import json
import urllib.request
from collections import Counter

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "authorization": "",
    "x-requested-with": "racingtv-web/5.6.0",
    "referer": "https://www.racingtv.com/",
    "accept": "application/json",
    "content-type": "application/json",
}
BASE = "https://api.racingtv.com"


def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


race = get("/racing/racecards/2026-09-15/punchestown/1340")
runners = race["runners"]
ids = [r["id"] for r in runners]
odds = get("/racing/runner/odds?" + "&".join("runner_ids[]=%d" % i for i in ids))
books = {int(k): v["name"] for k, v in odds["bookmakers"].items()}

L = []


def say(s=""):
    L.append(str(s))


name_of = {r["id"]: r["horse_name"] for r in runners}
status_of = {r["id"]: (r["status"]["state"], r["withdrawn"],
                       r.get("reserve")) for r in runners}
say(f"RACE: {race['race']['title']} | best_book field: "
    f"{race['race'].get('best_book')}")
say("racecard id order vs odds-response id order:")
say("  card : " + str(ids))
say("  odds : " + str([r["id"] for r in odds["runners"]]))
say()

# duplicates per runner
for r in odds["runners"]:
    ol = r.get("odds") or []
    c = Counter(o["bookmaker_id"] for o in ol)
    dupes = {k: v for k, v in c.items() if v > 1}
    if dupes:
        say("DUPLICATE BOOKS on runner %s (%s): %s"
            % (r["id"], name_of.get(r["id"]), dupes))

say()
say("RAW MATRIX (rows = runners in card order, cols = bookmaker price)")
bids = sorted(books)
say("%-22s %-24s %s" % ("HORSE", "STATE", " ".join("%7s" % books[b][:7] for b in bids)))
matrix = {}
for rid in ids:
    entry = next((x for x in odds["runners"] if x["id"] == rid), {})
    ol = {o["bookmaker_id"]: o for o in (entry.get("odds") or [])}
    row = []
    for b in bids:
        o = ol.get(b)
        row.append(float(o["price"]["decimal"]) if o else None)
    matrix[rid] = row
    st = status_of[rid]
    say("%-22s %-24s %s" % (
        str(name_of[rid])[:22], f"{st[0]}/wd={st[1]}/res={st[2]}"[:24],
        " ".join("%7s" % ("-" if v is None else f"{v:g}") for v in row)))

say()
say("OVERROUND per book  (all rows)  vs  (live runners only: entered & not withdrawn & not reserve)")
live = [rid for rid in ids
        if status_of[rid][0] == "entered" and not status_of[rid][1]
        and not status_of[rid][2]]
say("  live runners (%d): %s" % (len(live), [name_of[r] for r in live]))
say()
say("  %-16s %10s %10s" % ("BOOK", "ALL", "LIVE"))
for i, b in enumerate(bids):
    vals_all = [matrix[rid][i] for rid in ids if matrix[rid][i]]
    vals_live = [matrix[rid][i] for rid in live if matrix[rid][i]]
    if not vals_all:
        continue
    say("  %-16s %10.4f %10.4f" % (
        books[b], sum(1 / v for v in vals_all), sum(1 / v for v in vals_live)))
best_all = [max(v for v in matrix[rid] if v) if any(matrix[rid]) else None for rid in ids]
best_live = [max(v for v in matrix[rid] if v) for rid in live if any(matrix[rid])]
say("  %-16s %10.4f %10.4f" % ("BEST-OF-MARKET",
                               sum(1 / v for v in best_all if v),
                               sum(1 / v for v in best_live)))

txt = "\n".join(L)
with open(r"E:\Test\racing-form-system\reports\_o11.log", "w", encoding="utf-8") as f:
    f.write(txt)
print(txt.encode("ascii", "replace").decode("ascii"))
