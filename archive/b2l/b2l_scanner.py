"""
B2L SCANNER — Back-to-Lay / Value Back High-Odds System
========================================================
Identifies HIGH-ODDS (20+) value horses in handicap races using REVERSED form signals.

Betting modes:
  WIN  — Straight back at 20+ (fixed stake)
  EW   — Each-Way at 20+ (1/4 odds, 3 places)
  B2L  — Back at 20+, lay in-running when going well (green up)

Scoring (opposite of LAY scanner):
  +3  Front Runner (pace L/P/F)
  +2  Quick return (DSLR <=7) or claiming jockey
  +2  Clean last run (no discipline issues)
  +2  Positive speed comment (led, ran on, headway, quickened, etc.)
  -3  Stride Decay >= 0.66ft (physically tiring)
  -2  Bad discipline (dwelt, slowly away, hung, erratic, keen)
  -1  Long absence (DSLR > 120)

Tier 1: score >= 4  (Top B2L/EW/Win Target)
Tier 2: score >= 2 + positive speed comment
Min odds hint: 20.0 (filter manually on exchange)
"""

# NOTE (2026-08-21):
# LTO features (pace, stride decay, DSLR, jockey claim, comments) are now loaded
# from PRODB using a strict LTO CTE, matching the logic in
# b2l_realistic_stress_test.py. The scanner now implements the full B2L system
# used in the +24% ROI backtest.

import sys
import os
import re
import datetime
import asyncio
import pyodbc
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

CONN_SCRAPED = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=(localdb)\MSSQLLocalDB;"
    r"Database=RACINGTV_2023_2026;"
    r"Trusted_Connection=yes;"
)

CONN_PROFORM = (
    r"Driver={ODBC Driver 17 for SQL Server};"
    r"Server=(localdb)\MSSQLLocalDB;"
    r"Database=PRODB;"
    r"Trusted_Connection=yes;"
)

MIN_ODDS = 20.0  # Target horses that will be 20+ on the exchange

UK_IRE_COURSES = {
    "aintree", "ascot", "ayr", "ballinrobe", "bangor-on-dee", "bath",
    "bellewstown", "beverley", "brighton", "carlisle", "cartmel",
    "catterick", "catterick-bridge", "chelmsford-city", "chelmsford", "cheltenham",
    "chepstow", "chester", "clonmel", "cork", "curragh", "doncaster",
    "down-royal", "downpatrick", "dundalk", "epsom", "exeter",
    "fairyhouse", "fakenham", "ffos-las", "folkestone", "fontwell",
    "galway", "goodwood", "gowran-park", "hamilton", "haydock",
    "hereford", "hexham", "huntingdon", "kelso", "kempton",
    "kempton-park", "kilbeggan", "killarney", "laytown", "leicester",
    "leopardstown", "limerick", "lingfield", "lingfield-park", "listowel",
    "ludlow", "market-rasen", "musselburgh", "naas", "navan",
    "newbury", "newcastle", "newmarket", "newton-abbot", "nottingham",
    "perth", "plumpton", "pontefract", "punchestown", "redcar",
    "ripon", "roscommon", "salisbury", "sandown", "sedgefield",
    "sligo", "southwell", "stratford", "taunton", "thirsk",
    "thurles", "tipperary", "towcester", "tramore", "uttoxeter",
    "warwick", "wetherby", "wexford", "wincanton", "windsor",
    "wolverhampton", "worcester", "yarmouth", "york",
}

# --- Speed / positive comment keywords ---
GOOD_SPEED_WORDS = [
    'led', 'led briefly', 'ran on', 'ran on well', 'ran on strongly',
    'kept on', 'kept on well', 'strong finish', 'headway', 'good headway',
    'quickened', 'quickened well', 'chased leaders', 'chased leader',
    'pushed along', 'driven out', 'made all', 'disputed lead', 'prominent',
    'stayed on', 'stayed on well', 'rallied', 'finished well',
]

