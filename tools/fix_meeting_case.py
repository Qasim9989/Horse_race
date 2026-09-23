#!/usr/bin/env python3
"""fix_meeting_case.py - merge meetings that differ only by capitalisation.

cloud_update.py inserted Racing Post's spelling ("Goodwood") into a database that
uses "goodwood", so every course gained a second meeting entry:

    ['Chantilly','Goodwood','Happy Valley','Listowel','Perth','Redcar',
     'goodwood','happy valley','listowel','perth','redcar']

cloud_update.py now maps new rows onto the spelling already present.  This repairs
the rows inserted before that fix, then removes any exact duplicates the merge makes.

USAGE
    python fix_meeting_case.py            # report
    python fix_meeting_case.py --apply
"""

import argparse
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def find_db():
    d = HERE
    for _ in range(5):
        for cand in (os.path.join(d, "racing_form.db"),
                     os.path.join(d, "cloud_app", "racing_form.db")):
            if os.path.exists(cand) and os.path.getsize(cand) > 4096:
                return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.join(HERE, "racing_form.db")


DB = find_db()


def course_key(name):
    """Collapse the different spellings of ONE course onto a single key.

    Two writers use different conventions, so the same track appears as "Goodwood"
    and "goodwood", or "Kempton (AW)" and "kempton-park" (Racing Post uses its URL
    slug).

    An all-weather marker is kept SIGNIFICANT.  The data says the split is real, not
    cosmetic: Kempton has 1,333 non-AW rows and 10,807 "Kempton (AW)" rows, Newcastle
    1,771 vs 12,627.  Those are different fixtures - Kempton stages jumps on turf -
    so folding them together would mislabel every one of them.  Merging only happens
    within the same AW status.
    """
    s = str(name or "").strip().lower().replace("'", "").replace("\u2019", "")
    aw = 1 if re.search(r"\(aw\)|\baw\b", s) else 0
    s = re.sub(r"\(aw\)", " ", s)
    s = re.sub(r"\baw\b", " ", s)
    s = re.sub(r"\bracecourse\b", " ", s)
    s = re.sub(r"\bpark\b", " ", s)          # kempton-park -> kempton
    s = re.sub(r"[^a-z0-9]", "", s)
    return (s, aw)


def pretty(name):
    """Display spelling: hyphens to spaces, words capitalised, (AW) preserved."""
    s = str(name or "").strip().replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    out = []
    for word in s.split(" "):
        if word.upper() == "AW":
            out.append("(AW)")
        elif word:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def pick_canonical(options):
    """Choose the spelling to keep: prefer the app's own convention, then volume."""
    def score(item):
        n, spell = item
        return (1 if "(AW)" in spell.upper() else 0,
                1 if spell[:1].isupper() else 0,
                n)
    return max(options, key=score)[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    cur = con.cursor()

    print("=" * 78)
    print("FIX MEETING CASE")
    print("=" * 78)
    print("  db   : %s" % DB)
    print("  mode : %s" % ("APPLY" if a.apply else "DRY RUN"))
    print()

    groups = {}
    for m, n in cur.execute(
            "SELECT meeting, COUNT(*) FROM race_results "
            "WHERE meeting IS NOT NULL AND TRIM(meeting)<>'' GROUP BY meeting"):
        groups.setdefault(course_key(m), []).append((n, str(m).strip()))

    def relabel(old, new, n):
        print("     %-22s %5d rows -> %-22s" % (old, n, new))
        if a.apply:
            cur.execute("UPDATE race_results SET meeting=? "
                        "WHERE meeting IS NOT NULL AND meeting=? AND meeting<>?",
                        (new, old, new))

    # --- 1. same course, same AW status, written differently -------------------
    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    print("  courses written more than one way: %d" % len(dupes))
    total_moved = 0
    for key, opts in sorted(dupes.items()):
        keep = pick_canonical(opts)
        for n, spell in sorted(opts, reverse=True):
            if spell == keep:
                continue
            relabel(spell, keep, n)
            total_moved += n
    print()
    print("  rows re-labelled by merge: %d" % total_moved)

    # --- 2. lone lowercase slugs: REPORTED, not changed -----------------------
    # An earlier version title-cased these.  It was a bad idea: it also rewrote
    # names that were already correct ("baden-baden" -> "Baden Baden") and mangled
    # country tags ("brighton (gb)" -> "Brighton (gb)"), across hundreds of foreign
    # tracks this site does not even show.  Left alone deliberately - the merge above
    # is the part that is provably safe.
    print()
    print("  lone lowercase slugs (left as-is, see note in the script):")
    lone = []
    for (base, aw), opts in sorted(groups.items()):
        if len(opts) == 1 and opts[0][1].islower():
            lone.append((opts[0][0], opts[0][1]))
    lone.sort(reverse=True)
    print("     %d meetings, %d rows - e.g. %s"
          % (len(lone), sum(n for n, _ in lone),
             ", ".join("%s(%d)" % (s, n) for n, s in lone[:8])))

    # --- 3. REPORT ONLY: same course, different AW status --------------------
    # Kempton/Kempton (AW) etc.  These are genuinely different fixtures, so nothing
    # is changed - but a human may still want to look.
    print()
    print("  same course, DIFFERENT AW status (NOT changed - different fixtures):")
    by_base = {}
    for (base, aw), opts in groups.items():
        by_base.setdefault(base, []).append((aw, opts))
    pairs = 0
    for base, lst in sorted(by_base.items()):
        if len(lst) > 1:
            pairs += 1
            bits = []
            for aw, opts in sorted(lst):
                for n, spell in opts:
                    bits.append("%s=%d" % (spell, n))
            print("     %-22s %s" % (base, "  vs  ".join(bits)))
    if not pairs:
        print("     none")

    # the merge can create exact duplicates - keep the most complete of each group.
    # NOTE: race_results has no race_time column (that one lives in the ledger), so
    # the key is (race_date, horse_name, meeting) - a horse runs once per meeting.
    print()
    print("  exact duplicates (same date/horse/meeting):")
    dups = list(cur.execute("""
        SELECT race_date, horse_name, meeting, COUNT(*) n, GROUP_CONCAT(rowid) ids
        FROM race_results
        GROUP BY race_date, horse_name, meeting
        HAVING n > 1 ORDER BY n DESC"""))
    print("     %d groups" % len(dups))
    del_rows = 0
    for d, hn, mt, n, ids in dups:
        idlist = [int(x) for x in str(ids).split(",")]
        # keep the row with the fewest empty cells
        best, best_filled = None, -1
        for rid in idlist:
            row = cur.execute(
                "SELECT distance, finish_pos, beaten_distance, weight_lbs, "
                "official_rating, topspeed, rpr, jockey, sp_odds, going, race_id "
                "FROM race_results WHERE rowid=?", (rid,)).fetchone()
            filled = sum(1 for v in row if v not in (None, "", "nan", "None"))
            if filled > best_filled:
                best, best_filled = rid, filled
        losers = [r for r in idlist if r != best]
        del_rows += len(losers)
        if a.apply and losers:
            cur.executemany("DELETE FROM race_results WHERE rowid=?",
                            [(r,) for r in losers])
    print("     rows to delete: %d" % del_rows)

    if a.apply:
        cur.execute("REINDEX")
        con.commit()
        print("\n  applied.")
    else:
        print("\n  Nothing written.  Re-run with --apply.")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
