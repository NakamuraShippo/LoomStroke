"""Drive a sampled, provisional medium stroke for a tool on the host canvas."""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import ClassVar

from loomstroke.core.paint_tool_session import PaintToolCommand
from loomstroke.host_api import HostAPI, StrokePoint
from loomstroke.plugins.sdk.drawing_assist import ToolAssistBridge

from .sampling import PaintDab, PaintDabSampler

logger = logging.getLogger(__name__)


class SampledMediaInput(ABC):
    TOOL_ID: ClassVar[str]
    _NOTICES: ClassVar[dict[str, str]]

    def __init__(self) -> None:
        self.host_api: HostAPI | None = None
        self.size = 32
        self._assist = ToolAssistBridge(self.TOOL_ID)
        self._stroke_id: object | None = None
        self._sampler: PaintDabSampler | None = None

    @staticmethod
    @abstractmethod
    def _translate(source: str) -> str: ...

    @abstractmethod
    def _begin_command(self, token: object, first: PaintDab) -> PaintToolCommand | None: ...

    def on_pointer_down(self, pt: StrokePoint, **_modifiers: bool) -> None:
        self._cancel_interrupted_stroke()
        host = self.host_api
        if host is None:
            return
        pt = self._assist.begin(host, pt)
        if self._assist.session and self._assist.session.has_copies:
            self._assist.end()
            self._say("symmetry")
            return
        if not all(math.isfinite(coord) for coord in pt.pos):
            self._assist.end()
            return
        sampler = PaintDabSampler(pt, self.size)
        token = object()
        begin = self._begin_command(token, sampler.last_dab)
        if begin is None:
            self._assist.end()
            return
        accepted = self._send(begin)
        if not accepted:
            self._assist.end()
            if accepted is None:
                # A failed call may already have created the session before raising.
                self._send(PaintToolCommand("cancel", token))
                self._say("failed")
            else:
                self._say("layer")
            return
        self._stroke_id, self._sampler = token, sampler
        host.input.pointer_capture(self.TOOL_ID)

    def _say(self, notice: str) -> None:
        if self.host_api:
            self.host_api.ui.show_message(self._translate(self._NOTICES[notice]))

    def on_pointer_move(self, points: Iterable[StrokePoint]) -> None:
        if self._sampler is None:
            return
        dabs = [xy for pt in points for xy in self._sampler.advance(self._assist.point(pt))]
        if dabs:
            self._submit("move", tuple(dabs))

    def on_pointer_up(self, pt: StrokePoint) -> None:
        if self._sampler is None:
            return
        dabs = self._sampler.advance(self._assist.point(pt), lift=True)
        self._submit("end", tuple(dabs))
        self._release()

    def _submit(self, phase: str, dabs: tuple[PaintDab, ...]) -> None:
        host = self.host_api
        if host and not self._send(PaintToolCommand(phase, self._stroke_id, dabs)):
            self._send(PaintToolCommand("cancel", self._stroke_id))
            self._release()
            self._say("failed")

    def _send(self, command: PaintToolCommand) -> bool | None:
        if self.host_api is None:
            return False
        try:
            return self.host_api.canvas.submit(command)
        except Exception:
            logger.exception("Paint tool command %s failed", command.phase)
            return None

    def _release(self) -> None:
        if self._stroke_id is not None and self.host_api:
            self.host_api.input.release_capture(self.TOOL_ID)
        self._sampler = self._stroke_id = None
        self._assist.end()

    def _cancel_interrupted_stroke(self) -> None:
        if self._stroke_id is not None and self.host_api:
            accepted = self._send(PaintToolCommand("cancel", self._stroke_id))
            self._release()
            if accepted is None:
                self._say("failed")

    def can_undo(self) -> bool:
        return self._stroke_id is not None

    def on_undo(self) -> bool:
        # Undo while the pen is down removes the unfinished stroke and leaves history untouched.
        if self._stroke_id is None:
            return False
        self._cancel_interrupted_stroke()
        return True

    def on_key(self, key: str, pressed: bool) -> bool:
        if self._stroke_id is not None and pressed and key.lower() in {"esc", "escape"}:
            self._cancel_interrupted_stroke()
            return True
        return False