# --- Bad discipline keywords ---
BAD_DISC_WORDS = [
    'slowly away', 'dwelt', 'pulled hard', 'keen', 'hung', 'erratic',
    'lost ground start', 'missed break', 'reared',
]


def clean_horse_name(name):
    if not name:
        return ""
    name = re.sub(r"\s*\([A-Z]{2,4}\)$", "", str(name), flags=re.IGNORECASE).strip()
    return "".join(c for c in name.lower() if c.isalnum())


def parse_target_date():
    today = datetime.date.today()
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip().lower()
        if arg in ["tomorrow", "tmr", "next"]:
            return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        elif arg in ["today", "now"]:
            return today.strftime("%Y-%m-%d")
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", arg):
            return arg
    return today.strftime("%Y-%m-%d")


async def block_resources_async(route):
    if route.request.resource_type in ["image", "media", "font"]:
        await route.abort()
    elif any(d in route.request.url for d in ["googletagmanager", "google-analytics", "doubleclick", "facebook"]):
        await route.abort()
    else:
        await route.continue_()


async def fetch_single_card(sem, context, url, course, rtime, target_date_str):
    async with sem:
        page = await context.new_page()
        try:
            await page.route("**/*", block_resources_async)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except Exception as e:
                print(f" Warning: Timeout loading card {course} {rtime}: {e}")
                return None
            try:
                await page.wait_for_selector("a[href*='/profiles/horse/'], a[href*='/horse/'], h1", timeout=6000)
            except:
                pass
            await asyncio.sleep(0.5)

            title_elem = page.locator("h1, h2, .race-title, [class*='raceTitle']").first
            race_title = await title_elem.inner_text() if await title_elem.count() > 0 else ""
            page_text = await page.inner_text("body")
            is_handicap = (
                "handicap" in page_text.lower()
                or "h'cap" in page_text.lower()
                or "handicap" in race_title.lower()
            )

            raw_horses = await page.eval_on_selector_all(
                "a[href*='/profiles/horse/'], a[href*='/horse/'], .horse-name, [class*='horseName']",
                "els => els.map(e => e.innerText)"
            )

            runners = []
            seen_h = set()
            for h in raw_horses:
                if not h:
                    continue
                first_line = h.split('\n')[0].strip()
                h_raw = re.sub(r"\s*\([A-Z]{2,4}\)$", "", first_line).strip()
                h_clean = clean_horse_name(h_raw)
                if not h_clean or h_clean in seen_h or len(h_clean) <= 2:
                    continue
                seen_h.add(h_clean)
                runners.append({'horse_raw': h_raw, 'horse_clean': h_clean})

            if runners:
                time_formatted = f"{rtime[:2]}:{rtime[2:]}"
                return {
                    'date': target_date_str,
                    'time': time_formatted,
                    'course': course.replace('-', ' ').title(),
                    'title': race_title.strip(),
                    'is_handicap': is_handicap,
                    'runners': runners
                }
            else:
                print(f" Warning: 0 runners found on {course} {rtime}")
                return None
        except Exception as e:
            print(f" Warning: Card error {course} {rtime}: {e}")
            return None
        finally:
            try:
                await page.close()
            except:
                pass


