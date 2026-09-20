@echo off
setlocal
cd /d E:\Test\racing-form-system\cloud_app
title RacingSystem intraday odds capture

rem  For a timer, run it without a console window at all:
rem
rem    schtasks /create /tn "RacingSystem_IntradayCapture" ^
rem      /tr "pythonw.exe E:\Test\racing-form-system\cloud_app\intraday_capture.py --bsp-window 2 --log capture.log" ^
rem      /sc minute /mo 1 /st 12:00 /du 10:30 /f
rem
rem    schtasks /delete /tn "RacingSystem_IntradayCapture" /f
rem
rem  Every minute, 12:00 for 10.5 hours.  pythonw.exe = no window; the run is
rem  logged to capture.log.  Costs one request when nothing is due and a handful
rem  when a capture point is open, and it is idempotent, so it can run beside the
rem  cloud workflow without duplicating captures.

echo.
echo   intraday odds capture - %DATE% %TIME%
echo   ------------------------------------------------------------
python -u intraday_capture.py --bsp-window 2 %*
echo   ------------------------------------------------------------
echo.
echo   preview without capturing:
echo     python intraday_capture.py --dry-run
echo   test the schedule at a set time:
echo     python intraday_capture.py --now 13:45 --dry-run
