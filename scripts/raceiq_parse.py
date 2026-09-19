r"""
RACEIQ PARSER - clean, unit-aware, validated
============================================
Written against the real page (see reports\_raceiq_probe.txt).  RacingTV's
RaceIQ Comparison tab renders, for each horse, a regular block:

    Shifra
    METRIC
    RANK
    DATA
    0-20MPH        <- metric label
    5              <- rank
    TH             <- rank suffix
    3.96S          <- value + UNIT
    Stride Length
    7
    TH
    7.1M
    FSP
    6
    TH
    94.76%
    Top Speed
    3
    RD
    39.0MPH
    Median         <- end of this horse

The unit is what makes this safe: MPH is top speed, M is stride length in
metres, % is finishing speed percentage, S is the 0-20MPH acceleration time.
The old parser searched for "the first number before an M or MPH" anywhere in
the block, which is why "0-20MPH" ended up stored as a top speed of 20 and why
AvgFrequency (which does not exist on the page) was NULL on every row.

The metric set depends on the RACE TYPE, which is why whole meetings used to
come back with no stride at all:

    flat     0-20MPH, Stride Length, FSP, Top Speed
    jumps    Jump Index, LGJ, FSP, Top Speed, Speed Lost, Entry Speed

Jumps publish no stride and no 0-20MPH; they publish four metrics of their own
instead - Jump Index (/10), LGJ in lengths gained jumping (L), Entry Speed and
Speed Lost in MPH (Speed Lost is negative).  All six jump labels are parsed
below.  Anything still unrecognised is skipped silently, so a new label costs
coverage rather than correctness.

Nothing here touches the browser or the database - it is a pure function over
page text, so it can be tested against the captured page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# metric label on the page -> (expected unit, canonical field name)
METRICS: dict[str, tuple[str, str]] = {
    "0-20MPH": ("S", "accel_0_20_s"),
    "Stride Length": ("M", "stride_m"),
    "FSP": ("%", "fsp_pct"),
    "Top Speed": ("MPH", "top_speed_mph"),
    # jump races only - no stride, no 0-20MPH on the page at all
    "Jump Index": ("/10", "jump_index"),
    "LGJ": ("L", "lgj_lengths"),
    "Entry Speed": ("MPH", "entry_speed_mph"),
    "Speed Lost": ("MPH", "speed_lost_mph"),
}
# canonical field -> plausible range; anything outside is rejected, not stored
RANGES: dict[str, tuple[float, float]] = {
    "accel_0_20_s": (0.8, 8.0),
    "stride_m": (4.5, 9.5),
    "fsp_pct": (60.0, 160.0),
    "top_speed_mph": (20.0, 50.0),
    "jump_index": (0.0, 10.0),
    "lgj_lengths": (-40.0, 40.0),
    "entry_speed_mph": (5.0, 55.0),
    "speed_lost_mph": (-25.0, 25.0),
}
# also allows the jump-shaped values: '6.3/10' and a negative '-3.01MPH'
VALUE_RE = re.compile(r"^(-?\d+(?:\.\d+)?)(/10|[A-Z%]*)$")
SUFFIXES = {"ST", "ND", "RD", "TH"}


@dataclass
class HorseRaceIQ:
    """One horse's validated metrics from the RaceIQ comparison block."""

    horse: str
    values: dict[str, float] = field(default_factory=dict)
    ranks: dict[str, int] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)

    @property
    def stride_ft(self) -> float | None:
        s = self.values.get("stride_m")
        return round(s * 3.28084, 2) if s is not None else None

    @property
    def avg_frequency_sps(self) -> float | None:
        """Strides per second, derived: speed (m/s) / stride (m).

        The page has no 'Avg Frequency' field; the old column was never
        populated because the parser searched for a label that does not exist.
        """
        speed = self.values.get("top_speed_mph")
        stride = self.values.get("stride_m")
        if not speed or not stride:
            return None
        return round(speed * 0.44704 / stride, 3)


def parse_value(token: str) -> tuple[float, str] | None:
    """'39.0MPH' -> (39.0, 'MPH');  '94.76%' -> (94.76, '%')."""
    m = VALUE_RE.match(token.replace(" ", "").upper())
    if not m:
        return None
    return float(m.group(1)), (m.group(2) or "")


def keep(field_name: str, value: float) -> bool:
    lo, hi = RANGES[field_name]
    return lo <= value <= hi


def parse_by_horse(text: str) -> list[HorseRaceIQ]:
    """Pull every horse block out of a RaceIQ Comparison page."""
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n") if ln.strip()]
    out: list[HorseRaceIQ] = []
    i = 0
    while i < len(lines):
        if lines[i] != "METRIC" or i == 0:
            i += 1
            continue
        horse = lines[i - 1]
        i += 3                      # skip METRIC, RANK, DATA headers
        rec = HorseRaceIQ(horse=horse)
        while i + 3 < len(lines):
            label = lines[i]
            if label in {"Median", "METRIC"}:
                break
            rank, suffix, token = lines[i + 1], lines[i + 2], lines[i + 3]
            i += 4
            spec = METRICS.get(label)
            if spec is None:
                continue
            want_unit, field_name = spec
            parsed = parse_value(token)
            if parsed is None or parsed[1] != want_unit:
                rec.rejected.append(f"{field_name}={token} (unit)")
                continue
            value = parsed[0]
            if not keep(field_name, value):
                rec.rejected.append(f"{field_name}={value} (range)")
                continue
            rec.values[field_name] = value
            if suffix in SUFFIXES and rank.isdigit():
                rec.ranks[field_name] = int(rank)
        if rec.values:
            out.append(rec)
    return out
