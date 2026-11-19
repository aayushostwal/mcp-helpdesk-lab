"""Small statistics helpers (no numpy needed)."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion. ``(0, 1)`` when there is no data."""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def median(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def quantile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation quantile, ``q`` in [0, 1]."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo))
