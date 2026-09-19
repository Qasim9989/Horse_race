"""
B2L HISTORICAL AUDITOR
======================
Compatibility wrapper for the unbiased B2L backtest.

The previous version of this file only loaded horses that finished 1st, 2nd,
or 3rd. That is useful for finding examples, but it is not a valid ROI
backtest because all losing unplaced runners are removed before selection.

For profitability checks this wrapper now delegates to b2l_proper_backtest.py,
which loads every high-odds handicap runner before scoring and settling.
"""

import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from b2l_proper_backtest import run


if __name__ == "__main__":
    days_back = 365
    if len(sys.argv) > 1:
        try:
            days_back = int(sys.argv[1])
        except ValueError:
            pass

    print("B2L audit now uses the all-runners clean backtest. No outcome pre-filtering.")
    run(days_back)
