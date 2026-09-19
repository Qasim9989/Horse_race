@echo off
chcp 65001 > nul
title Racing Form System - High-Speed Daily DB Updater
color 0A
echo ===========================================================================
echo      RACING FORM SYSTEM - DAILY DB UPDATER (SMART AUTO-RESUME)
echo ===========================================================================
echo.
echo Database: SCRAPED_PRODB
echo Mode: Auto-detects latest date in database and updates new/today's races.
echo Parallel Workers: 5 Browser Tabs
echo.

python -u E:\Test\racing-form-system\scripts\racingtv_db_updater.py

echo.
echo ===========================================================================
echo Update complete! Latest races and sectionals are saved in SCRAPED_PRODB.
echo ===========================================================================
pause
