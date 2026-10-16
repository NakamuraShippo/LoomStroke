"""Safe scalar conversion shared by Japanese text metadata readers."""

from __future__ import annotations

import math
from typing import Any


def finite_number(value: Any, default: float, minimum: float, maximum: float) -> float:
    """Clamp a JSON numeric value; invalid/nonfinite input uses the default."""
    try:
        number = float(value) if not isinstance(value, bool) else default
    except (TypeError, ValueError, OverflowError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(minimum, min(maximum, number))
