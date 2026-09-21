@echo off
REM Hourly Betfair price snapshots into cloud_app\racing_form.db
REM Register once (fires every 30 minutes, writes at most once per hour):
REM   schtasks /create /tn "Betfair hourly prices" /tr "E:\Test\racing-form-system\cloud_app\price_snapshot_hourly.bat" /sc minute /mo 30 /f
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python price_snapshot.py --hourly >> price_snapshot.log 2>&1