async def fetch_live_racecards_async(target_date_str):
    print(f"\n[1/3] Scraping live UK & Irish racecards for {target_date_str} (5 Parallel Workers)...")
    race_links = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", block_resources_async)

        try:
            await page.goto(f"https://www.racingtv.com/racecards/{target_date_str}", wait_until="domcontentloaded", timeout=25000)
            try:
                await page.locator("#onetrust-accept-btn-handler").click(timeout=2000)
            except:
                pass
            await asyncio.sleep(2.5)
            raw_links = await page.eval_on_selector_all(
                f"a[href*='/racecards/{target_date_str}/']",
                "els => els.map(e => e.href)"
            )
            for l in set(raw_links):
                m = re.search(rf"/racecards/{target_date_str}/([^/]+)/(\d{{4}})", l)
                if m:
                    course = m.group(1).lower()
                    rtime = m.group(2)
                    if course in UK_IRE_COURSES:
                        race_links.append((l, course, rtime))
        except Exception as e:
            print(f" Warning: Error fetching racecards list: {e}")
        finally:
            try:
                await page.close()
            except:
                pass

        print(f"Found {len(race_links)} UK/Irish racecards. Loading concurrently...")
        sem = asyncio.Semaphore(5)
        context = await browser.new_context()
        tasks = [fetch_single_card(sem, context, url, course, rtime, target_date_str) for url, course, rtime in race_links]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await context.close()
            await browser.close()
        except:
            pass

        valid_races = [r for r in results if r and isinstance(r, dict)]
        print(f"Successfully loaded {len(valid_races)} racecards.")
        return valid_races


def fetch_live_racecards(target_date_str):
    return asyncio.run(fetch_live_racecards_async(target_date_str))


