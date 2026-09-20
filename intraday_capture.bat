@echo off
setlocal
cd /d E:\Test\racing-form-system\cloud_app
title RacingSystem intraday odds capture

rem  Runs every minute during racing hours (see the schtasks line below).
rem  Costs one RacingTV request when nothing is due, and a handful when a
rem  capture point is open.  Idempotent, so it can run beside the cloud job.

echo.
echo   intraday odds capture - %DATE% %TIME%
echo   ------------------------------------------------------------
python -u intraday_capture.py --bsp-window 2 %*
echo.
echo   ------------------------------------------------------------
echo   tip: preview without capturing
echo        python intraday_capture.py --dry-run
echo.
echo   register the timer (every minute, 12:00 for 10.5 hours):
echo     schtasks /create /tn "RacingSystem_IntradayCapture" ^
echo       /tr "E:\Test\racing-form-system\cloud_app\intraday_capture.bat" ^
echo       /sc minute /mo 1 /st 12:00 /du 10:30 /f
echo   remove it with:
echo     schtasks /delete /tn "RacingSystem_IntradayCapture" /f
