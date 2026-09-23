#!/usr/bin/env python3
"""
rp_to_rows.py  -  turn cached Racing Post JSON into the exact 37-column raceform shape.

THE PIPELINE (reverse-engineered from the files themselves)
-----------------------------------------------------------
The raceform database is a merge of two Racing Post feeds:

  racecards (pre-race)   rating_band, pattern, sex_code, headgear_first, region suffixes
  results   (post-race)  pos, sp, beaten distances, prize, winning time, run comment

Both are reachable:
  results  : https://www.racingpost.com/results/<courseUid>/<courseKey>/<date>/<raceId>/
  racecard : https://www.racingpost.com/racecards/<courseUid>/<courseKey>/<date>/<raceId>

Normalisation rules (derived by diffing against the existing database):
  comment   ", " -> " - ",  betting movements appended as "(op 9/2)"
  names     & and ' stripped,  country suffix from the *region* fields
  time      "5m 29.51s" -> "5:29.51"
  lengths   "1½" -> 1.5,  "¾" -> 0.75
  type      B->NH Flat, C->Chase, F->Flat, H->Hurdle, X->Flat
  off       localRaceDatetime -> 24h HH:MM
"""

import datetime as dt
import glob
import json
import os
import re

BASE = os.environ.get("RP_BASE") or r"D:\Mydata"
CACHE = os.path.join(BASE, "_rp_cache")

COLUMNS = ["date", "course", "race_id", "off", "race_name", "type", "class", "pattern",
           "rating_band", "age_band", "sex_rest", "dist", "going", "ran", "num", "pos",
           "draw", "ovr_btn", "btn", "horse", "age", "sex", "wgt", "hg", "time", "sp",
           "jockey", "trainer", "prize", "or", "rpr", "ts", "sire", "dam", "damsire",
           "owner", "comment"]

TYPE_MAP = {"B": "NH Flat", "C": "Chase", "F": "Flat", "H": "Hurdle", "X": "Flat",
            "N": "NH Flat", "S": "Flat"}

DASH = "\u2013"          # en dash used for "no value" in or/rpr/ts

# --------------------------------------------------------------------------- helpers

def clean(s):
    """Owner-style: remove & , apostrophes and commas, then Title Case the result
    ('S Sowray & Alan O'Keeffe' -> 'S Sowray Alan Okeeffe')."""
    if s is None:
        return ""
    s = str(s).replace("\u2019", "'").replace("'", "").replace("&", "")
    s = re.sub(r"\s{2,}", " ", s)
    s = s.replace(",", "").replace("(", "").replace(")", "").replace("/", "")
    s = s.replace("\u2013", "").replace("\u2014", "").replace("-", "")
    return s.strip().title()


def fix_apostrophes(s):
    """Jockey/trainer/pedigree-style: drop the apostrophe, KEEP the original case
    ('Conor O'Farrell' -> 'Conor OFarrell')."""
    if s is None:
        return ""
    s = str(s).replace("\u2019", "'").replace("'", "")
    return re.sub(r"\s{2,}", " ", s).strip()


def strip_punct(s):
    """race_name style: apostrophes, double quotes and commas all removed."""
    s = fix_apostrophes(s)
    s = s.replace('"', "").replace(",", "")
    return re.sub(r"\s{2,}", " ", s).strip()



def tidy_text(s):
    """For the run comment: apostrophes are KEPT verbatim."""
    if s is None:
        return ""
    return re.sub(r"\s{2,}", " ", str(s)).strip()


KNOWN_REGIONS = {"GB", "IRE", "FR", "GER", "USA", "ITY", "JPN", "AUS", "SAF", "BEL",
                 "SPA", "HK", "CAN", "NZ", "POL", "SWE", "NOR", "DEN", "UAE", "TUR",
                 "IND", "ARG", "BRZ", "CHI", "PER", "KOR", "SIN", "IRE", "GB"}


