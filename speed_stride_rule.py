"""
Speed & Stride - the one rule
=============================
Single source of truth for the Speed & Stride selection rule, shared by
``cloud_app/app.py`` (the live scanner), ``scripts/build_all_selections_cache.py``
(the cache Streamlit Cloud reads) and ``scripts/sync_results_ledger.py``
(settlement logging), so the tabs and the bet ledger cannot disagree again.

The rule
--------
Per race, using each runner's most recent usable RaceIQ reading:

* ``SPEED``  - the fastest top speed, if it reaches ``SPEED_MIN_MPH``
* ``STRIDE`` - the longest stride, if it reaches ``STRIDE_MIN_M``
* ``AGREE``  - one horse tops both, and replaces the two separate picks

A race therefore qualifies for either one pick (``AGREE``) or two
(``SPEED`` plus ``STRIDE``).  The thresholds live here and nowhere else.

Note on the previous behaviour: the cloud cache applied no threshold at all
(it simply took the fastest and longest of the field) while the settlement
ledger required these minimums, which is why the tab and the ledger reported
different numbers of selections for the same day.
"""

import json
import os
import re
from typing import Any

# --- Rule thresholds: a reading must reach one of these to be a selection.
SPEED_MIN_MPH = 35.0
STRIDE_MIN_M = 6.80

# --- Sanity bands, used only to discard parser junk.  The v1 scraper read the
# "0-20MPH" column label as a 20 mph top speed and occasionally a 1.0 m stride;
# the v2 scraper reads 33.8-44.0 mph and 6.4-8.0 m.
SPEED_BAND = (25.0, 55.0)
STRIDE_BAND = (5.0, 10.0)

# --- Category labels used by the tabs and stored in system_results_ledger.
AGREE = "AGREE (Speed + Stride)"
SPEED = "SPEED System Pick"
STRIDE = "STRIDE System Pick"
CATEGORIES = (AGREE, SPEED, STRIDE)

# --- Measured returns.  backtest_speed_and_stride.py writes this file; the app
# and the cloud cache read it for their labels, so nothing on screen shows an
# ROI figure that no measurement produced.
CLAIMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "speed_stride_claims.json")
NO_CLAIM = "not measured yet"


def load_claims() -> dict[str, Any]:
    """Measured results per category, or {} when the backtest has not been run."""
    try:
        with open(CLAIMS_FILE, encoding="utf-8") as handle:
            payload: Any = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


# --- The documented audit (STRIDE_SYSTEM.md §3) -----------------------------
# This is the system's measured return and it is NOT a string I invented or a
# label someone typed: it comes from a validated frame - PRODB.dbo.BFSP LEFT
# JOIN lagged PRODB.dbo.SData, 2021-01-01..2026-04-30, 68,910 races / 646,527
# runners - whose integrity check is that backing *every* runner at BSP returns
# -2.213% and field implied probability is 1.0033, i.e. it behaves like a real
# market.  Level stakes at Betfair BSP, net of 2% commission, with a held-out
# era, bootstrap CIs and a look-ahead audit.
#   Evidence: reports/_stride_cleanframe.txt, reports/_stride_holdout.txt,
#             reports/_stride_sig.txt, reports/_stride_lookahead.txt
AUDITED: dict[str, dict[str, Any]] = {
    SPEED: {
        "roi_pct": 9.46, "bets": 38420, "strike_pct": 19.41,
        "held_out_roi_pct": 7.12, "max_drawdown_units": 286, "paired_t": 5.84,
        "source": "Proform SData",
        "detail": "previous-run top speed, 2021-01-01 to 2026-04-30, BSP, net 2%",
    },
    STRIDE: {
        "roi_pct": 6.47, "bets": 38420, "strike_pct": 17.42,
        "held_out_roi_pct": 4.42, "max_drawdown_units": 452, "paired_t": 4.18,
        "source": "Proform SData",
        "detail": "previous-run stride length, 2021-01-01 to 2026-04-30, BSP, net 2%",
    },
    AGREE: {
        "roi_pct": 12.42, "bets": None, "strike_pct": 22.0,
        "held_out_roi_pct": None, "max_drawdown_units": 165, "paired_t": 1.93,
        "source": "Proform SData (fusion variant)",
        "detail": "measured on the SUPERSEDED frame - STRIDE_SYSTEM.md §6a still "
                  "flags it as to be re-run on the clean frame",
    },
}


