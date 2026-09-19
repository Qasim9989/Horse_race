@echo off
cd /d E:\Test\racing-form-system
title Racing odds dashboard
echo ============================================================
echo   RACING ODDS DASHBOARD
echo   Bookmaker prices vs real Betfair SP
echo ============================================================
echo.
if "%~1"=="fresh" goto fresh
choice /c YN /m "Take a fresh price snapshot before opening (Y/N)"
if errorlevel 2 goto open
:fresh
echo.
echo Taking a price snapshot from RacingTV (about 60 seconds) ...
python scripts\book_odds.py snapshot
python scripts\book_odds.py pricelog
:open
echo.
echo ============================================================
echo   Opening http://localhost:8501
echo   Close this window (or press Ctrl+C) to stop the dashboard.
echo ============================================================
python -m streamlit run scripts\dashboard.py --server.port 8501 --browser.gatherUsageStats false
pause
