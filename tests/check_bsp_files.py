"""Check exactly which Betfair SP publish file holds which race day.

The backfill docstring says the file named DDMMYYYY carries the PREVIOUS day's
races. Verify that (and that the URL format is DDMMYYYY, not YYYYMMDD).
"""
import csv
import io
import urllib.request

UA = {"User-Agent": "Mozilla/5.0"}


def probe(name):
    url = f"https://promo.betfair.com/betfairsp/prices/{name}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                    timeout=30) as r:
            text = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  {name:34s} -> {e}")
        return
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        print(f"  {name:34s} -> 200 but empty")
        return
    def key(r, k):
        return next((v for kk, v in r.items()
                                 if kk.lower() == k), "")
    dates = sorted({str(key(r, "event_dt"))[:10] for r in rows})
    courses = sorted({str(key(r, "menu_hint"))[:22] for r in rows})[:3]
    print(f"  {name:34s} -> 200  rows={len(rows):5d}  race-dates={dates}  "
          f"e.g. {courses}")


print("publish file (DDMMYYYY)              result")
for n in ("dwbfpricesukwin14092026.csv",     # publish 14th -> 13th races
          "dwbfpricesukwin15092026.csv",     # publish 15th -> 14th races
          "dwbfpricesirewin15092026.csv",    # publish 15th -> 14th IRE races
          "dwbfpricesukwin16092026.csv",     # publish 16th -> 15th races (today)
          "dwbfpricesirewin16092026.csv"):
    probe(n)

print("\nfor comparison, the YYYYMMDD format I tried first:")
probe("dwbfpricesukwin20260914.csv")
