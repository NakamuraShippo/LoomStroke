"""Prepare symmetric pattern regions in the existing bucket worker."""

from types import SimpleNamespace

from loomstroke.core.fill.prepared_pattern_command import PreparedPatternFillCommand

from ..basic_fill_bucket.fill_preparation_controller import FillPreparationController
from ..sdk.drawing_assist import ToolAssistBridge


class PatternDrawingAssist:
    def __init__(self, owner):
        self.owner = owner
        self.bridge = ToolAssistBridge("pattern_fill")
        self.adapter = SimpleNamespace(host_api=None)
        self.preparation = FillPreparationController(self.adapter, command_factory=self._command)
        self.pattern = None
        self.scale = 1.0

    def down(self, point):
        if self.preparation._worker is not None:
            return True
        self.bridge.capture(self.owner.host_api)
        session = self.bridge.session
        if session is None or not session.has_copies:
            self.bridge.end()
            return False
        self.pattern = self.owner._current_pattern.copy()
        self.scale = self.owner._pattern_scale
        self.adapter.host_api = self.owner.host_api
        self.adapter._assist = self.bridge
        self.adapter._active_color_rgba = lambda: (0.0, 0.0, 0.0, 1.0)
        self.adapter.settings = SimpleNamespace(
            reference_mode="Active Layer",
            fill_mode="Bucket",
            fill_target="active_layer",
            color_tolerance=self.owner._tolerance,
            gap_close_px=self.owner._gap_close_px,
            area_expansion=0,
        )
        self.preparation.prepare(point)
        self.bridge.end()
        return True

    def _command(self, snapshot, spans, bounds, _color):
        return PreparedPatternFillCommand(snapshot, spans, bounds, self.pattern, self.scale)

    def cancel(self):
        self.bridge.end()
        self.preparation.cancel()
        self.adapter.host_api = None
