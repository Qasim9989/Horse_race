"""
BETFAIR EXCHANGE EACH-WAY & VALUE EDGE SCANNER (CLI)
===================================================
Scans live UK & Irish horse racing markets using Betfair Delayed App Key API
and compares bookmaker Each-Way terms against Exchange Win + Place order books.

Usage:
  python scripts/betfair_ew_market.py
  python scripts/betfair_ew_market.py --meeting Newbury
  python scripts/betfair_ew_market.py --min-edge 0.0
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cloud_app"))

import betfair_ew_service as ew


def main() -> None:
    parser = argparse.ArgumentParser(description="Betfair Exchange Each-Way Edge Scanner")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--meeting", default="All Meetings Today", help="Meeting name (or 'All Meetings Today')")
    parser.add_argument("--min-edge", type=float, default=-999.0, help="Minimum EW Edge % to display")
    parser.add_argument("--limit", type=int, default=30, help="Max results to display")
    args = parser.parse_args()

    if not ew.is_configured():
        print("ERROR: Betfair credentials missing. Check BETFAIR_APP_KEY, BETFAIR_USERNAME, and BETFAIR_PASSWORD.")
        sys.exit(1)

    print(f"Scanning Betfair Exchange WIN & PLACE markets for {args.date} ({args.meeting})...")
    rows = ew.scan_day_ew_edges(args.date, meeting_filter=args.meeting)
    if not rows:
        print("No matching exchange markets found.")
        return

    filtered = [r for r in rows if (r.get("EW_Edge") or -999.0) >= args.min_edge]
    print(f"\nFound {len(filtered)} runners evaluated.")
    print("=" * 105)
    print(f"{'Race':<18} | {'Horse':<20} | {'Bookie':<10} | {'Win/Pl':<11} | {'BF Lay (W/P)':<14} | {'EW Edge':<9} | {'Pl Edge':<9} | {'Status'}")
    print("=" * 105)

    for r in filtered[:args.limit]:
        w_p = f"{r['Book_Win']:.1f}/{r['Book_Place']:.1f}"
        bf_l = f"{r['BF_Win_Lay'] or 0:.1f}/{r['BF_Place_Lay'] or 0:.1f}"
        ew_s = f"{r['EW_Edge']:+.1f}%" if r.get("EW_Edge") is not None else "N/A"
        pl_s = f"{r['Place_Edge']:+.1f}%" if r.get("Place_Edge") is not None else "N/A"
        verd = (r.get("Verdict") or "").encode("ascii", "replace").decode("ascii")
        print(f"{r['Race']:<18} | {r['Horse']:<20} | {r['Bookmaker']:<10} | {w_p:<11} | {bf_l:<14} | {ew_s:<9} | {pl_s:<9} | {verd}")


if __name__ == "__main__":
    main()
