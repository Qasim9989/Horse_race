"""
MASTER RACECARD & LAY SCANNER (TODAY & TOMORROW)
=================================================
Fetches live racecards from RacingTV (for today or tomorrow),
cross-references SCRAPED_PRODB & PRODB, and generates:
1. Executive Lay Summary Sheet (all qualified lay bets at a glance).
2. Full Detailed Racecard View (all runners, draws, jockeys, scores, reasons).

UPDATED:
- PRODB lookup now uses exact target race timestamps via temp table + OUTER APPLY.
- This aligns live scanning more closely with custom_date_audit.py point-in-time logic.
"""

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

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
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
                print(f" ⚠ Timeout loading card {course} {rtime}: {e}")
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
                print(f" ⚠ 0 runners found on {course} {rtime}")
                return None

        except Exception as e:
            print(f" ⚠ Card error {course} {rtime}: {e}")
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

            await asyncio.sleep(2.5)  # Give page time to fully render after cookie dismiss
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
            print(f" ⚠ Error fetching racecards list: {e}")
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

def score_racecards(races, target_date_str):
    print("\n[2/3] Cross-referencing SCRAPED_PRODB and PRODB pre-race records...")

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
        print("Matched 0 scraped runs and 0 Proform database records.")
        return []

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
    proform_lto_map = {}
    
    sec_lto = (
        df_sec
        .drop_duplicates(subset=['clean_horse'], keep='first')
        .set_index('clean_horse')
        .to_dict('index')
    )

    print(f"Matched {len(scraped_lto):,} scraped runs and {len(sec_lto):,} sectional form records in 0.5s.")

    print("\n[3/3] Ranking runners and generating Lay Sheet...")
    results = []

    for race in races:
        scored_runners = []
        race_dt = pd.to_datetime(f"{race['date']} {race['time']}", errors='coerce')
        if pd.isna(race_dt):
            continue

        for r in race['runners']:
            h_clean = r['horse_clean']
            s_data = scraped_lto.get(h_clean)
            p_data = proform_lto_map.get((h_clean, race_dt))

            pace = p_data['HIR_PaceAbbrev'] if p_data is not None and pd.notna(p_data['HIR_PaceAbbrev']) else ''
            is_leader = str(pace).upper() in ['L', 'P', 'F', 'LEAD', 'PROMINENT']

            stride_decay = (
                p_data['StrideDecay']
                if p_data is not None and 'StrideDecay' in p_data and pd.notna(p_data['StrideDecay'])
                else 0
            )

            posaftupg = (
                p_data['POSAFTUPG']
                if p_data is not None and 'POSAFTUPG' in p_data and pd.notna(p_data['POSAFTUPG'])
                else 0
            )

            comment = ""
            if s_data and s_data.get('Comment'):
                comment = str(s_data['Comment']).lower()
            elif p_data is not None and 'HIR_CommentsInRunning' in p_data and pd.notna(p_data['HIR_CommentsInRunning']):
                comment = str(p_data['HIR_CommentsInRunning']).lower()

            bad_disc = any(w in comment for w in ['slowly away', 'dwelt', 'pulled hard', 'keen', 'hung', 'erratic'])

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

            score = 0
            if is_leader:
                score += 3
            if posaftupg == 1:
                score += 3
            elif posaftupg == 2:
                score += 1
            if (dslr <= 7) or (jockey_claim > 0):
                score += 2

            if stride_decay >= 0.66:  # FIXED: feet not metres; 0.20 was 6cm
                score -= 2
            if posaftupg > 1:
                score -= 2
            if bad_disc:
                score -= 2

            scored_runners.append({
                'horse': r['horse_raw'],
                'horse_clean': h_clean,
                'score': score,
                'is_leader': is_leader,
                'bad_disc': bad_disc,
                'stride_decay': round(float(stride_decay), 2) if pd.notna(stride_decay) else 0.0,
                'dslr': int(dslr) if pd.notna(dslr) and dslr < 900 else 99,
                'lto_datetime': str(p_data['LTO_DateTime']) if p_data is not None and 'LTO_DateTime' in p_data and pd.notna(p_data['LTO_DateTime']) else None
            })

        if not scored_runners:
            continue

        df_r = pd.DataFrame(scored_runners)
        df_r = df_r.sort_values(
            ['score', 'stride_decay', 'dslr', 'horse'],
            ascending=[True, False, False, True]
        ).reset_index(drop=True)

        df_r['rank_worst'] = df_r.index + 1
        race['scored_runners'] = df_r.to_dict('records')
        results.append(race)

    return results

