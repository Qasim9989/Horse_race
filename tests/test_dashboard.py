"""Headless test of scripts/dashboard.py using Streamlit's AppTest.

Renders the whole dashboard (all tabs) against the real database and fails
loudly on any exception.

    pytest tests/test_dashboard.py
    python tests/test_dashboard.py      # same checks, with console output
"""
from __future__ import annotations

import sys

from streamlit.testing.v1 import AppTest

APP = r"E:\Test\racing-form-system\scripts\dashboard.py"


def run_checks() -> list[tuple[str, bool]]:
    """Render the app and return (check name, passed) pairs."""
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()

    errors = [str(e.value) for e in at.exception]
    charts = len(at.get("plotly_chart")) if hasattr(at, "get") else 0

    print("exceptions      :", errors if errors else "none")
    print("titles          :", [t.value for t in at.title])
    print("captions        :", len(at.caption))
    print("metrics         :", [(m.label, m.value) for m in at.metric])
    print("dataframes      :", len(at.dataframe))
    print("charts          :", charts if hasattr(at, "get") else "-")
    print("selectboxes     :", [s.label for s in at.selectbox])
    print("infos/warnings  :", len(at.info), "/", len(at.warning))
    print("subheaders      :", [s.value for s in at.subheader][:12])

    return [
        ("no exception", not errors),
        ("page title rendered", len(at.title) >= 1),
        ("KPI metrics rendered", len(at.metric) >= 5),
        ("tables rendered", len(at.dataframe) >= 3),
        ("charts rendered", charts >= 4 if hasattr(at, "get") else True),
        ("race selector present", len(at.selectbox) >= 1),
    ]


def test_dashboard_renders() -> None:
    """The dashboard must render every section without raising."""
    checks = run_checks()
    failed = [name for name, ok in checks if not ok]
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
