"""What are the last ~170 races without weights, and does the API have them?"""
import collections
import sys

import pyodbc

sys.path.insert(0, r"E:\Test\racing-form-system\scripts")
import rtv_api as api

c = pyodbc.connect("Driver={ODBC Driver 17 for SQL Server};"
                   "Server=(localdb)\\MSSQLLocalDB;Database=SCRAPED_PRODB;"
                   "Trusted_Connection=yes;Connection Timeout=60;")
cur = c.cursor()
cur.execute("""
    SELECT CONVERT(char(10), RaceDate, 120) AS d, CourseName,
           CONVERT(varchar(10), RaceTime) AS t
    FROM dbo.Scraped_Results
    WHERE RaceDate BETWEEN '2021-01-01' AND '2026-09-14' AND Weight IS NULL
    GROUP BY CONVERT(char(10), RaceDate, 120), CourseName,
             CONVERT(varchar(10), RaceTime)
    ORDER BY d
""")
rows = cur.fetchall()
print(f"{len(rows)} races without weights")

by_year = collections.Counter(r[0][:4] for r in rows)
print("by year:", dict(sorted(by_year.items())))
by_course = collections.Counter(r[1] for r in rows)
print("top courses:", by_course.most_common(12))
c.close()

# does the API know these races at all?
checked = 0
missing = 0
for d, course, t in rows:
    if checked >= 10:
        break
    try:
        races = api.day_races(d)
    except Exception as e:                                     # noqa: BLE001
        print(f"  {d} {course} {t}: day_races failed {type(e).__name__}")
        continue
    hit = [r for r in races if r["hhmm"] == str(t)[:5].replace(":", "")[:4]]
    checked += 1
    if not races:
        missing += 1
        print(f"  {d} {course} {t}: API has NO races for that date")
    elif not hit:
        print(f"  {d} {course} {t}: API date has {len(races)} races but none "
              f"at that time ({[r['hhmm'] for r in races][:6]})")
    else:
        r0 = hit[0]
        print(f"  {d} {course} {t}: API has it as {r0['course_slug']} "
              f"'{r0['course_name']}' - COURSE MATCH PROBLEM")
print(f"checked {checked}, dates the API does not serve: {missing}")
