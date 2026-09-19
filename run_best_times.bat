@echo off
setlocal
cd /d "E:\Test\racing-form-system"
call venv\Scripts\activate.bat
python scripts\best_times_analyzer.py %*
pause
