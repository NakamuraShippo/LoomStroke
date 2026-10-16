"""Screentone model and CPU reference (CT-02).

- :mod:`screentone_params` — normalized definition + stable ids
- :mod:`screentone_normalizer` — raw ``effect_params`` <-> params
- :mod:`screentone_validator` — DPI / LPI / pitch issue codes
- :mod:`spot_functions` — AM spot functions and density thresholds
- :mod:`screentone_renderer` — deterministic coverage / RGBA rendering
"""

from .screentone_normalizer import ScreentoneNormalizer
from .screentone_params import (
    MAX_LPI,
    MIN_LPI,
    MIN_PITCH_PX,
    ORIGIN_MODES,
    PATTERNS,
    PREVIEW_POLICIES,
    SCHEMA_VERSION,
    ScreentoneParams,
)
from .screentone_renderer import ScreentoneRenderer
from .screentone_validator import DEFAULT_DOCUMENT_DPI, ScreentoneValidator
from .spot_functions import SpotFunctionTable
from .tone_issue import ToneIssue

__all__ = [
    "DEFAULT_DOCUMENT_DPI",
    "MAX_LPI",
    "MIN_LPI",
    "MIN_PITCH_PX",
    "ORIGIN_MODES",
    "PATTERNS",
    "PREVIEW_POLICIES",
    "SCHEMA_VERSION",
    "ScreentoneNormalizer",
    "ScreentoneParams",
    "ScreentoneRenderer",
    "ScreentoneValidator",
    "SpotFunctionTable",
    "ToneIssue",
]
