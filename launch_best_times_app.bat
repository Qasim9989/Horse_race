@echo off
cd /d "E:\Test\racing-form-system"
title HR Best Times & Telemetry Software
echo ============================================================
echo   LAUNCHING HR BEST TIMES & TELEMETRY SOFTWARE
echo   Local Web App running on http://localhost:8505
echo ============================================================
echo.
call venv\Scripts\activate.bat
start "" http://localhost:8505
python -m streamlit run scripts\best_times_app.py --server.port 8505 --server.headless false
pause
