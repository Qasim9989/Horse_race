"""For races where every row is still unfilled, why don't the names match?"""
import sys

import pyodbc

sys.path.insert(0, r"E:\Test\racing-form-system\scripts")
import backfill_weights_api as b

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=SCRAPED_PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=60;")
cur = c.cursor()
cur.execute("""
    SELECT TOP 5 CONVERT(char(10), RaceDate, 120), CourseName,
           CONVERT(varchar(10), RaceTime)
    FROM dbo.Scraped_Results
    WHERE RaceDate BETWEEN '2021-01-01' AND '2026-09-14'
    GROUP BY CONVERT(char(10), RaceDate, 120), CourseName,
             CONVERT(varchar(10), RaceTime)
    HAVING COUNT(Weight) = 0
    ORDER BY CONVERT(char(10), RaceDate, 120)
""")
for d, course, t in cur.fetchall():
    hhmm = str(t)[:5].replace(":", "")
    cur.execute("SELECT HorseName FROM dbo.Scraped_Results WHERE "
                "RaceDate=? AND CourseName=? AND RaceTime=?",
                (d, course, t))
    db_names = [x[0] for x in cur.fetchall()]
    races = b.rtv_api.day_races(d)
    hit = next((r for r in races if r["hhmm"] == hhmm
                and b.course_match(course, r)), None)
    print(f"\n{d} {course} {hhmm}: db_rows={len(db_names)} "
          f"api_race={'found' if hit else 'NOT FOUND'}")
    if not hit:
        times = [r["hhmm"] for r in races]
        print(f"   api times that day: {times[:10]}")
        continue
    api_rows = [r for r in b.rtv_api.runners_of(
        b.rtv_api.race_detail(d, hit["course_slug"], hit["hhmm"]))]
    api_names = [r["horse_name"] for r in api_rows]
    dbk = {b.norm_name(n) for n in db_names}
    apik = {b.norm_name(n) for n in api_names}
    print(f"   api_runners={len(api_names)} matched={len(dbk & apik)}")
    for n in db_names[:4]:
        print(f"     db : {n!r:<32} -> {b.norm_name(n)!r}")
    for n in api_names[:4]:
        print(f"     api: {n!r:<32} -> {b.norm_name(n)!r}")
c.close()
