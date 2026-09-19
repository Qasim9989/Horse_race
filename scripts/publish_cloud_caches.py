"""
Publish refreshed Streamlit Cloud caches to GitHub
==================================================
Commits the three pre-computed cache files produced by
build_all_selections_cache.py inside cloud_app/ and pushes them so Streamlit
Cloud redeploys with today's picks automatically.

Cache files handled:
    cloud_app/bf_odds_today.json        (Betfair WIN + PLACE odds)
    cloud_app/tips_today.json           (Tips selections)
    cloud_app/speed_stride_today.json   (Speed & Stride selections)
    cloud_app/results_ledger.csv        (settled picks - the Results tab's source)

Behaviour:
  * Only files whose "date" matches today are staged and published; stale
    files are reported and left alone.
  * Exits 0 with "nothing to commit" when the caches are already published,
    so it is safe to run repeatedly.
  * Runs unattended from run_auto_daily_pipeline.bat, so git is invoked with
    GIT_TERMINAL_PROMPT=0 to fail fast instead of hanging on a credential
    prompt.
"""

import csv
import datetime
import json
import os
import subprocess
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.join(PROJECT_DIR, "cloud_app")
CACHE_FILES = ["bf_odds_today.json", "tips_today.json", "speed_stride_today.json",
               "results_ledger.csv", "our_system_forward_ledger.csv"]
# A running bet book has no "today" snapshot - it is publishable at any time.
RUNNING_BOOKS = {"our_system_forward_ledger.csv"}


def git(*args):
    """Run a git command inside the cloud_app repo without prompting for input."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="")
    return subprocess.run(
        ["git", *args], cwd=REPO_DIR, capture_output=True, text=True, env=env
    )


def summarize(file_name):
    """Return (date, count_label, count) for a cache file, or (None, None, None)."""
    path = os.path.join(REPO_DIR, file_name)
    if not os.path.exists(path):
        return None, None, None

    if file_name.endswith(".csv"):
        # A CSV cache: prefer rows dated today (the settlement ledger), otherwise
        # publish on its latest date (the forward book is a running record).
        today = datetime.date.today().isoformat()
        rows_today = total = 0
        latest = ""
        try:
            with open(path, newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    total += 1
                    day = str(row.get("race_date") or row.get("Date") or "")[:10]
                    if day:
                        latest = max(latest, day)
                        if day == today:
                            rows_today += 1
        except OSError as exc:
            print(f"[WARN] {file_name}: could not be read ({exc})")
            return None, None, None
        if rows_today:
            return today, "rows for today", rows_today
        if total:
            return latest, "rows (running book)", total
        return None, None, None

    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (ValueError, OSError) as exc:
        print(f"[WARN] {file_name}: could not be read ({exc})")
        return None, None, None

    if not isinstance(payload, dict):
        return None, None, None

    if file_name == "bf_odds_today.json":
        win = payload.get("win") or payload.get("odds") or {}
        place = payload.get("place") or {}
        return payload.get("date"), "win/place runners", (len(win), len(place))
    if file_name == "tips_today.json":
        picks = payload.get("picks") or []
        return payload.get("date"), "tips picks", len(picks)
    rows = payload.get("rows") or []
    return payload.get("date"), "speed & stride rows", len(rows)


def main():
    today = datetime.date.today().isoformat()
    print("=" * 75)
    print("  PUBLISH CLOUD CACHES - cloud_app -> GitHub (Streamlit Cloud)")
    print(f"  Date: {today} | Repo: {REPO_DIR}")
    print("=" * 75)

    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        print(f"[WARN] {REPO_DIR} is not a git repository - nothing published.")
        return 1

    stale = []
    missing = []
    for file_name in CACHE_FILES:
        file_date, label, count = summarize(file_name)
        if file_date is None:
            missing.append(file_name)
            print(f"[WARN] {file_name}: missing or unreadable")
        elif file_date != today and file_name not in RUNNING_BOOKS:
            stale.append(file_name)
            print(f"[WARN] {file_name}: dated {file_date} (not {today}) - skipping")
        else:
            print(f"[OK]   {file_name}: dated {file_date}, {label}={count}")

    publishable = [
        f
        for f in CACHE_FILES
        if f not in stale and f not in missing
    ]
    if not publishable:
        print("[WARN] No cache files are dated today - nothing published.")
        print("[WARN] Check that build_all_selections_cache.py ran successfully.")
        return 1

    add = git("add", *publishable)
    if add.returncode != 0:
        print(f"[WARN] git add failed: {add.stderr.strip()}")
        return 1

    staged = git("diff", "--cached", "--quiet")
    if staged.returncode == 0:
        print(f"[OK] Caches already published for {today} - nothing to commit.")
        return 0

    names = git("diff", "--cached", "--name-only").stdout.split()
    print(f"Staged for commit: {', '.join(names) if names else '(none)'}")

    commit = git("commit", "-m", f"chore: refresh daily caches ({today})")
    if commit.returncode != 0:
        print(f"[WARN] git commit failed: {commit.stderr.strip()}")
        return 1
    print(f"[OK] Committed: {commit.stdout.splitlines()[0] if commit.stdout.strip() else 'done'}")

    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    push = git("push", "origin", branch)
    if push.returncode != 0:
        print(f"[WARN] git push to {branch} failed: {push.stderr.strip()}")
        print("[WARN] The commit is safe locally - re-run this script to retry the push.")
        return 1
    print(f"[OK] Pushed to origin/{branch} - Streamlit Cloud will redeploy in ~1-2 min.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
