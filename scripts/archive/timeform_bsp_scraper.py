"""
TIMEFORM TRUE BSP & RESULTS SCRAPER
====================================
Scrapes official Betfair Starting Price (BSP), Industry SP (ISP), Finishing Positions,
and place terms directly from Timeform race result pages.

Saves directly into SCRAPED_PRODB.dbo.Scraped_Results and Scraped_RaceIQ with true BSP.
"""

import sys
import os
import re
import datetime
import pyodbc
import pandas as pd
from bs4 import BeautifulSoup

CONN_S = r"Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\MSSQLLocalDB;Database=SCRAPED_PRODB;Trusted_Connection=yes;"

def clean_horse_name(name):
    if not name: return ""
    name = re.sub(r"^\d+\.\s*", "", str(name))
    name = re.sub(r"\s*\([A-Z]{2,4}\)$", "", str(name), flags=re.IGNORECASE).strip()
    return "".join(c for c in name.lower() if c.isalnum())

def parse_timeform_html_table(html_content, race_date_str, course_name, race_time):
    """
    Parses runner rows from Timeform HTML table.
    Extracts: Pos, Horse Name, ISP, BSP, Jockey, Trainer, Equipment
    """
    soup = BeautifulSoup(html_content, "html.parser")
    runners = []

    # Find table rows
    rows = soup.find_all("tr")
    for tr in rows:
        # Check for horse link or runner cell
        horse_link = tr.find("a", href=re.compile(r"/horse-form/"))
        if not horse_link:
            continue

        horse_raw = horse_link.text.strip()
        horse_name = re.sub(r"^\d+\.\s*", "", horse_raw)

        # Get all cells in this row
        cells = [td.text.strip() for td in tr.find_all(["td", "th"])]
        if not cells:
            continue

        # Look for position (e.g. "1", "1 (8)", "2", "NR")
        pos_match = re.search(r"^\s*(\d+)", cells[0])
        pos_str = pos_match.group(1) if pos_match else ("NR" if "NR" in cells[0] else cells[0])

        isp = None
        bsp = None

        for cell in cells:
            # Check for ISP (fractional odds like 5/2, 12/1, 5/4f, evs)
            if re.search(r"^\d+/\d+[a-z]*$|^evs$|^evens$", cell, re.IGNORECASE) and not isp:
                isp = cell
            # Check for BSP (decimal odds like 3.8, 16.5, 26.41, 250.82)
            elif re.search(r"^\d+\.\d+$|^\d+$", cell) and isp and not bsp:
                try:
                    val = float(cell)
                    if val >= 1.01 and val != float(pos_str if pos_str.isdigit() else 0):
                        bsp = val
                except:
                    pass

        runners.append({
            "RaceDate": race_date_str,
            "RaceTime": race_time,
            "CourseName": course_name,
            "HorseName": horse_name,
            "PosNo": pos_str,
            "ISP": isp,
            "BSP": bsp
        })

    return runners

def save_timeform_results_to_db(runners_list):
    """
    Saves or updates true BSP and results into SCRAPED_PRODB.dbo.Scraped_Results.
    """
    if not runners_list:
        return 0

    conn = pyodbc.connect(CONN_S)
    cur = conn.cursor()

    # Ensure BSP column exists in Scraped_Results
    try:
        cur.execute("IF COL_LENGTH('dbo.Scraped_Results', 'BSP') IS NULL ALTER TABLE dbo.Scraped_Results ADD BSP FLOAT NULL")
        conn.commit()
    except:
        pass

    updated = 0
    for r in runners_list:
        h_clean = clean_horse_name(r['HorseName'])
        bsp_val = r.get('BSP')
        sp_val = r.get('ISP')
        pos_val = r.get('PosNo')

        if bsp_val:
            cur.execute("""
            UPDATE dbo.Scraped_Results
            SET BSP = ?, SP = COALESCE(?, SP), PosNo = COALESCE(?, PosNo)
            WHERE RaceDate = ? AND LOWER(REPLACE(REPLACE(HorseName, ' ', ''), '''', '')) LIKE ?
            """, (bsp_val, sp_val, pos_val, r['RaceDate'], f"%{h_clean}%"))
            if cur.rowcount > 0:
                updated += cur.rowcount

    conn.commit()
    conn.close()
    return updated

if __name__ == "__main__":
    print("Timeform True BSP Parser and DB Updater Module Loaded.")
