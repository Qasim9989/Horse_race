@echo off
REM Runs every 15 minutes while you are logged on (Task Scheduler, "run only when
REM user is logged on" - so it simply stops when the laptop is shut).
REM
REM   1. results: scrapes whatever finished 30+ minutes ago, syncs, settles
REM   2. prices:  writes at most one Betfair snapshot per hour
REM
REM Register once (15-minute timer):
REM   schtasks /create /tn "Race results auto update" /tr "E:\Test\racing-form-system\cloud_app\auto_update.bat" /sc minute /mo 15 /f
REM
REM Task Scheduler will not start a second copy while one is still running, so a
REM slow scrape can never stack up behind the next tick.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo [%DATE% %TIME%] ---- auto update ---- >> auto_update.log
python results_refresh.py >> auto_update.log 2>&1
python price_snapshot.py --hourly >> price_snapshot.log 2>&1
