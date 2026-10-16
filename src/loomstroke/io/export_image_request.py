"""What the Export Image dialog hands to the exporter (CT-09 spec §6/§8).

Pure data: format id, colour policy, alpha policy and per-format encoder
options.  The GUI fills it in; :mod:`export_color_policy` and
:func:`raster_export.export_raster` consume it.  Stable ids only — display
names live in the dialog.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .export_contract import capabilities

ColorPolicy = Literal["embed", "untagged", "convert_srgb"]
AlphaMode = Literal["preserve", "matte"]

COLOR_POLICIES: tuple[ColorPolicy, ...] = ("embed", "untagged", "convert_srgb")
ALPHA_MODES: tuple[AlphaMode, ...] = ("preserve", "matte")


@dataclass(frozen=True, slots=True)
class ExportImageRequest:
    """One export as the user configured it."""

    format_id: str
    color_policy: ColorPolicy = "embed"
    alpha_mode: AlphaMode = "preserve"
    matte_rgba: tuple[int, int, int, int] | None = None
    encoder_options: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_layered(self) -> bool:
        """PSD / SVG keep their own exporter (layers / vector objects)."""
        return capabilities(self.format_id).layered
