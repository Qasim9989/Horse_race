@echo off
chcp 65001 > nul
title Today's Master Racecard ^& Lay Scanner
color 0A
echo ===========================================================================
echo            TODAY'S MASTER RACECARD ^& LAY SCANNER
echo ===========================================================================
echo.
echo Scraping today's live UK and Irish handicap racecards...
echo Cross-referencing sectionals, stride decay, and pace...
echo.

python -u E:\Test\racing-form-system\scripts\daily_lay_scanner.py today

echo.
echo ===========================================================================
echo Scan complete. Lay ONLY when Betfair Starting Price is 6.0 or lower.
echo ===========================================================================
pause
