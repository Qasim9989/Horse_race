"""What Ben's five rules need vs what the API/DB can supply.

  rule 1 mark falling      OR_now < OR last time out
  rule 2 below last win    OR_now < OR when it last won
  rule 3 below career best OR_now < career-high OR
  rule 4 proven at trip    prior 1st-3rd over the same exact distance
  rule 5 ran top 4 LTO     finished 1-4 last time out

OR_now comes off today's racecard.  Rules 1-3 need a HISTORICAL mark series and
rule 4 needs HISTORICAL distances - both were coming from PRODB, frozen
2026-05-22.  This checks whether the RacingTV API can supply the replacements.
"""
import sys

sys.path.insert(0, r"E:\Test\racing-form-system\scripts")
import rtv_api as api

for date_str in ("2021-06-15", "2024-04-13", "2026-09-13"):
    races = api.day_races(date_str)
    band = tf = runners = 0
    for r in races[:12]:
        d = api.race_detail(date_str, r["course_slug"], r["hhmm"])
        race = d.get("race") or {}
        if race.get("rating_limit") and race.get("rating_limit_range"):
            band += 1
        for run in api.runners_of(d):
            runners += 1
            if run.get("timeform_rating"):
                tf += 1
    print(f"{date_str}: of {min(len(races), 12)} races checked -> "
          f"{band} carry a RATING BAND, runner timeform ratings "
          f"{tf}/{runners}")
    if races:
        d = api.race_detail(date_str, races[0]["course_slug"],
                            races[0]["hhmm"])
        race = d.get("race") or {}
        run = api.runners_of(d)[0]
        print(f"    band={race.get('rating_limit_range')!r} "
              f"distance={race.get('distance')!r} "
              f"formatted={race.get('distance_formatted')!r} "
              f"class={race.get('race_class')!r} type={race.get('race_type')!r}")
        print(f"    runner: tf={run.get('timeform_rating')!r} "
              f"form={run.get('form')!r} days={run.get('days_since_run')!r} "
              f"sp={run.get('starting_price')!r}")
