"""Is NEW_TPD_STRIDE one row per runner, or a time-series per runner?"""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=180;"
                   "MultipleActiveResultSets=True;")
cur = c.cursor()

print("=== per race: rows, runners, rows-per-runner ===")
cur.execute("""
    SELECT TOP 8 RH.RH_DateTime, S.RH_RNo, RH.RH_Name,
           COUNT(*) AS rows_, COUNT(DISTINCT S.HIR_HNo) AS horses
    FROM dbo.NEW_TPD_STRIDE S
    JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.RH_RNo
    GROUP BY RH.RH_DateTime, S.RH_RNo, RH.RH_Name
    ORDER BY COUNT(*) DESC
""")
for dt, rno, name, rows_, horses in cur.fetchall():
    per = rows_ / max(horses, 1)
    print(f"   {str(dt)[:10]}  race {rno:<7} {str(name)[:34]:<34} "
          f"rows {rows_:>6,}  horses {horses:>3}  -> {per:>6.1f} rows/horse")

cur.execute("""SELECT COUNT(DISTINCT RH_RNo), COUNT(DISTINCT HIR_HNo),
                      COUNT(*)
               FROM dbo.NEW_TPD_STRIDE""")
races, horses, rows_ = cur.fetchone()
print(f"\nTOTAL: {rows_:,} rows | {races} distinct races | {horses} distinct horses")

print("\n=== one horse inside one race: are these time slices? ===")
cur.execute("""
    SELECT TOP 6 RecordTime, HL16_PercentRaceLeft, HL15_DistanceToFinish,
           HL17_Position, HL11_StrideFrequency, HL6_SpeedMS
    FROM dbo.NEW_TPD_STRIDE
    WHERE RH_RNo = 104281
    ORDER BY RecordTime
""")
for r in cur.fetchall():
    print("   ", r)
c.close()
