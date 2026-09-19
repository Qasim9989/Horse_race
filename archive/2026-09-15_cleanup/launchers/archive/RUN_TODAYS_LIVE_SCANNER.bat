@echo off
TITLE Racing Form Master - High Speed 5-Worker Live Scanner
cls
echo ===========================================================================
echo   RACING FORM MASTER -- LIVE DAILY SCANNER (5 PARALLEL WORKERS)
echo ===========================================================================
echo.
echo Running High-Speed Scanner on Today's Live Cards (Database: RACINGTV_2023_2026)...
echo.
python scripts\daily_lay_scanner.py today
python b2l\b2l_scanner.py today
echo.
echo ===========================================================================
echo   SCAN COMPLETED! REPORTS SAVED TO E:\Test\racing-form-system\reports
echo ===========================================================================
pause
