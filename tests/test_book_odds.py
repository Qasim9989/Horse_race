"""Synthetic test for book_odds._compare_bsp (no database needed).

Builds a known frame and checks the printed medians/percentages are right.

    pytest tests/test_book_odds.py
    python tests/test_book_odds.py      # same checks, with console output
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout

import pandas as pd

sys.path.insert(0, r"E:\Test\racing-form-system\scripts")
import book_odds


def _synthetic() -> tuple[pd.DataFrame, pd.Series]:
    """Two books over two runners with known price/BSP ratios."""
    rows = []
    # Book A: ratios 1.00 and 1.10 -> median 1.05, 50% > BSP, 50% >= BSP+5%
    for i, (price, bsp) in enumerate([(10.0, 10.0), (11.0, 10.0)]):
        rows.append({"BookmakerName": "BookA", "PriceDecimal": price,
                         "BSP_TRUE": bsp, "CourseClean": "testcourse",
                         "RaceTime": "13:00", "HorseClean": f"horse{i}",
                         "HorseName": f"Horse {i}"})
    # Book B: ratios 0.80 and 0.90 -> median 0.85, 0% over BSP
    for i, (price, bsp) in enumerate([(8.0, 10.0), (9.0, 10.0)]):
        rows.append({"BookmakerName": "BookB", "PriceDecimal": price,
                         "BSP_TRUE": bsp, "CourseClean": "testcourse",
                         "RaceTime": "13:00", "HorseClean": f"horse{i}",
                         "HorseName": f"Horse {i}"})
    m = pd.DataFrame(rows)
    m["Ratio"] = m["PriceDecimal"] / m["BSP_TRUE"]
    marg = pd.Series({"BookA": 1.20, "BookB": 1.35})
    return m, marg


def run_checks() -> tuple[list[tuple[str, bool]], str]:
    """Return the checks and the captured report text."""
    m, marg = _synthetic()

    buf = io.StringIO()
    with redirect_stdout(buf):
        book_odds._compare_bsp(m, marg, best_margin=1.10, book="BookA", top=5)
    out = buf.getvalue()
    # collapse padding so the checks read cleanly
    flat = " ".join(out.split())

    checks = [
        ("BookA median ratio 1.050", "BookA 2 1.050" in flat),
        ("BookB median ratio 0.850", "BookB 2 0.850" in flat),
        ("BookA 50% > BSP and 50% >= BSP+5%",
         "BookA 2 1.050 50.0% 50.0% 20.0%" in flat),
        ("BookB 0% > BSP", "BookB 2 0.850 0.0% 0.0% 35.0%" in flat),
        # one horse per race -> bests are 10.0 and 11.0 vs BSP 10.0 -> median 1.05
        ("best-of-market row present",
         "BEST-OF-MARKET 2 1.050 50.0% 50.0% 10.0%" in flat),
        ("book detail section",
         "=== 'BookA' - best edges vs BSP (2 runners) ===" in out),
        ("subset line",
         "price >= BSP x 1.10 : 1 of 2 runners (50.0%)" in flat),
    ]

    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        book_odds._compare_bsp(m, marg, best_margin=1.10, book="BEST", top=5)
    out2 = buf2.getvalue()
    flat2 = " ".join(out2.split())
    checks += [
        ("BEST view header", "BEST PRICE across all your accounts" in flat2),
        ("BEST view lists the longest-priced runner first",
         "Horse 1 testcourse 13:00 11.00 10.00 1.100" in flat2),
    ]
    return checks, out + "\n" + out2


def test_book_odds_compare_bsp() -> None:
    """Every printed figure must match the known-answer frame."""
    checks, _ = run_checks()
    failed = [name for name, ok in checks if not ok]
    assert not failed, f"failed checks: {failed}"


def main() -> int:
    checks, text = run_checks()
    print(text)
    for name, ok in checks:
        print(("  PASS " if ok else "  FAIL ") + name)
    bad = [name for name, ok in checks if not ok]
    print("\nRESULT:", "ALL PASS" if not bad else f"{len(bad)} FAILED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
