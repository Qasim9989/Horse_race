TIDY-UP 2026-09-15
==================
Nothing here was deleted. MANIFEST.csv lists every item that moved - 237 rows,
733 MB - with its old path, new path and size, so anything can be put back.
(scratch/scratch_fix_manifest.py rebuilt the manifest after the first run was
interrupted; it re-checks that no original path is still in place.)

What moved and why
------------------
scratch/           24 one-off exploration scripts from the session that found
                   the RacingTV odds API (probe scripts, db checks, scans).
                   They served their purpose; the findings are written up in
                   ODDS_API_FINDING.md. The scratch_cleanup.py script that
                   performed this tidy-up is here too.
probe_logs/        ~140 console logs and captured API dumps (reports/_*.log,
                   reports/_*.err, reports/_api*) from that same session.
root_legacy/       dump_cols.py + rdb_cols.txt (one-off column dump and the
                   file it generates), gpt_coder.py, and the .aider.* chat
                   history files. Referenced by nothing.
data_generated/    ~722 MB of JSON exported for the old dashboard
                   (prodb-flat-handicaps*.json, historical-races*.json) plus
                   data/excel_raw. Nothing references them, and FRESH_NOTE.md
                   already says not to use the old dashboard's generated JSON.
                   SAFE TO DELETE THIS FOLDER to reclaim ~735 MB.
launchers/         .bat files for the scanners that are themselves archived
                   (run_today_scanner, run_5year_audit, b2l, etc.).

Two test scripts were kept, not archived
----------------------------------------
scratch_test_compare.py   -> tests/test_book_odds.py
scratch_test_dashboard.py -> tests/test_dashboard.py

Also removed: the root __pycache__/ folder (bytecode only, for modules that had
already been archived, so it can never be imported again - Python regenerates
its own).

Kept at the project root on purpose
-----------------------------------
may-aug26.xlsx      still read by scripts/bulk_update_from_excel.py
export_daily_card.py   standalone card exporter, no dependencies on it
README.md           rewritten on this date: it had been advertising the Master
                    Lay and B2L systems, which are archived, and quoting their
                    +19.7% / +24.8% ROIs, which the BSP-margin audit overturned.
