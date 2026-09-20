@echo off
cd /d E:\Test\racing-form-system
title Cloud odds snapshots - GitHub to SQL
echo ============================================================
echo   CLOUD ODDS SNAPSHOTS  (GitHub -^> PRODB.dbo.SnapshotOdds)
echo ============================================================
echo.
echo   The GitHub workflows capture the odds on their own servers
echo   (morning, hourly, T-15..T-1, BSP), because your SQL Server is
echo   LocalDB on this machine and cannot be reached from the cloud.
echo   This moves what they captured into SQL whenever you run it -
echo   it reads the fetched ref, so your local files are untouched.
echo.
python scripts\import_snapshots_to_sql.py --from-git
echo.
echo   Show what has arrived, by capture point:
echo     python -c "import pyodbc,pandas as pd; c=pyodbc.connect(r'Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;Database=PRODB;Trusted_Connection=yes;'); print(pd.read_sql('SELECT RaceDate, CapturePoint, COUNT(*) n FROM dbo.SnapshotOdds GROUP BY RaceDate, CapturePoint ORDER BY 1,2',c).to_string(index=False))"
echo.
echo   This also runs automatically as step 4b of the daily pipeline.
echo ============================================================
pause
