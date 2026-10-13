"""Synthetic arrivals for tests and for the golden fixtures: delays that persist with a drift."""

from __future__ import annotations

import numpy as np

from app.ml.featurize import Event

BASE = 1_760_000_000.0  # 2025-10-09


def synthetic_events(trips: int = 120, stops: int = 14, seed: int = 5) -> list[Event]:
    """One trip every minute on four lines, delays that persist with a drift."""
    rng = np.random.default_rng(seed)
    events = []
    for number in range(trips):
        delay = float(rng.normal(60, 90))
        line = str(10 + number % 4)
        for seq in range(1, stops + 1):
            delay += rng.normal(0, 12) - 0.05 * delay
            scheduled = BASE + number * 60 + seq * 90
            actual = scheduled + delay
            events.append(
                Event(
                    f"T{number}", seq, f"s{seq}", line, scheduled, actual, round(delay), actual + 30
                )
            )
    return events