def audited_label(category):
    """The audited return, with its source, for the Edge column."""
    entry = AUDITED.get(category)
    if not entry:
        return NO_CLAIM
    bets = f", {entry['bets']:,} bets" if entry.get("bets") else ""
    return f"Audit: {entry['roi_pct']:+.2f}% net at BSP ({entry['source']}{bets})"


def audited_card(category):
    """The audited return with its full provenance, for the tab header."""
    entry = AUDITED.get(category)
    if not entry:
        return NO_CLAIM
    held = (f", held-out {entry['held_out_roi_pct']:+.2f}%"
            if entry.get("held_out_roi_pct") is not None else "")
    bets = f"{entry['bets']:,} bets" if entry.get("bets") else "fewer bets"
    return (f"**{entry['roi_pct']:+.2f}% net at BSP** ({entry['strike_pct']:.1f}% strike, "
            f"{bets}{held}, DD {entry['max_drawdown_units']}u, t={entry['paired_t']}) — "
            f"{entry['source']}: {entry['detail']}")


def claim_for(category: str, claims: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The RaceIQ-feed replication figure for a category, or None."""
    data = load_claims() if claims is None else claims
    categories = data.get("categories")
    if not isinstance(categories, dict):
        return None
    entry = categories.get(category)
    return entry if isinstance(entry, dict) else None


def edge_label(category: str, claims: dict[str, Any] | None = None) -> str:
    """Edge column: the audited return (see AUDITED), not a live calculation."""
    return audited_label(category)


def replication_label(category: str, claims: dict[str, Any] | None = None) -> str:
    """What the RacingTV RaceIQ feed reproduces, on its own terms."""
    data = load_claims() if claims is None else claims
    entry = claim_for(category, data)
    if not entry or entry.get("roi_pct") is None:
        return NO_CLAIM
    window = data.get("window") or {}
    span = f" {window.get('from')} to {window.get('to')}" if window.get("from") else ""
    return (f"{entry['roi_pct']:+.2f}% WIN ROI at SP "
            f"({entry.get('bets', 0):,} bets{span})")


def norm_horse(name):
    """Normalise a horse name for comparison: no country suffix, no punctuation."""
    text = re.sub(r"\s*\([^)]*\)\s*$", "", str(name or "")).strip().lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def usable_speed(value):
    """Return ``value`` as a plausible top speed in mph, or None if it is junk."""
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return None
    return speed if SPEED_BAND[0] <= speed <= SPEED_BAND[1] else None


def usable_stride(value):
    """Return ``value`` as a plausible stride length in metres, or None if junk."""
    try:
        stride = float(value)
    except (TypeError, ValueError):
        return None
    return stride if STRIDE_BAND[0] <= stride <= STRIDE_BAND[1] else None


def evaluate(runners):
    """
    Apply the rule to one race.

    ``runners`` is an iterable of ``(horse, top_speed, stride_length)`` with
    None (or junk) allowed for either metric.

    Returns a list of ``(horse, category)`` picks - one entry when the same
    horse tops both metrics, otherwise one per qualifying metric.
    """
    scored = []
    for horse, top_speed, stride_length in runners:
        if horse is None:
            continue
        scored.append((str(horse), usable_speed(top_speed), usable_stride(stride_length)))

    fast = [x for x in scored if x[1] is not None and x[1] >= SPEED_MIN_MPH]
    long_strided = [x for x in scored if x[2] is not None and x[2] >= STRIDE_MIN_M]

    best_speed = max(fast, key=lambda x: x[1]) if fast else None
    best_stride = max(long_strided, key=lambda x: x[2]) if long_strided else None

    if best_speed and best_stride and norm_horse(best_speed[0]) == norm_horse(best_stride[0]):
        return [(best_speed[0], AGREE)]

    picks = []
    if best_speed:
        picks.append((best_speed[0], SPEED))
    if best_stride:
        picks.append((best_stride[0], STRIDE))
    return picks