def score_racecards_b2l(races, target_date_str):
    print("\n[2/3] Cross-referencing SCRAPED_PRODB and PRODB for B2L scoring...")

    conn_s = pyodbc.connect(CONN_SCRAPED)
    sql_scraped = f"""
    SELECT
        LOWER(HorseName) AS HorseName,
        RaceDate,
        Comment,
        JockeyClaim
    FROM dbo.Scraped_Results
    WHERE RaceDate < '{target_date_str}'
    ORDER BY RaceDate DESC;
    """
    df_scraped = pd.read_sql(sql_scraped, conn_s)
    conn_s.close()

    df_scraped['clean_horse'] = df_scraped['HorseName'].apply(clean_horse_name)
    scraped_lto = (
        df_scraped
        .drop_duplicates(subset=['clean_horse'], keep='first')
        .set_index('clean_horse')
        .to_dict('index')
    )

    target_rows = []
    for race in races:
        race_dt = pd.to_datetime(f"{race['date']} {race['time']}", errors='coerce')
        if pd.isna(race_dt):
            continue
        for r in race['runners']:
            target_rows.append({
                'target_datetime': race_dt,
                'horse_clean': r['horse_clean']
            })

    if not target_rows:
        print("No runners found to score.")
        return []

    df_targets = pd.DataFrame(target_rows).drop_duplicates()
    # Load LTO sectionals and form directly from RACINGTV_2023_2026
    sql_sec = f"""
    SELECT
        r.HorseName,
        r.RaceDate,
        r.PosNo,
        r.JockeyClaim,
        r.Comment,
        TRY_CAST(iq.StrideLength AS FLOAT) as StrideLength,
        TRY_CAST(iq.TopSpeed AS FLOAT) as TopSpeed,
        TRY_CAST(iq.FinishingSpeedPct AS FLOAT) as FinishingSpeedPct
    FROM dbo.Scraped_Results r
    LEFT JOIN dbo.Scraped_RaceIQ iq 
      ON r.RaceDate = iq.RaceDate AND r.CourseName = iq.CourseName AND r.HorseName = iq.HorseName
    WHERE r.RaceDate < '{target_date_str}'
    ORDER BY r.RaceDate DESC;
    """
    conn_s = pyodbc.connect(CONN_SCRAPED)
    df_sec = pd.read_sql(sql_sec, conn_s)
    conn_s.close()

    df_sec['clean_horse'] = df_sec['HorseName'].apply(clean_horse_name)
    
    # NEW PRODB LTO QUERY
    sql_proform = f"""
    WITH RankedRuns AS (
        SELECT 
            LOWER(H.H_Name_No_Anything) AS horse_clean,
            HIR.HIR_PaceAbbrev,
            CASE WHEN SD.ASL > 0 AND SD.SL_Finish > 0 THEN (SD.ASL - SD.SL_Finish) END AS StrideDecay,
            HIR.HIR_DSLR,
            HIR.HIR_JockeysClaim,
            HIR.HIR_CommentsInRunning,
            R.RH_DateTime AS LTO_DateTime,
            ROW_NUMBER() OVER(PARTITION BY HIR.HIR_HNo ORDER BY R.RH_DateTime DESC) as rn
        FROM dbo.NEW_HIR HIR
        JOIN dbo.NEW_H H ON H.H_No = HIR.HIR_HNo
        JOIN dbo.NEW_RH R ON R.RH_RNo = HIR.HIR_RNo
        LEFT JOIN dbo.SData SD ON SD.SD_RNo = HIR.HIR_RNo AND SD.SD_HNo = HIR.HIR_HNo
        WHERE R.RH_DateTime < '{target_date_str}'
    )
    SELECT * FROM RankedRuns WHERE rn = 1
    """
    try:
        conn_p = pyodbc.connect(CONN_PROFORM)
        df_pro = pd.read_sql(sql_proform, conn_p)
        conn_p.close()
        df_pro['horse_clean'] = df_pro['horse_clean'].apply(clean_horse_name)
        proform_lto_map = df_pro.set_index('horse_clean').to_dict('index')
    except Exception as e:
        print(f"Warning: Could not fetch from PRODB: {e}")
        proform_lto_map = {}
    
    sec_lto = (
        df_sec
        .drop_duplicates(subset=['clean_horse'], keep='first')
        .set_index('clean_horse')
        .to_dict('index')
    )

    print(f"Matched {len(scraped_lto):,} scraped runs, {len(sec_lto):,} sectional records, and {len(proform_lto_map):,} PRODB records.")

    print("\n[3/3] Scoring runners with B2L reverse logic...")
    results = []

    for race in races:
        scored_runners = []
        race_dt = pd.to_datetime(f"{race['date']} {race['time']}", errors='coerce')
        if pd.isna(race_dt):
            continue

        for r in race['runners']:
            h_clean = r['horse_clean']
            s_data = scraped_lto.get(h_clean)
            p_data = proform_lto_map.get(h_clean)

            # ---- Extract signals ----
            pace = p_data['HIR_PaceAbbrev'] if p_data is not None and pd.notna(p_data['HIR_PaceAbbrev']) else ''
            is_leader = str(pace).upper() in ['L', 'P', 'F', 'LEAD', 'PROMINENT']

            stride_decay = (
                p_data['StrideDecay']
                if p_data is not None and 'StrideDecay' in p_data and pd.notna(p_data['StrideDecay'])
                else 0
            )

            comment = ""
            if p_data is not None and 'HIR_CommentsInRunning' in p_data and pd.notna(p_data['HIR_CommentsInRunning']):
                comment = str(p_data['HIR_CommentsInRunning']).lower()
            elif s_data and s_data.get('Comment'):
                comment = str(s_data['Comment']).lower()

            bad_disc = any(w in comment for w in BAD_DISC_WORDS)
            good_speed = any(w in comment for w in GOOD_SPEED_WORDS)

            dslr = (
                p_data['HIR_DSLR']
                if p_data is not None and 'HIR_DSLR' in p_data and pd.notna(p_data['HIR_DSLR'])
                else 99
            )

            jockey_claim = (
                p_data['HIR_JockeysClaim']
                if p_data is not None and 'HIR_JockeysClaim' in p_data and pd.notna(p_data['HIR_JockeysClaim'])
                else 0
            )

            # ---- B2L Scoring (REVERSED vs LAY system) ----
            score = 0

            # Positive signals
            if is_leader:
                score += 3  # Front runner — went well last time
            if (dslr <= 7) or (jockey_claim > 0):
                score += 2  # Quick return = trainer confident; claimer = jockey statement
            if not bad_disc:
                score += 2  # Clean last run = trustworthy at big odds
            if good_speed:
                score += 2  # Positive speed comment — showed real ability

            # Negative signals (override above)
            if stride_decay >= 0.66:  # FIXED: feet not metres; 0.20 was 6cm
                score -= 3  # Physically tiring — AVOID at any odds
            if bad_disc:
                score -= 2  # Erratic behaviour — risky back
            if dslr > 120:
                score -= 1  # Long absence = ring rusty risk

            # Identify positive speed words found
            speed_words_found = [w for w in GOOD_SPEED_WORDS if w in comment]

            scored_runners.append({
                'horse': r['horse_raw'],
                'horse_clean': h_clean,
                'score': score,
                'is_leader': is_leader,
                'bad_disc': bad_disc,
                'good_speed': good_speed,
                'speed_words': ', '.join(speed_words_found[:3]) if speed_words_found else '',
                'stride_decay': round(float(stride_decay), 2) if pd.notna(stride_decay) else 0.0,
                'dslr': int(dslr) if pd.notna(dslr) and dslr < 900 else 99,
                'lto_datetime': str(p_data['LTO_DateTime']) if p_data is not None and 'LTO_DateTime' in p_data and pd.notna(p_data['LTO_DateTime']) else None
            })

        if not scored_runners:
            continue

        df_r = pd.DataFrame(scored_runners)
        # Sort BEST first (highest score) for B2L
        df_r = df_r.sort_values(
            ['score', 'stride_decay', 'dslr', 'horse'],
            ascending=[False, True, True, True]
        ).reset_index(drop=True)

        df_r['rank_best'] = df_r.index + 1
        race['scored_runners'] = df_r.to_dict('records')
        results.append(race)

    return results


