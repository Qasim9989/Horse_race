"""RaceIQ parser tests - run against the real captured RacingTV page.

    pytest tests/test_raceiq_parse.py
    python tests/test_raceiq_parse.py

The page text in reports/_raceiq_probe.txt was dumped by
scripts\\raceiq_probe.py from a live race, so these assertions are about the
real layout rather than a fixture somebody invented.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from raceiq_parse import HorseRaceIQ, parse_by_horse, parse_value

PROBE = Path(__file__).resolve().parent.parent / "reports" / "_raceiq_probe.txt"


def _horses() -> list[HorseRaceIQ]:
    text = PROBE.read_text(encoding="utf-8")
    # the probe dumps each tab; the comparison block is the one this parser reads
    marker = "TAB RACEiQ COMPARISON"
    if marker in text:
        text = text.split(marker, 1)[1]
    return parse_by_horse(text)


def run_checks() -> list[tuple[str, bool]]:
    horses = _horses()
    names = {h.horse for h in horses}
    shifra = next((h for h in horses if h.horse == "Shifra"), None)
    checks = [
        ("page parses into horse blocks", len(horses) >= 4),
        ("Shifra found", shifra is not None),
    ]
    if shifra is not None:
        v = shifra.values
        checks += [
            ("stride is 7.1 metres", v.get("stride_m") == 7.1),
            ("top speed is 39.0 mph", v.get("top_speed_mph") == 39.0),
            ("FSP is 94.76 %", v.get("fsp_pct") == 94.76),
            ("0-20MPH is 3.96 s", v.get("accel_0_20_s") == 3.96),
            ("stride in feet derived", shifra.stride_ft == 23.29),
            ("strides/sec derived ~2.45",
             shifra.avg_frequency_sps is not None
             and abs(shifra.avg_frequency_sps - 2.456) < 0.01),
            ("ranks captured", shifra.ranks.get("top_speed_mph") == 3),
        ]
    checks += [
        ("no junk values survived in any horse",
         all(not h.rejected for h in horses)),
        ("every horse has a stride and a speed",
         all("stride_m" in h.values and "top_speed_mph" in h.values
             for h in horses)),
        ("the 0-20MPH label is not stored as a top speed",
         all(h.values.get("top_speed_mph") != 20.0 for h in horses)),
        ("value + unit parser", parse_value("39.0MPH") == (39.0, "MPH")),
        ("unit mismatch is rejected, not stored",
         parse_value("7.1M") == (7.1, "M")),
    ]
    checks.append(("horse count is stable", len(names) == len(horses)))
    return checks


def test_raceiq_parse() -> None:
    failed = [name for name, ok in run_checks() if not ok]
    assert not failed, f"failed checks: {failed}"


def main() -> int:
    checks = run_checks()
    for name, ok in checks:
        print(("  PASS " if ok else "  FAIL ") + name)
    bad = [name for name, ok in checks if not ok]
    print("\nRESULT:", "ALL PASS" if not bad else f"{len(bad)} FAILED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
