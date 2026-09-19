@echo off
cd /d E:\Test\racing-form-system
title Price watch - how fast do the accounts move?
echo ============================================================
echo   PRICE WATCH
echo   Snapshots every 60s so the repricing rate can be measured.
echo   Best run in the 30 minutes before a race, when books move
echo   hardest. Leave this window open.
echo ============================================================
echo.
set MINS=%~1
if "%MINS%"=="" set MINS=30
python scripts\price_watch.py %MINS% --every 60
echo.
echo   Snapshots are in PRODB.dbo.BookOdds - see the 'Movement' tab
echo   in dashboard.bat for the charts.
pause
