@echo off
chcp 65001 > nul
title RacingTV Real Online Scraper
color 0A
cls
echo ===========================================================================
echo            RACINGTV REAL ONLINE SCRAPER ^& DB BUILDER
echo ===========================================================================
echo.
echo Enter the start date you want to scrape from (YYYY-MM-DD),
echo or press ENTER to default to: 2025-01-01
echo.
set /p START_DATE="Start Date (e.g. 2025-01-01): "
if "%START_DATE%"=="" set START_DATE=2025-01-01

echo.
echo Enter the end date you want to scrape up to (YYYY-MM-DD),
echo or press ENTER to default to: today
echo.
set /p END_DATE="End Date (e.g. 2026-08-17): "
if "%END_DATE%"=="" set END_DATE=2026-08-17

echo.
echo ===========================================================================
echo Scraping online data from %START_DATE% to %END_DATE%...
echo ===========================================================================
echo.

python -u scripts\racingtv_db_updater.py %START_DATE% %END_DATE%

echo.
echo ===========================================================================
echo Done! Data saved to SCRAPED_PRODB.
echo ===========================================================================
pause
