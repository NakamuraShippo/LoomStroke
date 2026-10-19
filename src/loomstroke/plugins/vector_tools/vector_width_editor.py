"""Preview direct width drags and publish one existing VectorReplaceCommand."""

from __future__ import annotations

import logging
import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from loomstroke.core.events import Events
from loomstroke.core.vector_correction import StrokeProfile

from ..common.vector_hit_test_geometry import VectorHitTestGeometry
from .parametric_shapes import is_editable_parametric_shape
from .stroke_profile import StrokeProfileAdapter
from .vector_width_geometry import WidthPathGeometry
from .vector_width_overlay import VectorWidthOverlay

logger = logging.getLogger(__name__)


@dataclass
class _WidthDrag:
    original: Any
    context: tuple
    profile: StrokeProfile
    t: float
    normal: tuple[float, float]
    pointer: tuple[float, float]
    side: float
    draft: Any = None


class VectorWidthEditor:
    def __init__(self, tool: Any) -> None:
        self._tool = tool
        self.enabled = False
        self._drag: _WidthDrag | None = None
        self._geometry: WidthPathGeometry | None = None
        self._cached_object: Any = None
        self._overlay = VectorWidthOverlay()
        self._bus: Any = None
        self._tokens: list[tuple[str, Any]] = []
        # EventBus keeps weak references, including bound methods.
        self._change_handler = self._changed

    def set_enabled(self, enabled: bool) -> None:
        self.cancel()
        self.enabled = enabled
        if not enabled:
            for event, token in self._tokens:
                self._bus.off(event, token)
            self._tokens.clear()
            self._bus = None
            return
        bus = getattr(self._tool.host_api, "events", None)
        if not self._tokens and callable(getattr(bus, "subscribe", None)):
            self._bus = bus
            for event in (Events.DOC_CHANGED, Events.ACTIVE_PAGE_CHANGING, Events.HISTORY_CHANGED):
                self._tokens.append((event, bus.subscribe(event, self._change_handler)))
        self.hover()

    def _changed(self, _payload: Any) -> None:
        self.cancel()

    def _context(self) -> tuple:
        host = self._tool.host_api
        canvas = host.canvas
        page_id = getattr(getattr(host, "pages", None), "get_active_page_id", lambda: None)()
        return (
            id(canvas),
            page_id,
            canvas.current_document().id,
            canvas.current_layer().id,
            canvas.history_undo_count(),
            canvas.history_redo_count(),
        )

    def _object(self) -> Any:
        selection = self._tool._selected
        host = self._tool.host_api
        if selection is None or host is None:
            return None
        canvas = host.canvas
        selected = canvas.get_vector_state_snapshot().selection
        if not isinstance(selected, dict) or (selected.get("id"), selected.get("layer_id")) != (
            selection.vec_id,
            selection.layer_id,
        ):
            return None
        nodes = {layer.id: layer for layer in canvas.get_layer_tree_snapshot().layers}
        node = nodes.get(selection.layer_id)
        if node is None or node.layer_type != "vector" or node.vector_kind != "draw":
            return None
        while node is not None:
            info = canvas.get_layer_info(node.id)
            if info is None or info.get("locked") or not info.get("visible", True):
                return None
            node = nodes.get(node.parent_id)
        return self._tool._width_actions()._committed_object()

    def _prepare(self) -> tuple[Any, StrokeProfile] | None:
        obj = self._object()
        if obj is None or is_editable_parametric_shape(obj) or obj.style.stroke_width <= 0:
            return None
        if obj is not self._cached_object:
            self._geometry = WidthPathGeometry.build(obj)
            self._cached_object = obj
        if self._geometry is None:
            return None
        profile = StrokeProfileAdapter.read(obj) or StrokeProfile(base_width=obj.style.stroke_width)
        # Preserve dense source samples rather than silently decimating their shape
        # into the 64-stop editing representation.
        if len(profile.curve) > profile.MAX_STOPS:
            return None
        return obj, profile

    def hover(self) -> None:
        if self._drag is not None:
            return
        prepared = self._prepare()
        if prepared is None:
            self._overlay.clear()
        else:
            self._overlay.show(self._tool.host_api, self._geometry, prepared[1])

    @staticmethod
    def _position(point: Any) -> tuple[float, float] | None:
        position = tuple(map(float, point.pos))
        return position if all(math.isfinite(v) for v in position) else None

    def down(self, point: Any, *, alt: bool = False) -> None:
        self.cancel()
        position = self._position(point)
        if position is None:
            return
        prepared = self._prepare()
        if prepared is None:
            self._notify("Select an unlocked single-path stroke with at most 64 width samples.")
            return
        obj, profile = prepared
        radius = VectorHitTestGeometry(self._tool.host_api).canvas_radius(7)
        stop_hit = self._hit_stop(position, profile, radius)
        if alt:
            if stop_hit is not None and profile.stops:
                t = stop_hit[0]
                stops = [(at, scale) for at, scale in profile.stops if abs(at - t) > 1e-9]
                if len(stops) != len(profile.stops):
                    self._submit(StrokeProfileAdapter.write(obj, profile.with_stops(stops)))
            self.hover()
            return
        t, _center, normal, distance = self._geometry.nearest(position)
        side = 1.0
        if stop_hit is not None:
            t, side = stop_hit
            _center, normal = self._geometry.at(t)
        elif distance > max(radius, profile.width_at(t) * 0.5):
            self.hover()
            return
        stops = profile.curve or ((0.0, 1.0), (1.0, 1.0))
        if len(stops) >= profile.MAX_STOPS and not any(
            abs(at - t) <= profile.EPSILON for at, _ in stops
        ):
            self._notify("This stroke already has 64 width stops.")
            return
        self._drag = _WidthDrag(deepcopy(obj), self._context(), profile, t, normal, position, side)
        self._tool.host_api.input.pointer_capture(self._tool.descriptor().id)

    def _hit_stop(self, position: tuple, profile: StrokeProfile, radius: float) -> tuple | None:
        best, result = radius, None
        for t, _scale in profile.curve:
            center, normal = self._geometry.at(t)
            for side in (-1.0, 1.0, 0.0):
                half = side * profile.width_at(t) * 0.5
                distance = math.hypot(
                    position[0] - center[0] - normal[0] * half,
                    position[1] - center[1] - normal[1] * half,
                )
                if distance <= best:
                    best, result = distance, (t, side or 1.0)
        return result

    def _valid_drag(self) -> bool:
        drag = self._drag
        return bool(drag and self._context() == drag.context and self._object() == drag.original)

    def move(self, point: Any) -> None:
        if self._drag is None:
            return
        if not self._valid_drag():
            self.cancel()
            return
        position = self._position(point)
        if position is None:
            self.cancel()
            return
        drag = self._drag
        delta = sum((position[i] - drag.pointer[i]) * drag.normal[i] for i in (0, 1))
        width = min(512.0, max(0.0, drag.profile.width_at(drag.t) + 2 * delta * drag.side))
        if abs(width - drag.profile.width_at(drag.t)) < 1e-6:
            draft = drag.original
        else:
            stops = list(drag.profile.curve or ((0.0, 1.0), (1.0, 1.0)))
            stops = [(t, s) for t, s in stops if abs(t - drag.t) > drag.profile.EPSILON]
            stops.append((drag.t, width / drag.profile.base_width))
            draft = StrokeProfileAdapter.write(drag.original, drag.profile.with_stops(stops))
        drag.draft = draft
        canvas = self._tool.host_api.canvas
        canvas._vector_preview = draft
        canvas._vector_preview_layer_id = self._tool._selected.layer_id
        canvas.notify_vector_preview_changed()
        profile = StrokeProfileAdapter.read(draft) or drag.profile
        self._overlay.show(self._tool.host_api, self._geometry, profile)

    def up(self, point: Any) -> None:
        self.move(point)
        drag = self._drag
        if drag is None:
            return
        changed = drag.draft is not None and drag.draft != drag.original
        valid = self._valid_drag()
        self.cancel()
        if valid and changed:
            self._submit(drag.draft)
        self.hover()

    def _submit(self, obj: Any) -> bool:
        try:
            applied = self._tool._width_actions()._submit(obj)
        except Exception:
            logger.exception("Direct vector width edit failed")
            applied = False
        if not applied:
            self._notify("The width edit could not be applied. The original stroke was preserved.")
        return applied

    def cancel(self) -> None:
        host = self._tool.host_api
        drag, self._drag = self._drag, None
        if drag is not None and host is not None:
            host.input.release_capture(self._tool.descriptor().id)
            if host.canvas.get_vector_state_snapshot().preview_object is drag.draft:
                self._tool._clear_canvas_preview()
        self._geometry, self._cached_object = None, None
        self._overlay.clear()

    def _notify(self, source: str) -> None:
        show = getattr(getattr(self._tool.host_api, "ui", None), "show_message", None)
        if callable(show):
            show(self._tool._width_actions()._ts(source))
