"""Explicit, session-only drawing return; no hover or stroke-driven selection."""

from __future__ import annotations

from dataclasses import dataclass

from .models import PanelFrameError


@dataclass(frozen=True, slots=True)
class DrawingTargetState:
    layer_id: str
    panel_id: str
    drawable: bool = True
    visible: bool = True
    locked: bool = False
    mask_editing: bool = False


class PanelDrawingTargets:
    def __init__(self) -> None:
        self._targets: dict[tuple[str, str], str] = {}

    def remember(self, page_id: str, panel_id: str, target: DrawingTargetState) -> None:
        if not page_id or not panel_id or not target.layer_id or target.panel_id != panel_id:
            raise PanelFrameError("drawing target does not belong to this panel")
        self._validate(target)
        self._targets[(page_id, panel_id)] = target.layer_id

    def resolve(
        self,
        page_id: str,
        panel_id: str,
        states: dict[str, DrawingTargetState],
        *,
        input_busy: bool = False,
    ) -> str:
        if input_busy:
            raise PanelFrameError("finish the current stroke or text composition first")
        layer_id = self._targets.get((page_id, panel_id))
        if layer_id is None:
            raise PanelFrameError("choose a drawing target for this panel first")
        target = states.get(layer_id)
        if target is None or target.panel_id != panel_id:
            raise PanelFrameError("recorded drawing target is missing or moved")
        self._validate(target)
        return layer_id

    @staticmethod
    def _validate(target: DrawingTargetState) -> None:
        if not target.drawable:
            raise PanelFrameError("recorded target is not drawable with the current tool")
        if not target.visible:
            raise PanelFrameError("recorded drawing target is hidden")
        if target.locked:
            raise PanelFrameError("recorded drawing target is locked")
        if target.mask_editing:
            raise PanelFrameError("finish mask editing before returning to drawing")
