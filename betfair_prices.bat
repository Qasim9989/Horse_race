@echo off
cd /d E:\Test\racing-form-system
title Betfair live prices
echo ============================================================
echo   BETFAIR LIVE PRICES  (uses the saved app key)
echo     1. check the login works
echo     2. store live back/lay for every runner -> dbo.BetfairLive
echo     3. compare with your best bookmaker price -> live_edge
echo ============================================================
echo.
python scripts\betfair_api.py login
if errorlevel 1 goto credfix
echo.
python scripts\betfair_api.py snapshot
echo.
python scripts\book_odds.py snapshot --limit 38
echo.
python scripts\live_edge.py
goto end
:credfix
echo.
echo   Login failed - check the stored credentials:
echo       python scripts\betfair_creds.py --diagnose
:end
echo.
echo ============================================================
echo   AFTER RACING (about 21:00) run this to pull the SP:
echo       python scripts\betfair_api.py sp
echo       python scripts\book_odds.py report --book BEST
echo ============================================================
pause
