"""Can SData join to BFSP (prices/results)? Check the keys."""
import pyodbc

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=180;"
                   "MultipleActiveResultSets=True;")
cur = c.cursor()


def cols(t):
    cur.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION", (t,))
    return cur.fetchall()


print("NEW_C cols:", [n for n, _ in cols("NEW_C")])
print("\nNEW_RH cols:", [n for n, _ in cols("NEW_RH")][:24])
print("\nNEW_H name fields:",
      [n for n, _ in cols("NEW_H") if "Name" in n])

print("\nSData sample joined to race + horse names:")
cur.execute("""
    SELECT TOP 6 RH.RH_DateTime, RH.RH_CNo, RH.RH_TimeInSeconds,
           H.H_Name, H.H_Name_No_Anything, S.SD_RNo, S.SD_HNo,
           S.STRK_Finish, S.SL_Finish, S.STDIFF_Finish, S.SPOS_Finish
    FROM dbo.SData S
    JOIN dbo.NEW_RH RH ON RH.RH_RNo = S.SD_RNo
    JOIN dbo.NEW_H  H  ON H.H_No  = S.SD_HNo
    ORDER BY RH.RH_DateTime DESC
""")
for r in cur.fetchall():
    print("   ", r)

print("\ndoes BFSP carry the same horse cleaned names?")
cur.execute("SELECT TOP 5 HorseClean, CourseClean, RaceDate, RaceTime "
            "FROM dbo.BFSP WHERE RaceDate >= '2026-06-01'")
for r in cur.fetchall():
    print("   ", r)
c.close()
