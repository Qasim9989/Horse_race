@echo off
setlocal
cd /d E:\Test\racing-form-system
set WHAT=%1
set LIMIT=%2
set LIMITARG=
if not "%LIMIT%"=="" set LIMITARG=--limit %LIMIT%

echo ============================================================
echo   SELECTION PRICE LOG
echo   What price could we ACTUALLY have taken, and did it hold?
echo ============================================================
echo.
echo   WHERE THE BETFAIR CREDENTIALS LIVE - not in this repo:
echo     User environment variables  BETFAIR_APP_KEY / USERNAME / PASSWORD
echo     fallback copy               E:\CGMBET\betfair_api_config.json
echo   Both copies must hold the SAME values or the env one silently
echo   wins.  Check any time with:  log_selections.bat check
echo.

if /i "%WHAT%"=="check"   goto check
if /i "%WHAT%"=="refresh" goto refresh
if /i "%WHAT%"=="capture" goto capture
if /i "%WHAT%"=="picks"   goto picks
if /i "%WHAT%"=="sweep"   goto sweep
if /i "%WHAT%"=="settle"  goto settle
if /i "%WHAT%"=="report"  goto report

echo   Usage: log_selections.bat [picks^|sweep^|refresh^|capture^|check^|settle^|report] [flag or limit]
echo.
echo     picks    build picks.csv from today's sheet  (do this first, daily)
echo     sweep    refresh both price sources, then log them   (run 3x a day)
echo     refresh  take the snapshots only: bookmaker + Betfair
echo     capture  log what is already in the database  (no site contact)
echo     check    are the Betfair credentials present and consistent?
echo     settle   fill in BSP / SP / result   (re-run until nothing pending)
echo     report   the five tables
echo.
echo   capture, settle and report make NO requests to the site or to
echo   Betfair - they only read the database.  refresh and sweep run the
echo   existing fetchers once each, sequentially, with their normal delays.
goto end

:check
echo   Credentials as the scripts see them (values are masked):
echo.
python scripts\betfair_creds.py --diagnose
goto end

:refresh
echo   [1/3] Checking the Betfair login first - a broken login is the
echo         usual reason BetfairLive stops filling up.
echo.
python scripts\betfair_creds.py --diagnose
echo.
echo   [2/3] Bookmaker prices  (your site, one sequential sweep)
echo         %LIMITARG%
python scripts\book_odds.py snapshot %LIMITARG%
echo.
echo   [3/3] Betfair live back/lay  (api.betfair.com, one sweep)
python scripts\betfair_api.py snapshot
goto end

:picks
echo   Building reports\picks.csv from the day's own racecard sheet
echo   (reports\selections_YYYY-MM-DD.csv).  Which flag counts as a
echo   selection can be changed:  log_selections.bat picks SEL_HARD
echo.
python scripts\selection_price_log.py picks --flag %2
goto end

:sweep
echo   Full sweep: refresh both sources, then write one timestamped row
echo   per selection.  Run it morning, midday and close to the off.
echo.
echo   (First run of the day: log_selections.bat picks, so picks.csv holds
echo   today's flagged selections rather than yesterday's.)
echo.
call "%~f0" refresh %2
echo.
echo   [4/4] Logging what is now in the database
echo.
python scripts\selection_price_log.py capture --selections reports\picks.csv
goto end

:capture
echo   Logging what is already in the database for the picks in
echo   reports\picks.csv.  Adds one timestamped row per selection.
echo.
python scripts\selection_price_log.py capture --selections reports\picks.csv
echo.
echo   If a row has no price and no Betfair back, that source has not
echo   been refreshed yet - run: log_selections.bat refresh
goto end

:settle
echo   Filling in BSP, SP and finishing position.  Safe to re-run: races
echo   whose result is not published yet stay pending and are picked up
echo   next time.  Nothing is ever deleted.
echo.
python scripts\selection_price_log.py settle
goto end

:report
echo   The five tables that decide how - and when - to bet.
echo.
python scripts\selection_price_log.py report
goto end

:end
echo.
echo ============================================================
echo   Log file: reports\selection_prices.csv
echo     sweep -^> prices   settle -^> results   report -^> verdict
echo ============================================================
pause