def print_b2l_sheet(races, target_date_str):
    print("\n" + "=" * 95)
    print(f" B2L BACK SHEET — HIGH-ODDS VALUE FINDER ({target_date_str})")
    print(f" Target: Horses at 20.00+ on Betfair Exchange | Modes: WIN / EACH-WAY / BACK-TO-LAY")
    print("=" * 95)

    win_star = []
    tier1 = []
    tier2 = []
    total_handicaps = 0

    for race in races:
        if not race['is_handicap'] or len(race['scored_runners']) < 5:
            continue
        total_handicaps += 1

        for r in race['scored_runners']:
            is_win_star = (r['rank_best'] <= 2 and r['score'] >= 7)
            is_t1 = (r['rank_best'] <= 2 and r['score'] >= 4 and not is_win_star)
            is_t2 = (r['rank_best'] <= 3 and r['score'] >= 2 and r['good_speed'] and not is_win_star and not is_t1)

            signals = []
            if r['is_leader']:
                signals.append("FrontRunner(+3)")
            if r['good_speed'] and r['speed_words']:
                signals.append(f"Speed({r['speed_words']})(+2)")
            if r['stride_decay'] >= 0.66:
                signals.append(f"StrideDecay({r['stride_decay']}ft)(-3)")
            if r['bad_disc']:
                signals.append("Discipline(-2)")

            entry = {
                'tier': '*** WIN STAR ***' if is_win_star else ('B2L Tier 1 EW' if is_t1 else 'B2L Tier 2'),
                'time': race['time'],
                'course': race['course'],
                'horse': r['horse'],
                'score': r['score'],
                'dslr': r['dslr'],
                'signals': ', '.join(signals) if signals else 'Form Score',
                'win': 'WIN ONLY' if is_win_star else ('YES' if (is_t1 or is_t2) else ''),
                'ew': '' if is_win_star else ('YES' if (is_t1 or is_t2) else ''),
                'b2l': 'YES' if is_win_star else ('YES' if is_t1 else ('Consider' if is_t2 else '')),
            }
            if is_win_star:
                win_star.append(entry)
            elif is_t1:
                tier1.append(entry)
            elif is_t2:
                tier2.append(entry)

    all_targets = win_star + tier1 + tier2
    print(f"\n{'BACK SELECTIONS':=<95}")
    print(f" *** WIN STAR (score 7+): {len(win_star)} | Tier 1 EW (score 4-6): {len(tier1)} | Tier 2 B2L (score 2+): {len(tier2)}")
    print(f" Filter on Betfair Exchange for odds >= {MIN_ODDS:.1f} | WIN STAR = WIN BET ONLY at 20+")
    print("=" * 95)
    print(f"{'TIER':<16} | {'TIME':<7} | {'COURSE':<18} | {'HORSE':<22} | {'SCR':<4} | {'DSLR':<5} | {'WIN':<9} | {'EW':<4} | {'B2L':<8} | SIGNALS")
    print("-" * 95)
    for q in all_targets:
        print(f"{q['tier']:<16} | {q['time']:<7} | {q['course']:<18} | {q['horse']:<22} | {q['score']:>4} | {str(q['dslr']):<5} | {q['win']:<9} | {q['ew']:<4} | {q['b2l']:<8} | {q['signals']}")
    print("=" * 95)
    print(f" BETTING RULES:")
    print(f"  WIN STAR — Score 7+: WIN ONLY on Betfair Exchange >= {MIN_ODDS:.0f}.0 (highest confidence)")
    print(f"  EW Tier1 — Score 4-6: Each-Way at >= {MIN_ODDS:.0f}.0  (1/4 odds, 3 places)")
    print(f"  B2L      — Back at >= {MIN_ODDS:.0f}.0, lay in-running when going well (green up)")
    print()

    # Full Racecard
    print("\n" + "=" * 95)
    print(f" FULL B2L RACECARD BREAKDOWN ({total_handicaps} Handicaps Scanned)")
    print("=" * 95)

    for race in races:
        if not race['is_handicap'] or len(race['scored_runners']) < 5:
            continue

        print(f"\n[RACE] {race['time']} {race['course']} - {race['title']} ({len(race['scored_runners'])} Runners)")
        print("-" * 95)
        print(f"{'STATUS':<16} | {'HORSE':<24} | {'SCORE':<6} | {'DSLR':<6} | KEY SIGNALS")
        print("-" * 95)

        for r in race['scored_runners']:
            is_win_star = (r['rank_best'] <= 2 and r['score'] >= 7)
            is_t1 = (r['rank_best'] <= 2 and r['score'] >= 4 and not is_win_star)
            is_t2 = (r['rank_best'] <= 3 and r['score'] >= 2 and r['good_speed'] and not is_win_star and not is_t1)

            flags = []
            if r['is_leader']:
                flags.append("FrontRunner(+3)")
            if r['good_speed'] and r['speed_words']:
                flags.append(f"Speed:{r['speed_words']}(+2)")
            if r['stride_decay'] >= 0.66:
                flags.append(f"StrideDecay({r['stride_decay']}ft)(-3)")
            if r['bad_disc']:
                flags.append("Discipline(-2)")
            if r['dslr'] <= 7:
                flags.append(f"QuickReturn({r['dslr']}d)(+2)")
            reasons = ', '.join(flags) if flags else 'Standard Form'

            if is_win_star:
                prefix = "*** WIN STAR WIN"
            elif is_t1:
                prefix = "GREEN TIER1 EW  "
            elif is_t2:
                prefix = "BLUE  TIER2 B2L "
            else:
                prefix = "      Pass      "

            print(f"{prefix:<16} | {r['horse']:<24} | {r['score']:>6} | {str(r['dslr']):<6} | {reasons}")

    print("\n" + "=" * 95)
    print(f" TOTAL HANDICAPS SCANNED: {total_handicaps}")
    print(f" WIN STAR (Win Only at 20+, score 7+): {len(win_star)}")
    print(f" TIER 1 (EW at 20+, score 4-6):       {len(tier1)}")
    print(f" TIER 2 (B2L Consider, score 2+):     {len(tier2)}")
    print("=" * 95)


