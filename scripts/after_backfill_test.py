r"""
WAIT FOR THE BACKFILL, THEN RUN THE TWO-FEED TEST
=================================================
The RaceIQ backfill chain writes "BACKFILL COMPLETE" to its log when the last
window (2023) is done.  Nightly runs of the comparison before that are wasted:
the 2025 window is what supplies the previous-run priors for the 2026 test
window, and re-running mid-backfill produced numbers that moved between runs.

So this waits for the marker, then runs the dual-source study over the settled
data and writes reports\_two_feeds_after_backfill.txt.  Read-only: the study
only reads, and the only file written is that report.

    python scripts\after_backfill_test.py
    python scripts\after_backfill_test.py --max-hours 14 --poll-seconds 600
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
DEFAULT_LOG = os.path.join(os.environ.get("TEMP", ""), "cline_diag", "chart2.log")
REPORT = os.path.join(PROJECT, "reports", "_two_feeds_after_backfill.txt")
STUDY = os.path.join(HERE, "compare_sources_speed_stride.py")
MARKER = "BACKFILL COMPLETE"


def marker_seen(log_path: str) -> bool:
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            return MARKER in handle.read()
    except OSError:
        return False


def last_line(log_path: str) -> str:
    """Where the chain is, so the watcher's own log is informative."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            lines = [ln.strip() for ln in handle if ln.strip()]
        return lines[-1][:90] if lines else "(empty log)"
    except OSError:
        return "(no log yet)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--max-hours", type=float, default=14.0)
    ap.add_argument("--poll-seconds", type=int, default=300)
    a = ap.parse_args()

    started = dt.datetime.now()
    print(f"watching {a.log} for '{MARKER}'", flush=True)
    while not marker_seen(a.log):
        waited = (dt.datetime.now() - started).total_seconds() / 3600
        if waited > a.max_hours:
            print(f"gave up after {waited:.1f}h", flush=True)
            return 1
        print(f"[{dt.datetime.now():%H:%M:%S}] {waited:.1f}h in :: "
              f"{last_line(a.log)}", flush=True)
        time.sleep(a.poll_seconds)

    print("backfill finished - running the two-feed test", flush=True)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as out:
        out.write(f"TWO-FEED TEST   run {dt.datetime.now():%Y-%m-%d %H:%M}\n")
        out.write(f"backfill marker seen {dt.datetime.now():%H:%M}\n")
        out.write("=" * 100 + "\n\n")
        out.flush()
        result = subprocess.run([sys.executable, STUDY], cwd=PROJECT,
                                capture_output=True, text=True, timeout=7200)
        out.write(result.stdout)
        if result.stderr:
            out.write("\n--- stderr ---\n" + result.stderr)
    print(f"wrote {os.path.basename(REPORT)} (study exit {result.returncode})",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