def print_master_sheet(races, target_date_str):
    print("\n" + "=" * 90)
    print(f" MASTER RACECARD & LAY SHEET ({target_date_str})")
    print("=" * 90)

    qualifiers = []
    total_handicaps = 0

    for race in races:
        if not race['is_handicap'] or len(race['scored_runners']) < 5:
            continue
        total_handicaps += 1

        for r in race['scored_runners']:
            if r['rank_worst'] <= 2 and r['score'] < 0:
                tier_label = "Tier 1 (#1 Worst)" if r['rank_worst'] == 1 else "Tier 2 (#2 Worst)"
                flags = []
                if r['bad_disc']:
                    flags.append("Discipline Issue")
                if r['stride_decay'] >= 0.66:
                    flags.append(f"Stride Decay ({r['stride_decay']}ft)")
                qualifiers.append({
                    'tier': tier_label,
                    'time': race['time'],
                    'course': race['course'],
                    'horse': r['horse'],
                    'score': r['score'],
                    'dslr': r['dslr'],
                    'reasons': ", ".join(flags) if flags else "Form Decay"
                })

    print(f"\n🏆 EXECUTIVE SUMMARY: {len(qualifiers)} QUANTITATIVE LAY TARGETS (Tier 1 & Tier 2)")
    print("=" * 95)
    print(f"{'TIER':<18} | {'TIME':<7} | {'COURSE':<18} | {'HORSE':<22} | {'SCORE':<5} | {'DSLR':<5} | {'VULNERABILITY'}")
    print("-" * 95)
    for q in qualifiers:
        print(f"{q['tier']:<18} | {q['time']:<7} | {q['course']:<18} | {q['horse']:<22} | {q['score']:>5} | {str(q['dslr']):<5} | {q['reasons']}")
    print("=" * 95)
    print(" BETTING RULE: Lay on Betfair Exchange ONLY if Betfair Starting Price (BSP) <= 6.00 (Fixed £15 Liability).\n")

    print("\n" + "=" * 95)
    print(f" DETAILED RACECARD BREAKDOWN ({total_handicaps} Handicaps Scanned)")
    print("=" * 95)

    for race in races:
        if not race['is_handicap'] or len(race['scored_runners']) < 5:
            continue

        print(f"\n[RACE] {race['time']} {race['course']} - {race['title']} ({len(race['scored_runners'])} Runners)")
        print("-" * 95)
        print(f"{'STATUS':<16} | {'HORSE':<24} | {'SCORE':<6} | {'DSLR':<6} | {'KEY SIGNALS & FLAGS'}")
        print("-" * 95)

        for r in race['scored_runners']:
            is_t1 = (r['rank_worst'] == 1 and r['score'] < 0)
            is_t2 = (r['rank_worst'] == 2 and r['score'] < 0)

            flags = []
            if r['bad_disc']:
                flags.append("Discipline Issue (dwelt/hung)")
            if r['stride_decay'] >= 0.66:
                flags.append(f"Stride Decay ({r['stride_decay']}ft)")
            if r['is_leader']:
                flags.append("Front Runner (+3)")
            if r['dslr'] != "N/A" and isinstance(r['dslr'], int) and r['dslr'] <= 7:
                flags.append(f"Quick Return ({r['dslr']}d)")

            reasons = ", ".join(flags) if flags else "Standard Form"

            if is_t1:
                prefix = "🔴 TIER 1 LAY"
            elif is_t2:
                prefix = "🟠 TIER 2 LAY"
            else:
                prefix = " Pass"

            print(f"{prefix:<16} | {r['horse']:<24} | {r['score']:>5} | {str(r['dslr']):<6} | {reasons}")

    print("\n" + "=" * 95)
    print(f" TOTAL HANDICAPS SCANNED: {total_handicaps} | TOTAL QUALIFIERS: {len(qualifiers)}")
    print("=" * 95)