def save_to_database_b2l(races, target_date_str):
    try:
        conn = pyodbc.connect(CONN_SCRAPED)
        cur = conn.cursor()

        cur.execute("""
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Scored_Racecards_B2L' and xtype='U')
        CREATE TABLE dbo.Scored_Racecards_B2L (
            RaceDate DATE NOT NULL,
            RaceTime VARCHAR(10) NOT NULL,
            CourseName VARCHAR(100) NOT NULL,
            RaceTitle VARCHAR(255),
            HorseName VARCHAR(100) NOT NULL,
            B2LScore INT,
            RankBest INT,
            IsB2LTarget BIT,
            Tier VARCHAR(20),
            DSLR INT,
            StrideDecay FLOAT,
            BadDiscipline BIT,
            GoodSpeed BIT,
            IsLeader BIT,
            SpeedWords VARCHAR(200),
            BetModes VARCHAR(50),
            Signals VARCHAR(500),
            ScannedAt DATETIME DEFAULT GETDATE()
        )
        """)
        conn.commit()

        cur.execute(f"DELETE FROM dbo.Scored_Racecards_B2L WHERE RaceDate = '{target_date_str}'")
        conn.commit()

        rows = []
        for race in races:
            if not race['is_handicap']:
                continue
            for r in race['scored_runners']:
                is_win_star = (r['rank_best'] <= 2 and r['score'] >= 7)
                is_t1 = (r['rank_best'] <= 2 and r['score'] >= 4 and not is_win_star)
                is_t2 = (r['rank_best'] <= 3 and r['score'] >= 2 and r['good_speed'] and not is_win_star and not is_t1)
                is_target = 1 if (is_win_star or is_t1 or is_t2) else 0

                if is_win_star:
                    tier = 'WIN STAR'
                    bet_modes = 'WIN,EW,B2L'
                elif is_t1:
                    tier = 'Tier 1 EW'
                    bet_modes = 'EW,B2L,WIN'
                elif is_t2:
                    tier = 'Tier 2 B2L'
                    bet_modes = 'EW,B2L-Consider'
                else:
                    tier = 'Pass'
                    bet_modes = ''

                flags = []
                if r['is_leader']:
                    flags.append("FrontRunner")
                if r['good_speed']:
                    flags.append(f"Speed({r['speed_words']})")
                if r['stride_decay'] >= 0.66:
                    flags.append(f"StrideDecay({r['stride_decay']}ft)")
                if r['bad_disc']:
                    flags.append("Discipline")
                signals_str = ', '.join(flags) if flags else 'Form Score'

                dslr_val = r['dslr'] if isinstance(r['dslr'], int) else None

                rows.append((
                    target_date_str,
                    race['time'],
                    race['course'],
                    race['title'],
                    r['horse'],
                    r['score'],
                    int(r['rank_best']),
                    is_target,
                    tier,
                    dslr_val,
                    float(r['stride_decay']) if r['stride_decay'] else 0.0,
                    1 if r['bad_disc'] else 0,
                    1 if r['good_speed'] else 0,
                    1 if r['is_leader'] else 0,
                    r['speed_words'][:200] if r['speed_words'] else '',
                    bet_modes,
                    signals_str
                ))

        if rows:
            insert_sql = """
            INSERT INTO dbo.Scored_Racecards_B2L
            (RaceDate, RaceTime, CourseName, RaceTitle, HorseName, B2LScore, RankBest,
             IsB2LTarget, Tier, DSLR, StrideDecay, BadDiscipline, GoodSpeed, IsLeader,
             SpeedWords, BetModes, Signals)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            cur.executemany(insert_sql, rows)
            conn.commit()
            print(f" Saved {len(rows)} B2L scored runners into SCRAPED_PRODB.dbo.Scored_Racecards_B2L")

        conn.close()
    except Exception as e:
        print(f" DB save note: {e}")


def export_to_excel_b2l(races, target_date_str):
    reports_dir = r"E:\Test\racing-form-system\reports"
    os.makedirs(reports_dir, exist_ok=True)
    excel_path = os.path.join(reports_dir, f"B2L_Sheet_{target_date_str}.xlsx")

    win_star_rows = []
    tier1_rows = []
    tier2_rows = []
    all_runner_rows = []

    for race in races:
        if not race['is_handicap'] or len(race['scored_runners']) < 5:
            continue

        for r in race['scored_runners']:
            is_win_star = (r['rank_best'] <= 2 and r['score'] >= 7)
            is_t1 = (r['rank_best'] <= 2 and r['score'] >= 4 and not is_win_star)
            is_t2 = (r['rank_best'] <= 3 and r['score'] >= 2 and r['good_speed'] and not is_win_star and not is_t1)

            flags = []
            if r['is_leader']:
                flags.append('FrontRunner(+3)')
            if r['good_speed']:
                flags.append(f"Speed({r['speed_words']})(+2)")
            if r['stride_decay'] >= 0.66:
                flags.append(f'StrideDecay({r["stride_decay"]}ft)(-3)')
            if r['bad_disc']:
                flags.append('Discipline(-2)')

            tier_name = 'WIN STAR' if is_win_star else ('Tier 1 EW' if is_t1 else ('Tier 2 B2L' if is_t2 else 'Pass'))
            win_bet = 'WIN ONLY' if is_win_star else ('YES' if is_t1 else '')
            ew_bet = 'YES' if (is_win_star or is_t1 or is_t2) else ''
            b2l_bet = 'YES' if (is_win_star or is_t1) else ('Consider' if is_t2 else '')

            row = {
                'RaceTime': race['time'],
                'Course': race['course'],
                'RaceTitle': race['title'],
                'Horse': r['horse'],
                'B2L_Score': r['score'],
                'Rank': r['rank_best'],
                'Tier': tier_name,
                'WIN_Bet': win_bet,
                'EachWay_Bet': ew_bet,
                'BackToLay_Bet': b2l_bet,
                'Min_Odds': f'>= {MIN_ODDS:.0f}.0' if (is_win_star or is_t1 or is_t2) else '',
                'DSLR': r['dslr'],
                'StrideDecay': r['stride_decay'],
                'FrontRunner': r['is_leader'],
                'GoodSpeed': r['good_speed'],
                'SpeedWords': r['speed_words'],
                'BadDiscipline': r['bad_disc'],
                'Signals': ', '.join(flags) if flags else 'Form Score',
            }
            all_runner_rows.append(row)
            if is_win_star:
                win_star_rows.append(row)
            elif is_t1:
                tier1_rows.append(row)
            elif is_t2:
                tier2_rows.append(row)

    try:
        import openpyxl
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            if win_star_rows:
                pd.DataFrame(win_star_rows).to_excel(writer, sheet_name='WIN STAR (Score 7+)', index=False)
            if tier1_rows:
                pd.DataFrame(tier1_rows).to_excel(writer, sheet_name='Tier 1 EW (Score 4-6)', index=False)
            if tier2_rows:
                pd.DataFrame(tier2_rows).to_excel(writer, sheet_name='Tier 2 B2L (Score 2-3)', index=False)
            if all_runner_rows:
                pd.DataFrame(all_runner_rows).to_excel(writer, sheet_name='All Scored Runners', index=False)
        print(f' Excel saved: {excel_path}')
    except Exception as e:
        print(f' Excel export skipped: {e}')


if __name__ == '__main__':
    target_date_str = parse_target_date()
    print('=' * 75)
    print(f'  B2L BACK SHEET SCANNER (HIGH-ODDS VALUE)  --  {target_date_str}')
    print(f'  Target: Betfair Exchange odds >= {MIN_ODDS:.0f}.0')
    print(f'  Modes: WIN / EACH-WAY / BACK-TO-LAY')
    print('=' * 75)

    races = fetch_live_racecards(target_date_str)
    if not races:
        print('No racecards found. Exiting.')
    else:
        scored = score_racecards_b2l(races, target_date_str)
        print_b2l_sheet(scored, target_date_str)
        save_to_database_b2l(scored, target_date_str)
        export_to_excel_b2l(scored, target_date_str)
    print('\nDone.')
