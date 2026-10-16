"""Edit existing stroke-profile stops through one undoable vector replacement."""

from __future__ import annotations

from typing import Any

from loomstroke.core.vector_correction.stroke_profile import StrokeProfile

from ..sdk.tool import ContextWidgetSpec
from .common import VectorReplaceCommand, copy_vector_object
from .stroke_profile import StrokeProfileAdapter


class VectorWidthActions:
    def __init__(self, tool: Any, *, ask_value: Any = None) -> None:
        self._tool = tool
        self._ask_value = ask_value or self._ask_with_dialog

    def widgets(self) -> list[ContextWidgetSpec]:
        return [
            ContextWidgetSpec(
                key="vector_width_stop",
                kind="button",
                label=self._ts("Edit Width Stop"),
                on_clicked=self.edit,
                row=2,
            ),
            ContextWidgetSpec(
                key="vector_width_reset",
                kind="button",
                label=self._ts("Reset Width Profile"),
                on_clicked=self.reset,
                row=2,
            ),
        ]

    def edit(self) -> bool:
        obj = self._committed_object()
        if obj is None:
            return False
        position = self._ask_value(
            self._ts("Edit Width Stop"), self._ts("Position along stroke (%)"), 50.0, 0.0, 100.0
        )
        if position is None:
            return False
        profile = StrokeProfileAdapter.read(obj) or StrokeProfile(base_width=obj.style.stroke_width)
        t = position / 100.0
        width = self._ask_value(
            self._ts("Edit Width Stop"),
            self._ts("Width at this position (px)"),
            profile.width_at(t),
            0.0,
            512.0,
        )
        if width is None:
            return False
        return self.set_stop(t, width)

    def set_stop(self, position: float, width: float) -> bool:
        import math  # noqa: PLC0415

        obj = self._committed_object()
        if obj is None or not math.isfinite(position) or not math.isfinite(width):
            return False
        profile = StrokeProfileAdapter.read(obj) or StrokeProfile(base_width=obj.style.stroke_width)
        t = min(1.0, max(0.0, position))
        stops = list(profile.stops or profile.samples or ((0.0, 1.0), (1.0, 1.0)))
        stops = [(at, scale) for at, scale in stops if abs(at - t) > profile.EPSILON]
        stops.append((t, max(0.0, min(512.0, width)) / max(0.1, profile.base_width)))
        return self._submit(StrokeProfileAdapter.write(obj, profile.with_stops(stops)))

    def reset(self) -> bool:
        obj = self._committed_object()
        if obj is None or StrokeProfileAdapter.read(obj) is None:
            return False
        return self._submit(StrokeProfileAdapter.write(obj, None))

    def _committed_object(self) -> Any | None:
        """Read the document geometry rather than provisional editor handles."""
        selection = self._tool._selected
        canvas = getattr(getattr(self._tool, "host_api", None), "canvas", None)
        if selection is None or canvas is None:
            return None
        snapshot = canvas.get_vector_state_snapshot()
        layer = snapshot.layer(selection.layer_id)
        if layer is None:
            return None
        return next((obj for obj in layer.vector_objects if obj.id == selection.vec_id), None)

    def _submit(self, obj: Any) -> bool:
        selection = self._tool._selected
        canvas = getattr(getattr(self._tool, "host_api", None), "canvas", None)
        if selection is None or canvas is None:
            return False
        if not canvas.submit(
            VectorReplaceCommand(selection.vec_id, obj, layer_id=selection.layer_id)
        ):
            return False
        self._tool._editing_object = copy_vector_object(obj)
        self._tool._selected_object_cache = obj
        return True

    @staticmethod
    def _ts(source: str) -> str:
        from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

        return translated_literal_domain("VectorTools", source, use_fallback=True)

    @staticmethod
    def _ask_with_dialog(
        title: str, prompt: str, value: float, lower: float, upper: float
    ) -> float | None:
        from PySide6.QtWidgets import QInputDialog  # noqa: PLC0415

        result, accepted = QInputDialog.getDouble(None, title, prompt, value, lower, upper, 2)
        return result if accepted else None
