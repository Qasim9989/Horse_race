@echo off
cd /d E:\Test\racing-form-system
title Finish the BSP import
echo ============================================================
echo   IMPORT BETFAIR SP FOR A RACE DAY
echo.
echo   Betfair publishes a day's prices in the NEXT day's file,
echo   so today's races can only be imported the following day.
echo ============================================================
echo.
set RACEDATE=%~1
if "%RACEDATE%"=="" set /p RACEDATE="Race date YYYY-MM-DD (blank = today): "
if "%RACEDATE%"=="" for /f %%i in ('python -c "import datetime;print(datetime.date.today().isoformat())"') do set RACEDATE=%%i
echo.
echo Checking whether the SP for %RACEDATE% has been published ...
echo.
python scripts\wait_for_bsp.py %RACEDATE% --check
if errorlevel 1 goto notyet
echo Importing ...
python scripts\wait_for_bsp.py %RACEDATE%
goto end
:notyet
echo.
echo ------------------------------------------------------------
echo   That file is not published yet - re-running will not help.
echo   Betfair only creates it on the day AFTER racing.
echo ------------------------------------------------------------
echo.
choice /c YN /m "Leave this window waiting in the background (Y/N)"
if errorlevel 2 goto end
python scripts\wait_for_bsp.py %RACEDATE%
:end
echo.
pause
