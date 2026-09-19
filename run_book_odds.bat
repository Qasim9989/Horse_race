@echo off
cd /d E:\Test\racing-form-system
echo ============================================================
echo   BOOK ODDS - every bookmaker's price, straight from the
echo   RacingTV / Oddschecker JSON feed (no browser, no API key)
echo ============================================================
echo.
if "%~1"=="tomorrow" goto tomorrow
echo [1/3] Snapshotting TODAY's bookmaker prices (12 books) ...
python scripts\book_odds.py snapshot
echo.
echo [2/3] Building the price log sheet (no typing needed) ...
python scripts\book_odds.py pricelog
echo.
echo [3/3] Book-vs-Betfair report ...
python scripts\book_odds.py report
goto end
:tomorrow
for /f %%i in ('python -c "import datetime;print((datetime.date.today()+datetime.timedelta(days=1)).isoformat())"') do set TOM=%%i
echo [1/1] Snapshotting %TOM% (prices may not be published yet) ...
python scripts\book_odds.py snapshot %TOM%
:end
echo.
echo ============================================================
echo   Data is in PRODB.dbo.BookOdds (timestamped snapshots).
echo   Compare with Betfair SP in PRODB.dbo.BFSP:
echo      python scripts\book_odds.py report YYYY-MM-DD --book "Paddy Power"
echo   Run this again in the EVENING to build a price-movement history.
echo.
echo   VISUAL VIEW: double-click  dashboard.bat
echo ============================================================
pause
