import datetime, sys, os
sys.path.insert(0, os.path.join(os.getcwd(), 'scripts'))
import betfair_bsp_backfill as bf
# clear stale (wrongly-mapped) values first
import pyodbc
c = pyodbc.connect(bf.CONN_PROFORM); c.autocommit = True; cur = c.cursor()
cur.execute("IF COL_LENGTH('dbo.NEW_HIR','HIR_BSP_TRUE') IS NOT NULL UPDATE dbo.NEW_HIR SET HIR_BSP_TRUE = NULL")
cur.execute("SELECT COUNT(*) FROM dbo.BFSP"); print('BFSP rows:', cur.fetchone()[0])
c.close()
bf.map_to_hir(datetime.date(2020,12,31), datetime.date(2026,9,14))
print('REMAP DONE')
