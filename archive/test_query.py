import pandas as pd
import pyodbc

conn = pyodbc.connect('Driver={ODBC Driver 17 for SQL Server};Server=(localdb)\\MSSQLLocalDB;Database=SCRAPED_PRODB;Trusted_Connection=yes;')
print(pd.read_sql("SELECT RaceDate, COUNT(*) as Runners FROM Scraped_Results WHERE RaceDate >= '2026-06-01' AND RaceDate <= '2026-06-10' GROUP BY RaceDate ORDER BY RaceDate", conn))
