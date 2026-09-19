@echo off
chcp 65001 > nul
title Racing Form System - 100%% Auto-Schedule Setup
color 0B

echo ===========================================================================
echo       RACING FORM SYSTEM - 100%% AUTOMATIC DAILY SCHEDULE SETUP
echo ===========================================================================
echo.
echo Registering Windows Scheduled Task:
echo   - Task Name: RacingSystem_DailyAutoPipeline
echo   - Schedule: Daily at 10:30 AM
echo   - Action: E:\Test\racing-form-system\run_auto_daily_pipeline.bat
echo.

schtasks /create /tn "RacingSystem_DailyAutoPipeline" /tr "E:\Test\racing-form-system\run_auto_daily_pipeline.bat" /sc daily /st 10:30 /f

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ===========================================================================
    echo [SUCCESS] Daily task is registered and ACTIVE in Windows Task Scheduler!
    echo Every day at 10:30 AM:
    echo   1. Database auto-updates with newly settled races
    echo   2. Today's Lay Sheet is automatically generated in:
    echo      E:\Test\racing-form-system\reports\
    echo ===========================================================================
) else (
    echo.
    echo [NOTE] If you encountered an error, right-click this file and select:
    echo        'Run as administrator'
)

echo.
pause
