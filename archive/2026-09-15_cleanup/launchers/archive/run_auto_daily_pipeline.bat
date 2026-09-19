@echo off
chcp 65001 > nul
title Racing Form System - Master Auto Daily Pipeline
color 0E

echo ===========================================================================
echo            RACING FORM SYSTEM - MASTER AUTO DAILY PIPELINE
echo ===========================================================================
echo.
echo 1. Updating database with latest results and sectionals...
echo 2. Scanning today's racecards and generating Lay Sheet...
echo.

python -u E:\Test\racing-form-system\scripts\auto_daily_pipeline.py

echo.
echo ===========================================================================
echo Pipeline finished. Selections saved to database and Excel!
echo ===========================================================================
pause
