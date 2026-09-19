@echo off
title 5-Year Historical Master Lay Audit (2021-2026)
cls
echo ===========================================================================
echo            5-YEAR HISTORICAL MASTER LAY AUDIT (2021 - 2026)
echo ===========================================================================
echo.
echo Running full 5-year SQL extraction, full-field ranking, and zero-lookahead audit...
echo.
python scripts\audit_5year_historical.py
echo.
echo ===========================================================================
echo Audit finished. Output saved to reports\Audit_5Year_Full_Results.csv
echo ===========================================================================
pause