def with_region(name, suffix_or_region):
    """Append the country suffix. When the source has none the build defaults to (GB)."""
    if not name:
        return ""
    n = fix_apostrophes(name)
    m = re.match(r"^(.*?)\s*\(([A-Za-z]{2,3})\)\s*$", n)
    if m:
        return "%s (%s)" % (m.group(1).strip(), m.group(2).upper())
    r = ""
    if suffix_or_region:
        r = re.sub(r"[^A-Za-z]", "", str(suffix_or_region)).upper()
    if r not in KNOWN_REGIONS:
        r = "GB"                      # source omitted it -> the build writes (GB)
    return "%s (%s)" % (n.strip(), r)



FRACT = {"\u00bc": 0.25, "\u00bd": 0.5, "\u00be": 0.75, "\u2153": 0.33, "\u2154": 0.67,
         "\u215b": 0.125, "\u215c": 0.375, "\u215d": 0.625, "\u215e": 0.875}

# nose / head / neck / short-head as RP writes them -> the decimals the build stores
SHORT = {"nse": 0.05, "nose": 0.05, "dht": 0.05, "hd": 0.2, "head": 0.2,
         "shd": 0.1, "sh": 0.1, "short head": 0.1, "shorthead": 0.1,
         "nk": 0.3, "neck": 0.3,
         "1/2": 0.5, "3/4": 0.75, "1/4": 0.25}


