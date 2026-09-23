#!/usr/bin/env python3
"""pack_db.py - gzip racing_form.db for the repo (GitHub rejects files >= 100 MB).

The raw database is ~100 MB and grows daily; the repo carries the .gz (~29 MB) and
cloud_app/app.py unpacks it on cold start.  See scripts/pack_cloud_db.py on the
archive host for the fuller version with warnings.
"""
import gzip
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "racing_form.db")
if not os.path.exists(DB):
    DB = os.path.join(os.path.dirname(HERE), "racing_form.db.gz").replace(".gz", "")
GZ = DB + ".gz"

if not os.path.exists(DB):
    print("no racing_form.db found near %s" % HERE)
    sys.exit(1)

tmp = GZ + ".tmp"
with open(DB, "rb") as fi, gzip.open(tmp, "wb", compresslevel=9) as fo:
    shutil.copyfileobj(fi, fo, 1024 * 1024)
os.replace(tmp, GZ)
print("packed %.1f MB -> %.1f MB (%s)"
      % (os.path.getsize(DB) / 1048576.0, os.path.getsize(GZ) / 1048576.0, GZ))
