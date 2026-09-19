@echo off
title B2L Back Sheet Scanner
echo ============================================================
echo  B2L HIGH-ODDS VALUE SCANNER (Back / Each-Way / B2L)
echo ============================================================
echo.

cd /d E:\Test\racing-form-system

if "%1"=="" (
    echo Scanning for TODAY...
    python b2l\b2l_scanner.py today
) else (
    echo Scanning for: %1
    python b2l\b2l_scanner.py %1
)

echo.
echo Done! Check reports\ folder for B2L_Sheet_YYYY-MM-DD.xlsx
pause