def lengths(v):
    """'1½' -> 1.5, '¾' -> 0.75, 'nk' -> 0.3, '' -> None."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in ("-", DASH, "\u2014"):
        return None
    low = s.lower()
    if low in SHORT:
        return SHORT[low]
    m = re.match(r"^(\d+)?\s*([\u00bc\u00bd\u00be\u2153\u2154\u215b\u215c\u215d\u215e])$", s)
    if m:
        return (float(m.group(1)) if m.group(1) else 0.0) + FRACT[m.group(2)]
    try:
        return float(s)
    except ValueError:
        return None



def num_out(v):
    """Format a length for the db: whole numbers stay whole (1 not 1.0)."""
    if v is None:
        return 0
    return int(v) if float(v) == int(v) else round(float(v), 2)


def norm_time(s):
    """'5m 29.51s' -> '5:29.51',  '1m 2.33s' -> '1:02.33'."""
    if not s:
        return ""
    m = re.match(r"^\s*(?:(\d+)m\s*)?([\d.]+)s\s*$", str(s))
    if not m:
        return str(s).strip()
    mins = int(m.group(1)) if m.group(1) else 0
    secs = float(m.group(2))
    return "%d:%05.2f" % (mins, secs)


def off_24h(race):
    """The build stores the race's scheduled time from `raceDatetime` (the meeting's own
    clock as Racing Post publishes it), NOT localRaceDatetime."""
    s = race.get("raceDatetime") or race.get("localRaceDatetime") or ""
    if len(s) >= 16 and s[11:13].isdigit():
        return s[11:16]
    h = (race.get("header") or {}).get("raceTime") or ""
    return h


def prize_str(v):
    """'£3,510' -> '3510';  '£240.70' -> '240.7';  '€5,900' -> '€5900' (symbol kept)."""
    if not v:
        return ""
    s = str(v).replace(",", "").replace("\u00a3", "").strip()
    if not s:
        return ""
    if re.match(r"^[\d.]+$", s):
        try:
            f = float(s)
            if f == int(f):
                return "%d" % int(f)
            return ("%.2f" % f).rstrip("0").rstrip(".")
        except ValueError:
            return s
    return s


CLASS_ANY_RE = re.compile(r"\((Class\s*\d)\)", re.I)


def extract_class_from_title(title):
    """Hong Kong / foreign races carry the class inside the title: strip it out and
    return it, because the build stores it in the `class` column."""
    if not title:
        return title, ""
    m = CLASS_ANY_RE.search(title)
    cls = ""
    if m:
        cls = "Class %s" % re.sub(r"[^0-9]", "", m.group(1))
        title = CLASS_ANY_RE.sub("", title)
    return re.sub(r"\s{2,}", " ", title).strip(), cls



def sex_from_colour(colour_sex):
    """'ch f' -> 'F', 'b g' -> 'G', 'br c' -> 'C', 'b f' -> 'F'."""
    if not colour_sex:
        return ""
    parts = str(colour_sex).strip().split()
    return parts[-1].upper()[:1] if parts else ""


def headgear(hg, first_time):
    """headgear 'p' + first time -> 'p1'."""
    h = (hg or "").strip()
    if not h:
        return ""
    return h + "1" if first_time else h


SEX_REST_PATTERNS = [
    (r"\bColts\s*(?:&|and)\s*Geldings\b", "C & G"),
    (r"\bFillies\s*(?:&|and)\s*Mares\b", "F & M"),
    (r"\bFillies\s*(?:&|and)\s*Colts\b", "C & F"),
    (r"\bMares\b", "M"),
    (r"\bFillies\b", "F"),
    (r"\bColts\b", "C"),
    (r"\bGeldings\b", "G"),
]

PATTERN_RE = re.compile(r"\((Group\s*\d|Grade\s*[A-Z0-9]|Listed)\)", re.I)
CLASS_RE = re.compile(r"\((Class\s*\d)\)", re.I)


def extract_pattern_pattern_from_title(title):
    """Pattern is stored separately and stripped out of race_name."""
    if not title:
        return "", ""
    m = PATTERN_RE.search(title)
    pat = ""
    if m:
        pat = re.sub(r"\s+", " ", m.group(1)).strip()
        pat = pat[0].upper() + pat[1:]
        title = PATTERN_RE.sub("", title)
    return re.sub(r"\s{2,}", " ", title).strip(), pat


def derive_sex_rest(title):
    for rx, val in SEX_REST_PATTERNS:
        if re.search(rx, title or "", re.I):
            return val
    return ""


# --------------------------------------------------------------------- row builder

def build_rows(race, card=None):
    """race = cached results JSON; card = optional racecard race object.

    Returns a list of dicts keyed by COLUMNS - one per runner.
    """
    if not race:
        return []
    h = race.get("header") or {}
    det = race.get("details") or {}

    date = (race.get("localRaceDatetime") or race.get("raceDatetime") or "")[:10]

    # racecard supplied fields (captured pre-race by rp_racecards.py)
    card_band = (card or {}).get("ratingBand") or ""
    card_pat = (card or {}).get("pattern") or ""
    card_sex = (card or {}).get("sex_code") or ""
    card_class = (card or {}).get("raceClass")

    title_raw = h.get("raceTitle") or ""
    title, pat_from_title = extract_pattern_pattern_from_title(title_raw)
    title, cls_from_title = extract_class_from_title(title)
    race_class = h.get("raceClass")
    if race_class not in (None, "", "None"):
        cls = "Class %s" % race_class
    elif cls_from_title:
        cls = cls_from_title
    elif card_class not in (None, "", "None"):
        cls = "Class %s" % card_class
    else:
        cls = ""

    # prize money by finishing position
    prizes = {}
    for p in (h.get("prizes") or []):
        try:
            prizes[int(p.get("position"))] = p.get("formatted")
        except (TypeError, ValueError):
            continue

    common = {
        "date": date,
        "course": race.get("courseName") or "",
        "race_id": race.get("raceId"),
        "off": off_24h(race),
        "race_name": strip_punct(title),
        "type": TYPE_MAP.get(h.get("raceTypeCode"), h.get("raceTypeCode") or ""),
        "class": cls,
        "pattern": (card_pat or pat_from_title or ""),
        "rating_band": (card_band or ""),
        "age_band": h.get("agesAllowed") or (card or {}).get("ageRestriction") or "",
        "sex_rest": (card_sex or derive_sex_rest(title_raw) or ""),
        "dist": h.get("distanceShort") or (card or {}).get("displayDistance") or "",
        "going": h.get("going") or (card or {}).get("meetingGoing") or "",
        "ran": det.get("numberOfRunners") or (card or {}).get("numberOfRunners"),
    }

    win_time = norm_time(det.get("winningTime"))
    win_secs = None
    m = re.match(r"^(\d+):([\d.]+)$", win_time or "")
    if m:
        win_secs = int(m.group(1)) * 60 + float(m.group(2))

    # ---- running total of the consecutive gaps = distance behind the winner.
    # The build derives ovr_btn this way rather than using RP's beatenDistanceToWinner,
    # because RP rounds each gap independently and the totals drift.
    btn_by_pos = {}
    for r in (race.get("runners") or []):
        try:
            p = int(r.get("outcomeCode"))
        except (TypeError, ValueError):
            continue
        btn_by_pos[p] = lengths(r.get("beatenDistance"))
    cum, run = {}, 0.0
    for p in sorted(btn_by_pos):
        if p == 1:
            cum[p] = 0.0
        else:
            run += (btn_by_pos.get(p) or 0.0)
            cum[p] = round(run, 4)

    rows = []
    for r in (race.get("runners") or []):
        ped = r.get("pedigree") or {}
        cm = r.get("comment") or {}
        text = tidy_text(cm.get("comment") or "")
        moves = tidy_text(cm.get("bettingMovements") or "")
        if text:
            text = re.sub(r"\s*,\s*", " - ", text)
        if moves:
            text = "%s(%s)" % (text, moves)
        try:
            pos = int(r.get("outcomeCode"))
        except (TypeError, ValueError):
            pos = r.get("outcomeCode") or ""
        finished = isinstance(pos, int)
        prize = prize_str(prizes.get(pos)) if finished else ""

        ovr = lengths(r.get("beatenDistanceToWinner"))
        btn = lengths(r.get("beatenDistance"))
        # use RP's distance-to-winner when it gives one; otherwise fall back to the
        # running total of the consecutive gaps
        ovr_val = ovr if ovr is not None else cum.get(pos, 0.0)
        if not finished:
            ovr_out = btn_out = "-"
            ovr_val = 0.0
        else:
            ovr_out = num_out(ovr_val)
            btn_out = num_out(btn)

        # per-runner time: winner's time plus its beaten distance, converted at
        # 6 lengths/second on the Flat and 5 lengths/second over jumps.
        if not finished:
            t = "-"
        elif win_secs is not None:
            k = (1.0 / 6.0) if common["type"] == "Flat" else 0.2
            secs = win_secs + ovr_val * k
            t = "%d:%05.2f" % (int(secs // 60), secs % 60)
        else:
            t = ""

        row = dict(common)
        row.update({
            "num": r.get("saddleClothNo"),
            "pos": pos,
            "draw": re.sub(r"[^0-9]", "", str(r.get("drawLabel") or "")),
            "ovr_btn": ovr_out,
            "btn": btn_out,
            "horse": with_region(r.get("horseName"), r.get("horseSuffix")),
            "age": r.get("age"),
            "sex": sex_from_colour(ped.get("colourSex")),
            "wgt": ("%s-%s" % (r.get("weightStones"), r.get("weightPounds"))
                    if r.get("weightStones") is not None else ""),
            "hg": headgear(r.get("headgear"), r.get("isFirstTimeHeadgear")),
            "time": t,
            "sp": r.get("odds") or "",
            "jockey": fix_apostrophes(r.get("jockeyName")),
            "trainer": fix_apostrophes(r.get("trainerName")),
            "prize": prize,
            "or": (str(r.get("officialRating")).strip() if r.get("officialRating") not in (None, "")
                   else DASH),
            "rpr": (str(r.get("rpRating")).strip() if r.get("rpRating") not in (None, "")
                    else DASH),
            "ts": (str(r.get("topspeed")).strip() if r.get("topspeed") not in (None, "")
                   else DASH),
            "sire": with_region(ped.get("sireName"), ped.get("sireSuffix")),
            "dam": with_region(ped.get("damName"), ped.get("damSuffix")),
            "damsire": fix_apostrophes(ped.get("damSireName")),
            "owner": clean(r.get("ownerName")),
            "comment": text,
        })
        rows.append(row)
    return rows


def load_day(date):
    """All cached races for a date -> list of (race, card=None)."""
    out = []
    for f in sorted(glob.glob(os.path.join(CACHE, date, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if d:
            out.append(d)
    return out


def day_rows(date):
    """Build rows for a date, folding in any racecards captured pre-race."""
    try:
        import rp_racecards
        cards = rp_racecards.load_day(date)
    except Exception:
        cards = {}
    rows = []
    for race in load_day(date):
        card = cards.get(str(race.get("raceId")))
        rows.extend(build_rows(race, card))
    return rows


def load_into_db(rows, db_path, csv_path=None):
    """Append rows to the database (idempotent via the unique index) and, optionally,
    to the flat CSV. Only rows that are genuinely NEW are appended to the CSV, so
    re-running over an overlapping range cannot duplicate CSV lines.
    Returns (inserted, skipped)."""
    import sqlite3
    if not rows:
        return 0, 0
    con = sqlite3.connect(db_path)
    con.isolation_level = None
    cur = con.cursor()
    cur.execute("PRAGMA busy_timeout=60000")     # the watcher and backfill can overlap
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_data_row "
                "ON data(date, race_id, horse, pos)")

    # work out which of these rows already exist, so the CSV stays clean too
    dates = sorted(set(str(r.get("date")) for r in rows))
    existing = set()
    for d in dates:
        for row in cur.execute("SELECT date, race_id, horse, pos FROM data WHERE date=?",
                               (d,)):
            existing.add(tuple(str(x) for x in row))

    def key(r):
        return (str(r.get("date")), str(r.get("race_id")), str(r.get("horse")),
                str(r.get("pos")))

    new_rows = [r for r in rows if key(r) not in existing]

    before = cur.execute("SELECT COUNT(*) FROM data").fetchone()[0]
    if new_rows:
        collist = ", ".join('"%s"' % c for c in COLUMNS)   # "or" must be quoted
        params = ", ".join("?" * len(COLUMNS))
        cur.execute("PRAGMA journal_mode=MEMORY")
        cur.execute("PRAGMA synchronous=OFF")
        cur.execute("BEGIN")
        cur.executemany("INSERT OR IGNORE INTO data (%s) VALUES (%s)" % (collist, params),
                        [[r.get(c) for c in COLUMNS] for r in new_rows])
        cur.execute("COMMIT")
        cur.execute("PRAGMA synchronous=FULL")
    after = cur.execute("SELECT COUNT(*) FROM data").fetchone()[0]
    con.commit()
    con.close()

    if csv_path and new_rows:
        import csv as _csv
        newline = b""
        if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
            with open(csv_path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    newline = b"\n"
        with open(csv_path, "ab") as f:
            if newline:
                f.write(newline)
            buf = []
            for r in new_rows:
                sio = __import__("io").StringIO()
                w = _csv.writer(sio, lineterminator="\n")
                w.writerow(["" if r.get(c) is None else r.get(c) for c in COLUMNS])
                buf.append(sio.getvalue().encode("utf-8"))
            f.write(b"".join(buf))

    return after - before, len(rows) - (after - before)


def fill(date, db_path, csv_path=None, dry=False):
    rows = day_rows(date)
    if not rows:
        return 0, 0, 0
    if dry:
        return len(rows), 0, 0
    ins, skipped = load_into_db(rows, db_path, csv_path)
    return len(rows), ins, skipped


# ------------------------------------------------------------------------ validate

def validate(date, db_path):
    """Compare generated rows against the existing database and report a match rate
    per column. This is how the transforms get driven to 100%."""
    import sqlite3
    c = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    cur = c.cursor()
    dbcols = [r[1] for r in cur.execute("PRAGMA table_info(data)")]
    db = {}
    for row in cur.execute("SELECT * FROM data WHERE date=?", (date,)):
        rec = dict(zip(dbcols, row))
        db[(str(rec["race_id"]), str(rec["num"]))] = rec
    c.close()

    gen = {}
    for r in day_rows(date):
        gen[(str(r["race_id"]), str(r["num"]))] = r

    both = sorted(set(gen) & set(db))
    only_gen = sorted(set(gen) - set(db))
    only_db = sorted(set(db) - set(gen))

    print("=" * 78)
    print("VALIDATION  %s" % date)
    print("=" * 78)
    print("  generated rows : %d" % len(gen))
    print("  database rows  : %d" % len(db))
    print("  matched keys   : %d" % len(both))
    if only_gen:
        print("  ONLY in generated (%d): %s" % (len(only_gen), only_gen[:6]))
    if only_db:
        print("  ONLY in database  (%d): %s" % (len(only_db), only_db[:6]))

    stats = {}
    examples = {}
    for key in both:
        g, d = gen[key], db[key]
        for col in COLUMNS:
            gv = "" if g.get(col) is None else str(g.get(col))
            dv = "" if d.get(col) is None else str(d.get(col))
            ok = (gv == dv)
            s = stats.setdefault(col, [0, 0])
            s[0 if ok else 1] += 1
            if not ok and len(examples.setdefault(col, [])) < 2:
                examples[col].append((key, gv, dv))

    print("\n  %-12s %8s %8s %8s   %s" % ("column", "match", "diff", "match%", "example mismatch"))
    print("  " + "-" * 74)
    total_ok = total = 0
    worst = []
    for col in COLUMNS:
        ok, bad = stats.get(col, [0, 0])
        total_ok += ok
        total += ok + bad
        pct = 100.0 * ok / (ok + bad) if (ok + bad) else 0
        ex = ""
        if examples.get(col):
            k, gv, dv = examples[col][0]
            ex = "gen=%r db=%r" % (gv[:26], dv[:26])
        print("  %-12s %8d %8d %7.2f%%   %s" % (col, ok, bad, pct, ex))
        if pct < 100:
            worst.append((pct, col))
    print("\n  OVERALL CELL MATCH RATE: %.2f%%  (%d of %d cells)"
          % (100.0 * total_ok / total, total_ok, total))
    return worst


def write_csv(rows, path):
    import csv
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)


DB_PATH = os.path.join(BASE, r"form_2015-present\form_2015-present\raceform.db")
CSVMAIN = os.path.join(BASE, r"form_2015-present\form_2015-present\raceform.csv")


def fill_range(d0, d1, db_path, csv_path=None, dry=True):
    """Fill every date from d0..d1 that has cached results. Resumable: dates already
    in the db are skipped, so this can be run again after any interruption."""
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    have = set(r[0] for r in con.execute("SELECT DISTINCT date FROM data"))
    con.close()
    a = dt.date.fromisoformat(d0)
    b = dt.date.fromisoformat(d1)
    grand = 0
    while a <= b:
        d = a.isoformat()
        a += dt.timedelta(days=1)
        rows = day_rows(d)
        if not rows:
            continue
        if d in have:
            print("   %s  already present - skipped" % d)
            continue
        if dry:
            print("   %s  DRY: would add %d rows" % (d, len(rows)))
            continue
        n, ins, skipped = fill(d, db_path, csv_path)
        grand += ins
        print("   %s  rows=%d inserted=%d skipped=%d" % (d, n, ins, skipped))
    return grand


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", metavar="DATE")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--emit", metavar="DATE")
    ap.add_argument("--out")
    ap.add_argument("--fill", nargs=2, metavar=("FROM", "TO"),
                    help="fill dates into the database + csv")
    ap.add_argument("--csv", default=CSVMAIN)
    ap.add_argument("--apply", action="store_true", help="with --fill, actually write")
    a = ap.parse_args()
    if a.validate:
        validate(a.validate, a.db)
    elif a.emit:
        rows = day_rows(a.emit)
        out = a.out or os.path.join(BASE, "_rp_cache", "out_%s.csv" % a.emit)
        print("wrote %d rows -> %s" % (write_csv(rows, out), out))
    elif a.fill:
        print("FILL %s -> %s   (%s)" % (a.fill[0], a.fill[1],
                                        "APPLY" if a.apply else "DRY RUN"))
        n = fill_range(a.fill[0], a.fill[1], a.db, a.csv, dry=not a.apply)
        print("total inserted: %d" % n)
