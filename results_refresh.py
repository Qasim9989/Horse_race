"""
UPDATE RESULTS NOW - scrape, sync, settle
=========================================
One call the app's button makes, for any date, at any time of day.

Why this exists: `settle_ledger()` can only settle what `Scraped_Results` already
holds, and that table is filled by a separate Playwright scraper
(`scripts/racingtv_db_updater.py`). When nobody runs the scraper, the table goes
stale - on 21 Sep it still stopped at the 19th - and the Settle button then reports
"updated 0 outcomes" while looking broken. Betfair cannot fill the gap: its API
serves live markets only, so a finished day cannot be fetched from it afterwards.

So the chain is:
    1. scrape    racingtv.com/results/<date> -> RACINGTV_2023_2026.dbo.Scraped_Results
    2. sync      -> race_results and system_results_ledger in racing_form.db
    3. settle    -> finish positions, SP/BSP and Early-vs-SP P&L on the ledger

Every step is idempotent: the scraper skips races it already has (unless force),
the sync only inserts what is missing, and the settle re-checks rows.
"""
from __future__ import annotations

import os
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(PROJECT_DIR, "scripts")
UPDATER = os.path.join(SCRIPTS_DIR, "racingtv_db_updater.py")
SYNCER = os.path.join(SCRIPTS_DIR, "sync_results_ledger.py")


def _run(script: str, args: list[str], timeout: int = 1800) -> tuple[bool, str]:
    """Run one of the pipeline scripts, returning (ok, tail of its output)."""
    if not os.path.exists(script):
        return False, f"{os.path.basename(script)} not found at {script}"
    try:
        done = subprocess.run([sys.executable, script] + args, cwd=PROJECT_DIR,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"{os.path.basename(script)} timed out after {timeout // 60} minutes."
    except Exception as ex:                                    # noqa: BLE001
        return False, f"{os.path.basename(script)} could not start: {ex}"
    tail = "\n".join((done.stdout or "").strip().splitlines()[-6:])
    if done.returncode != 0:
        err = (done.stderr or "").strip().splitlines()[-3:]
        return False, (tail + "\n" + "\n".join(err)).strip()
    return True, tail


def races_due(date_str: str, min_age_minutes: int) -> tuple[int, int, str]:
    """(races finished long enough, total races, note) for the Racing TV card.

    Used to decide whether a scrape is worth starting yet.  `min_age_minutes` is the
    grace period after the off - a race whose result page is still filling in should
    not be scraped, because the scraper then treats it as done and never revisits it.
    """
    try:
        import datetime as _dt
        import rtv_api
        races = rtv_api.day_races(date_str) or []
        if not races:
            return 0, 0, "no card published yet"
        now = _dt.datetime.now(_dt.timezone.utc)
        due = 0
        for race in races:
            off = race.get("start_iso")
            if not off:
                continue
            try:
                when = _dt.datetime.fromisoformat(str(off).replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=_dt.timezone.utc)
            if (now - when).total_seconds() >= min_age_minutes * 60:
                due += 1
        return due, len(races), f"{due} of {len(races)} races are past off+{min_age_minutes}m"
    except Exception as ex:                                    # noqa: BLE001
        # Cannot tell - scrape anyway rather than silently do nothing.
        return 1, 0, f"card check failed ({ex}) - scraping anyway"


def refresh(date_str: str, scrape: bool = True, sync: bool = True,
            settle: bool = True, force_scrape: bool = False,
            min_age_minutes: int = 30) -> dict:
    """Scrape the day, sync it in, settle the ledger.  Returns a report for the UI."""
    steps: list[tuple[str, bool, str]] = []
    due_note = ""

    if scrape:
        if not force_scrape and min_age_minutes > 0:
            due, total, due_note = races_due(date_str, min_age_minutes)
            if due == 0:
                scrape = False
                steps.append(("scrape", True,
                              f"skipped - {due_note}. Nothing has finished long enough yet."))
        if scrape:
            steps.append(("scrape", *_run(UPDATER, [date_str, date_str]
                                          + (["--force"] if force_scrape else []))))
    if sync:
        steps.append(("sync", *_run(SYNCER, [])))
    if settle:
        try:
            import settle_daily_results
            updated = settle_daily_results.settle_ledger(date_str, force=True) or 0
            steps.append(("settle", True, f"{updated} outcome(s) updated"))
        except Exception as ex:                                # noqa: BLE001
            steps.append(("settle", False, str(ex)))

    ok = all(s[1] for s in steps)
    lines = [f"{'-' if s[1] else 'FAILED'} {s[0]}: {s[2] or 'done'}" for s in steps]
    if due_note:
        lines.insert(0, f"card: {due_note}")
    return {"ok": ok, "steps": steps, "message": "\n".join(lines)}


def main() -> None:
    import argparse
    import datetime as dt

    parser = argparse.ArgumentParser(description="Scrape results, sync and settle one date")
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--no-scrape", action="store_true", help="skip the web scraper")
    parser.add_argument("--force", action="store_true", help="re-scrape races already stored")
    parser.add_argument("--min-age-minutes", type=int, default=30,
                        help="wait this long after the off before scraping (default 30)")
    parser.add_argument("--dry-run", action="store_true",
                        help="say whether a scrape is due, without doing anything")
    args = parser.parse_args()

    if args.dry_run:
        due, total, note = races_due(args.date, args.min_age_minutes)
        print(f"{args.date}: {note}")
        print("a scrape would run now." if due else "nothing to scrape yet.")
        return

    result = refresh(args.date, scrape=not args.no_scrape, force_scrape=args.force,
                     min_age_minutes=args.min_age_minutes)
    print(result["message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
