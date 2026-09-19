"""
STRIDE & OTHER ANGLES vs REAL PRICES
====================================
For every angle we can build from what is already in PRODB, measure:

    n, strike rate, ROI at REAL BSP          (EV against the exchange)
    ROI at the EARLY price (IR slot 1/2)     (is betting early better?)
    mean early/BSP ratio                     (the price edge)

LOOK-AHEAD CONTROL: every stride/pace figure is taken from the horse's
PREVIOUS run, never the run being bet.  Stride data is in-race data
(position, speed, %race left) so using it on its own race would be cheating.
"""
from __future__ import annotations

import pandas as pd
import pyodbc

PRO = ("Driver={ODBC Driver 17 for SQL Server};"
       "Server=(localdb)\\MSSQLLocalDB;Database=PRODB;"
       "Trusted_Connection=yes;Connection Timeout=180;"
       "MultipleActiveResultSets=True;")

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)
c = pyodbc.connect(PRO)
cur = c.cursor()


def cols(table):
    cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION", (table,))
    return [r[0] for r in cur.fetchall()]


print("BFSP columns:", cols("BFSP"))
print("NEW_HIR has BSP?", [x for x in cols("NEW_HIR") if "BSP" in x.upper()])
c.close()
