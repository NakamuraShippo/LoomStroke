"""Path Edit context-toolbar actions: Simplify / Smooth / Straighten / Offset (CT-15 Phase 2).

Thin delegate kept out of ``edit_tool.py``: asks for one numeric value, runs
``PathCorrectionService`` on the selected object and submits a single
``VectorReplaceCommand`` (1 operation = 1 undo).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from ..sdk.tool import ContextWidgetSpec
from .common import VectorReplaceCommand, copy_vector_object
from .path_correction import PathCorrectionService

__all__ = ["PathCorrectionActions"]

AskValue = Callable[[str, str, float], float | None]


class PathCorrectionActions:
    DEFAULTS: ClassVar[dict[str, float]] = {
        "simplify": 1.5,
        "smooth": 0.5,
        "straighten": 1.0,
        "offset": 4.0,
    }
    LABELS: ClassVar[dict[str, str]] = {
        "simplify": "Simplify",
        "smooth": "Smooth",
        "straighten": "Straighten",
        "offset": "Offset",
    }
    PROMPTS: ClassVar[dict[str, str]] = {
        "simplify": "Tolerance (px)",
        "smooth": "Strength (0-1)",
        "straighten": "Amount (0-1)",
        "offset": "Distance (px, negative = other side)",
    }

    def __init__(self, tool: Any, *, ask_value: AskValue | None = None) -> None:
        self._tool = tool
        self._ask_value = ask_value or self._ask_with_dialog
        self._service = PathCorrectionService()
        self.last_message = ""

    def widgets(self) -> list[ContextWidgetSpec]:
        return [
            ContextWidgetSpec(
                key=f"path_correction_{kind}",
                kind="button",
                label=self._ts(label),
                on_clicked=lambda kind=kind: self.apply(kind),
                row=1,
            )
            for kind, label in self.LABELS.items()
        ]

    def apply(self, kind: str) -> bool:
        prepared = self._prepare(kind)
        if prepared is None:
            return False
        selection, new_obj = prepared
        return self._submit(selection, new_obj, kind)

    def _prepare(self, kind: str) -> tuple[Any, Any] | None:
        if kind not in self.DEFAULTS:
            return None
        selection = getattr(self._tool, "_selected", None)
        obj = self._tool._current_selection_object()  # noqa: SLF001 - same plugin
        if selection is None or obj is None:
            self.last_message = "no_selection"
            return None
        value = self._ask_value(
            self._ts(self.LABELS[kind]), self._ts(self.PROMPTS[kind]), self.DEFAULTS[kind]
        )
        if value is None:
            self.last_message = "cancelled"
            return None
        new_obj = self._run(kind, obj, float(value))
        if new_obj is None:
            self.last_message = "no_change"
            return None
        return selection, new_obj

    def _submit(self, selection: Any, new_obj: Any, kind: str) -> bool:
        canvas = getattr(getattr(self._tool, "host_api", None), "canvas", None)
        if canvas is None:
            self.last_message = "no_canvas"
            return False
        command = VectorReplaceCommand(selection.vec_id, new_obj, layer_id=selection.layer_id)
        try:
            ok = bool(canvas.submit(command, merge_key=None))
        except Exception:
            ok = False
        if not ok:
            self.last_message = "command_rejected"
            return False
        self._tool._editing_object = copy_vector_object(new_obj)  # noqa: SLF001
        self._tool._selected_object_cache = new_obj  # noqa: SLF001
        self.last_message = kind
        return True

    def _run(self, kind: str, obj: Any, value: float) -> Any | None:
        if kind == "simplify":
            return self._service.simplify(obj, value)
        if kind == "smooth":
            return self._service.smooth(obj, strength=value)
        if kind == "straighten":
            return self._service.straighten(obj, amount=value)
        return self._service.offset(obj, value)

    @staticmethod
    def _ts(text: str) -> str:
        from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

        return translated_literal_domain("VectorTools", text, use_fallback=True)

    def _ask_with_dialog(self, title: str, prompt: str, default: float) -> float | None:
        from PySide6.QtWidgets import QInputDialog  # noqa: PLC0415

        value, ok = QInputDialog.getDouble(None, title, prompt, default, -10_000.0, 10_000.0, 2)
        return float(value) if ok else None
