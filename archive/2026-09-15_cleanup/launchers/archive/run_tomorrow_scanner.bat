@echo off
chcp 65001 > nul
title Tomorrow's Master Racecard ^& Lay Scanner
color 0B
echo ===========================================================================
echo           TOMORROW'S MASTER RACECARD ^& LAY SCANNER (ADVANCE VIEW)
echo ===========================================================================
echo.
echo Scraping tomorrow's published UK and Irish handicap racecards...
echo Cross-referencing sectionals, stride decay, and pace...
echo.

python -u E:\Test\racing-form-system\scripts\daily_lay_scanner.py tomorrow

echo.
echo ===========================================================================
echo Scan complete. Tomorrow's advance Lay Sheet generated!
echo ===========================================================================
pause
