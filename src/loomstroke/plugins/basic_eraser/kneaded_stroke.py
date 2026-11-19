"""The eraser's kneaded type: press and drag to lift paint a little at a time."""

from __future__ import annotations

from typing import Any, ClassVar

from loomstroke.core.paint_tool_session import PaintToolCommand
from loomstroke.plugins.paint_tool.sampling import PaintDab
from loomstroke.plugins.paint_tool.stroke_input import SampledMediaInput
from loomstroke.ui.translations import translated_literal_domain

MAX_DIAMETER = 128


def _ts(source: str) -> str:
    return translated_literal_domain("BasicEraser", source, use_fallback=True)


class KneadedStroke(SampledMediaInput):
    """Drive one provisional lifting stroke for the eraser tool.

    The eraser samples the input into dabs, previews it and commits at pen-up. Each
    stroke starts with a freshly kneaded lump, which fills up and lifts less as it goes.
    """

    TOOL_ID = "basic_eraser"
    _translate = staticmethod(_ts)
    _NOTICES: ClassVar[dict[str, str]] = {
        "symmetry": "Turn off symmetry copies to use the kneaded eraser.",
        "failed": "The eraser stroke could not be completed.",
        "layer": "Use an editable RGB raster layer for the kneaded eraser.",
    }

    def __init__(self, eraser: Any) -> None:
        super().__init__()
        self.eraser = eraser

    @property
    def active(self) -> bool:
        return self._stroke_id is not None

    def on_pointer_down(self, pt: Any, **modifiers: bool) -> None:
        # The eraser's size is in canvas pixels like the lump's diameter.
        self.size = max(1, min(MAX_DIAMETER, round(self.eraser.settings.size)))
        super().on_pointer_down(pt, **modifiers)

    def cancel(self) -> None:
        self._cancel_interrupted_stroke()

    def _begin_command(self, token: object, first: PaintDab) -> PaintToolCommand | None:
        settings = self.eraser.settings
        strength = max(0.0, min(1.0, float(settings.opacity)))
        if strength == 0.0:
            return None
        return PaintToolCommand(
            "begin",
            token,
            (first,),
            medium="knead",
            diameter=self.size,
            opacity=strength,
            # The eraser's hardness shapes the rim of the lump as it does the normal eraser.
            tip=("", max(0.0, min(1.0, float(settings.hardness))), "radial"),
            wear=(max(0.0, min(100.0, float(settings.durability))) / 100.0, 0.5, 0.5),
        )
