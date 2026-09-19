@echo off
title Compare Scraped Database vs Proform
cls
echo ===========================================================================
echo       SCRAPED DATABASE VS PROFORM (PRODB) 7-DIMENSIONAL COMPARISON
echo ===========================================================================
echo.
echo Running cross-validation on:
echo 1. 5-Year Race and Runner Volume
echo 2. Direct Horse Matching Rate
echo 3. Finishing Position and Winner Agreement
echo 4. SP and Decimal Odds Calibration
echo 5. In-Running Comment and Discipline Flags
echo.
python scripts\compare_scraped_vs_proform.py
echo.
echo ===========================================================================
echo Comparison complete. Output saved to reports\Database_Comparison_Report.xlsx
echo ===========================================================================
pause
