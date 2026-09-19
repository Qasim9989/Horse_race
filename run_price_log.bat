@echo off
cd /d E:\Test\racing-form-system
echo ============================================================
echo   PRICE LOG - today's runners and log sheet
echo ============================================================
echo.
echo [1/2] Scraping today's racecards from RacingTV ...
python scripts\racecard_today.py
echo.
echo [2/2] Building today's price log sheet ...
python scripts\price_log.py new
echo.
echo ============================================================
echo   DONE.  Open the file in the price_log folder:
echo     - type your BEST bookmaker price into BookPrice
echo     - type the BETFAIR price (same moment) into BetfairPrice
echo   Then run report.bat to see if you have an edge.
echo ============================================================
pause
