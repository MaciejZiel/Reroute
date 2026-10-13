"""Plain-language reasons for a prediction, from per-feature SHAP values.

The model predicts how much the delay *changes* between the latest recorded stop and the target
stop, so every contribution is a number of seconds added to or taken off the current delay.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

NOTABLE_S = 5.0  # smaller effects are noise to a rider
MAX_FACTORS = 3


@dataclass(frozen=True)
class Factor:
    feature: str
    effect_s: int  # positive: later than the current delay alone would suggest
    text: str


@dataclass(frozen=True)
class Explanation:
    summary: str
    factors: tuple[Factor, ...]


def duration(seconds: float) -> str:
    seconds = abs(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min" if rest < 5 else f"{minutes} min {rest} s"


def delay_phrase(seconds: float) -> str:
    if abs(seconds) < 30:
        return "on time"
    return f"{duration(seconds)} {'late' if seconds > 0 else 'early'}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


_PHRASES: dict[str, Callable[[float], str]] = {
    "stops_ahead": lambda v: f"the stop is {_plural(round(v), 'stop')} ahead",
    "vehicle_delay_s": lambda v: f"the vehicle is {delay_phrase(v)} right now",
    "vehicle_minus_origin_s": lambda v: (
        f"its delay grew by {duration(v)} since the last stop"
        if v > 0
        else f"it has made up {duration(v)} since the last stop"
    ),
    "origin_delay_s": lambda v: f"the vehicle was {delay_phrase(v)} at its last stop",
    "prev_delay_change_s": lambda v: (
        f"its delay grew by {duration(v)} at the last stop"
        if v > 0
        else f"it made up {duration(v)} at the last stop"
    ),
    "trend_s_per_min": lambda v: (
        f"its delay has been {'growing' if v > 0 else 'shrinking'} by about "
        f"{abs(v):.0f} s per minute"
    ),
    "sched_run_s": lambda v: f"{duration(v)} of scheduled running time remain",
    "stop_seq": lambda v: f"this is stop number {round(v)} of the trip",
    "hour_frac": lambda v: "of the time of day",
    "weekday": lambda v: "of the day of the week",
    "is_tram": lambda v: "it is a tram" if v else "it is a bus",
    "line_recent_delay_s": lambda v: (
        f"other vehicles of this line were {delay_phrase(v)} on average in the last 15 minutes"
    ),
    "stop_recent_delay_s": lambda v: (
        f"vehicles at this stop were {delay_phrase(v)} on average in the last 30 minutes"
    ),
    "leader_delay_s": lambda v: f"the vehicle ahead on this line was {delay_phrase(v)} here",
    "headway_s": lambda v: f"the vehicle ahead is scheduled {duration(v)} earlier",
}


def explain(
    names: Sequence[str],
    values: Sequence[float],
    contributions: Sequence[float],
    origin_delay_s: float,
    predicted_delay_s: float,
) -> Explanation:
    """Top contributing factors, largest effect first, as short sentences."""
    ranked = sorted(zip(names, values, contributions, strict=True), key=lambda i: -abs(i[2]))
    factors = []
    for name, value, effect in ranked:
        if abs(effect) < NOTABLE_S or math.isnan(value) or name not in _PHRASES:
            continue
        text = _PHRASES[name](value)
        text = text if text.startswith("of ") else text[0].upper() + text[1:]
        factors.append(Factor(name, round(effect), text))
        if len(factors) == MAX_FACTORS:
            break
    now = f"Now {delay_phrase(origin_delay_s)}; expected {delay_phrase(predicted_delay_s)}"
    if not factors:
        return Explanation(f"{now} at this stop, no strong signal beyond the current delay.", ())
    main = "; ".join(f"{_lower(f.text)} ({f.effect_s:+d} s)" for f in factors[:2])
    return Explanation(f"{now} at this stop. Main factors: {main}.", tuple(factors))


def _lower(text: str) -> str:
    return text if text.startswith("of ") else text[0].lower() + text[1:]
