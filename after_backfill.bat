@echo off
REM ===================================================================
REM  Waits for the weight backfill, then verifies coverage, audits both
REM  the databases and the project, scraps dead tables and runs the tests.
REM
REM  The waiting is done in Python (scripts\wait_for_backfill.py) because
REM  detecting the process from a .bat is unreliable.
REM ===================================================================
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

python scripts\wait_for_backfill.py %*
