"""Validate the RacingTV odds API against the racecard HTML the user pasted.

Compares, for Punchestown 13:40 (2026-09-15):
   * best market price we compute from the API  vs  the price shown on the page
   * which bookmaker holds the best price        vs  the logo on the page
   * each bookmaker's overround for the race (implied-probability sum)

Writes reports/_o10.log
"""
import json
import urllib.request

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

# ---- prices exactly as they appear in the pasted racecard HTML ---------------
PASTE = {
    "Exceptionally": "2.00", "The Good Wife": "5.50", "Silver Kiss": "6.50",
    "Wunderschon": "8.50", "Angels Have Wings": "15.00",
    "Runningupthill": "17.00", "Whytemontella": "17.00", "Lusted": "41.00",
    "Beach": "51.00", "Cross Of Stars": "67.00", "Eagles Voice": "126.00",
    "Ever My Treasure": "151.00", "Soldier's Charm": "201.00",
    "Mother's Call": "201.00", "Bex Noir": "251.00", "Harita": "NR",
}
LOGO_TO_BOOK = {"bet365-logo.webp": "bet365", "pp-logo.webp": "Paddy Power",
                "u-icon-black.webp": "?"}
PASTE_BOOK = {  # from the logo in each price cell of the paste
    "Exceptionally": "u-icon-black", "The Good Wife": "pp", "Silver Kiss": "u-icon",
    "Wunderschon": "pp", "Angels Have Wings": "bet365", "Runningupthill": "pp",
    "Whytemontella": "pp", "Lusted": "pp", "Beach": "pp", "Cross Of Stars": "pp",
    "Eagles Voice": "pp", "Ever My Treasure": "pp", "Soldier's Charm": "pp",
    "Mother's Call": "pp", "Bex Noir": "u-icon", "Harita": "default",
}


def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def main():
    L = []

    def say(s=""):
        L.append(str(s))

    race = get("/racing/racecards/2026-09-15/punchestown/1340")
    runners = race["runners"]
    ids = [r["id"] for r in runners]
    q = "/racing/runner/odds?" + "&".join("runner_ids[]=%d" % i for i in ids)
    odds = get(q)

    books = {int(k): v["name"] for k, v in odds.get("bookmakers", {}).items()}
    say("BOOKMAKERS IN FEED (%d):" % len(books))
    for bid in sorted(books):
        say("   %4d  %s" % (bid, books[bid]))
    say()

    by_id = {r["id"]: r for r in odds["runners"]}
    name_of = {r["id"]: r["horse_name"] for r in runners}

    say("PER-RUNNER BEST PRICE  (API)  vs  PAGE (your paste)")
    say("%-20s %8s %-14s %5s %7s  %8s %-9s %s" %
        ("HORSE", "API_BEST", "API_BOOK", "BOOKS", "SPREAD", "PAGE", "PAGE_BK", "MOVE"))

    per_book = {}   # bookmaker id -> list of decimal prices for the race
    for rid in ids:
        entry = by_id.get(rid) or {}
        ol = entry.get("odds") or []
        if not ol:
            say("%-20s %8s" % (name_of.get(rid, rid)[:20], "-- no odds --"))
            continue
        decs = [(float(o["price"]["decimal"]), o["bookmaker_id"],
                 o.get("fluctuation_type")) for o in ol]
        decs.sort(reverse=True)
        best, bbid, _ = decs[0]
        spread = decs[0][0] - decs[-1][0]
        nm = name_of.get(rid, str(rid))[:20]
        page = PASTE.get(nm, "-")
        say("%-20s %8.2f %-14s %5d %7.2f  %8s %-9s %s" %
            (nm, best, books.get(bbid, bbid)[:14], len(decs), spread,
             page, PASTE_BOOK.get(nm, ""), decs[0][2]))
        for dec, bid, _fl in decs:
            per_book.setdefault(bid, []).append(dec)

    say()
    say("BOOK-LEVEL OVERROUND FOR THIS RACE (sum of 1/price for 15 priced runners)")
    rows = []
    for bid, decs in per_book.items():
        if len(decs) < 8:
            continue
        ov = sum(1.0 / d for d in decs)
        rows.append((ov, books.get(bid, bid), len(decs)))
    for ov, nm, n in sorted(rows):
        say("   %-18s overround %.4f  (=%+.2f%% margin)  on %d runners"
            % (nm, ov, (ov - 1) * 100, n))
    if rows:
        best_ov = min(rows)[0]
        lines = []
        for rid in ids:
            entry = by_id.get(rid) or {}
            ol = entry.get("odds") or []
            if not ol:
                continue
            lines.append(max(float(o["price"]["decimal"]) for o in ol))
        if lines:
            say("   %-18s overround %.4f  (=%+.2f%% margin)  on %d runners"
                % ("BEST-OF-MARKET", sum(1.0 / d for d in lines), 
                   (sum(1.0 / d for d in lines) - 1) * 100, len(lines)))

    txt = "\n".join(L)
    with open(r"E:\Test\racing-form-system\reports\_o10.log", "w",
              encoding="utf-8") as f:
        f.write(txt)
    print(txt.encode("ascii", "replace").decode("ascii"))


main()