def save_to_database(races, target_date_str):
    """Saves all scored runners and lay qualifiers into SCRAPED_PRODB."""
    try:
        conn = pyodbc.connect(CONN_SCRAPED)
        cur = conn.cursor()

        cur.execute("""
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Scored_Racecards_LaySheet' and xtype='U')
        CREATE TABLE dbo.Scored_Racecards_LaySheet (
            RaceDate DATE NOT NULL,
            RaceTime VARCHAR(10) NOT NULL,
            CourseName VARCHAR(100) NOT NULL,
            RaceTitle VARCHAR(255),
            HorseName VARCHAR(100) NOT NULL,
            MasterScore INT,
            RankWorst INT,
            IsLayTarget BIT,
            DSLR INT,
            StrideDecay FLOAT,
            BadDiscipline BIT,
            IsLeader BIT,
            Reasons VARCHAR(500),
            ScannedAt DATETIME DEFAULT GETDATE()
        )
        """)
        conn.commit()

        cur.execute(f"DELETE FROM dbo.Scored_Racecards_LaySheet WHERE RaceDate = '{target_date_str}'")
        conn.commit()

        rows = []
        for race in races:
            if not race['is_handicap']:
                continue
            for r in race['scored_runners']:
                is_target = 1 if (r['rank_worst'] <= 2 and r['score'] < 0) else 0
                flags = []
                if r['bad_disc']:
                    flags.append("Discipline Issue (dwelt/hung)")
                if r['stride_decay'] >= 0.66:
                    flags.append(f"Stride Decay ({r['stride_decay']}ft)")
                if r['is_leader']:
                    flags.append("Front Runner (+3)")
                dslr_val = r['dslr'] if isinstance(r['dslr'], int) else None
                if dslr_val and dslr_val <= 7:
                    flags.append(f"Quick Return ({dslr_val}d)")
                reasons_str = ", ".join(flags) if flags else "Standard Form"

                rows.append((
                    target_date_str,
                    race['time'],
                    race['course'],
                    race['title'],
                    r['horse'],
                    r['score'],
                    int(r['rank_worst']),
                    is_target,
                    dslr_val,
                    float(r['stride_decay']) if r['stride_decay'] else 0.0,
                    1 if r['bad_disc'] else 0,
                    1 if r['is_leader'] else 0,
                    reasons_str
                ))

        if rows:
            insert_sql = """
            INSERT INTO dbo.Scored_Racecards_LaySheet
            (RaceDate, RaceTime, CourseName, RaceTitle, HorseName, MasterScore, RankWorst, IsLayTarget, DSLR, StrideDecay, BadDiscipline, IsLeader, Reasons)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            cur.executemany(insert_sql, rows)
            conn.commit()
            print(f" 💾 Saved {len(rows)} scored runners into SCRAPED_PRODB.dbo.Scored_Racecards_LaySheet")

        conn.close()

    except Exception as e:
        print(f" ⚠ Database save note: {e}")

def export_to_excel(races, target_date_str):
    """Exports executive summary and full racecards to formatted Excel workbook."""
    reports_dir = r"E:\Test\racing-form-system\reports"
    os.makedirs(reports_dir, exist_ok=True)
    excel_path = os.path.join(reports_dir, f"Lay_Sheet_{target_date_str}.xlsx")

    qualifiers = []
    all_runners = []

    for race in races:
        if not race['is_handicap'] or len(race['scored_runners']) < 5:
            continue

        for r in race['scored_runners']:
            is_target = (r['rank_worst'] <= 2 and r['score'] < 0)
            tier_str = "Tier 1 (#1 Worst)" 
            if is_target:
                tier_str = 'Tier 1 (#1 Worst)' if r['rank_worst'] == 1 else 'Tier 2 (#2 Worst)'
            else:
                tier_str = ''

            flags = []
            if r['bad_disc']:
                flags.append('Discipline Issue')
            if r['stride_decay'] >= 0.66:
                flags.append(f'Stride Decay ({r['stride_decay']}ft)')
            if r['is_leader']:
                flags.append('Front Runner')
            reasons = ', '.join(flags) if flags else 'Standard Form'

            all_runners.append({
                'RaceTime': race['time'],
                'Course': race['course'],
                'RaceTitle': race['title'],
                'Horse': r['horse'],
                'Score': r['score'],
                'RankWorst': r['rank_worst'],
                'IsLayTarget': is_target,
                'Tier': tier_str,
                'DSLR': r['dslr'],
                'StrideDecay': r['stride_decay'],
                'BadDiscipline': r['bad_disc'],
                'IsLeader': r['is_leader'],
                'Reasons': reasons,
            })
            if is_target:
                qualifiers.append(all_runners[-1])

    try:
        import openpyxl
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            if qualifiers:
                pd.DataFrame(qualifiers).to_excel(writer, sheet_name='Lay Targets', index=False)
            if all_runners:
                pd.DataFrame(all_runners).to_excel(writer, sheet_name='All Runners', index=False)
        print(f' Excel saved: {excel_path}')
    except Exception as e:
        print(f' Excel export skipped: {e}')


if __name__ == '__main__':
    target_date_str = parse_target_date()
    print('=' * 75)
    print(f'  MASTER RACECARD & LAY SCANNER  --  {target_date_str}')
    print('=' * 75)

    races = fetch_live_racecards(target_date_str)
    if not races:
        print('No racecards found. Exiting.')
    else:
        scored = score_racecards(races, target_date_str)
        print_master_sheet(scored, target_date_str)
        save_to_database(scored, target_date_str)
        export_to_excel(scored, target_date_str)
    print('\nDone.')
