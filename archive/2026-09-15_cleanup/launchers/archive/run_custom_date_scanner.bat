@echo off
chcp 65001 > nul
title Historical Date Auditor & CSV Exporter
color 0E
echo ===========================================================================
echo            HISTORICAL DATE AUDITOR ^& CSV/EXCEL EXPORTER
echo ===========================================================================
echo.
echo You can enter ANY date (e.g. 2026-08-13, 2026-08-12, 2025-07-20)
echo to inspect what happened, check zero-lookahead lay bets, and export CSV/Excel.
echo.
set /p RDATE="Enter race date (YYYY-MM-DD) [or press ENTER for 2026-08-13]: "

if "%RDATE%"=="" set RDATE=2026-08-13

echo.
echo Running pre-race audit and generating report for %RDATE%...
echo.

python -u E:\Test\racing-form-system\scripts\custom_date_audit.py %RDATE%

echo.
echo ===========================================================================
echo Audit completed! The CSV and Excel reports are saved in the 'reports' folder.
echo ===========================================================================
pause
