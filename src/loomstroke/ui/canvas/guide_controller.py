"""Guide and ruler interaction controller (Photoshop-style)."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import uuid4

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen

from ...core.events import Events
from ...core.guide_layout_geometry import GuidePosition, canvas_snap_positions, numeric_guide
from ...host_api import HostAPI
from ...platform.settings_manager import SettingsManager
from .guide_canvas_bridge import update_guide_overlay
from .guide_models import Guide, GuideOrientation, RulerState
from .guide_position_overlay import draw_guide_position
from .rotated_guides import projected_axis, segment, snap_rotated, stored_transform

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SnapResult:
    value: float
    snapped: bool


class GuideController:
    """Manages guide state, interaction, and rendering."""

    def __init__(
        self,
        *,
        canvas: object,
        host: HostAPI | None,
        settings: SettingsManager,
    ) -> None:
        self._canvas = canvas
        self._host = host
        self._settings = settings
        self._guides: list[Guide] = []
        self._selected_id: str | None = None
        self._dragging = False
        self._drag_guide_id: str | None = None
        self._drag_orientation: GuideOrientation | None = None
        self._drag_original_pos: float | None = None
        self._drag_preview_pos: float | None = None
        self._drag_last_inside_pos: float | None = None
        self._drag_new_guide = False
        self._drag_pointer_pos: QPointF | None = None
        self.guides_visible = bool(self._settings.get_bool("view/guides_visible", True))
        self.guides_locked = bool(self._settings.get_bool("view/guides_locked", False))
        self._canvas_edge_snap = bool(self._settings.get_bool("view/canvas_edge_snap", True))
        self._canvas_center_snap = bool(self._settings.get_bool("view/canvas_center_snap", False))
        self._snap_active = False
        self._snap_position: float | None = None
        self._extra_snap_points: list[tuple[float, float]] = []
        self._ruler_update_cb: Callable[[], None] | None = None

        unit_value = self._settings.get_str("view/ruler_unit", "px")
        if unit_value not in ("px", "mm", "inch"):
            unit_value = "px"
        self._ruler_state = RulerState(
            unit=cast("Literal['px', 'mm', 'inch']", unit_value),
            origin=QPointF(0.0, 0.0),
        )

        self._guides_color = QColor(self._settings.get_str("view/guides_color", "#00B7FF"))
        self._guides_opacity = float(self._settings.get_float("view/guides_opacity", 0.8))
        self._hit_slop_px = int(self._settings.get_int("view/guides_hit_slop_px", 6))
        self._snap_threshold_px = int(self._settings.get_int("view/guides_snap_threshold_px", 8))
        self._ruler_thickness_px = int(self._settings.get_int("view/ruler_thickness_px", 18))

        self._settings.settings_changed.connect(self._on_setting_changed)
        self._subscribe_page_events()
        self._load_active_page_state()

    # ------------------------------------------------------------------
    # Public accessors
    def guides(self) -> Sequence[Guide]:
        return list(self._guides)

    def reload_page_state(self) -> None:
        """Refresh guides after another page view-state writer applies a template."""
        self._reset_drag_state()
        self._load_active_page_state()

    def apply_guides(
        self,
        positions: Iterable[GuidePosition],
        *,
        replace: bool = False,
        rotation: float = 0,
        pivot: tuple | None = None,
    ) -> None:
        """Apply validated canvas-space guides through the existing page-state writer."""
        if replace and self.guides_locked:
            raise ValueError("Guides are locked")
        if not math.isfinite(rotation) or (
            pivot is not None and (len(pivot) != 2 or not all(math.isfinite(v) for v in pivot))
        ):
            raise ValueError("Invalid guide rotation")
        pivot = tuple(pivot) if pivot is not None else None
        width, height, dpi = self._active_canvas_spec()
        validated = [
            numeric_guide(axis, value, unit="px", dpi=dpi, size=(width, height))
            for axis, value in positions
        ]
        additions = [
            Guide(
                id=self._new_guide_id(),
                orientation=axis,
                position=value,
                rotation=rotation % 360,
                pivot=pivot,
            )
            for axis, value in validated
        ]
        self._reset_drag_state()
        self._selected_id = None
        self._guides = additions if replace else [*self._guides, *additions]
        self._persist_view_state()
        self._request_update()

    def ruler_state(self) -> RulerState:
        return self._ruler_state

    def document_dpi(self) -> tuple[float, float]:
        spec = self._active_canvas_spec()
        return spec[2]

    def set_extra_snap_points(self, points: Iterable[tuple[float, float]]) -> None:
        self._extra_snap_points = list(points)

    def set_ruler_update_callback(self, callback: Callable[[], None] | None) -> None:
        self._ruler_update_cb = callback

    # ------------------------------------------------------------------
    # Ruler origin
    def set_ruler_origin(self, origin: QPointF, *, persist: bool = True) -> None:
        self._ruler_state.origin = QPointF(origin.x(), origin.y())
        if persist:
            self._persist_view_state()
        self._request_update()

    def reset_ruler_origin(self) -> None:
        self.set_ruler_origin(QPointF(0.0, 0.0), persist=True)

    # ------------------------------------------------------------------
    # Guide manipulation
    def begin_ruler_drag(self, orientation: GuideOrientation, canvas_pos: QPointF) -> None:
        if not self.guides_visible or self.guides_locked:
            return
        self._drag_pointer_pos = canvas_pos
        self._dragging = True
        self._drag_new_guide = True
        self._drag_orientation = orientation
        self._drag_preview_pos = self._axis_value(orientation, canvas_pos)
        self._snap_active = False
        self._snap_position = None
        self._request_update()

    def update_ruler_drag(self, canvas_pos: QPointF) -> None:
        if not self._dragging or not self._drag_new_guide or self._drag_orientation is None:
            return
        value = self._axis_value(self._drag_orientation, canvas_pos)
        self._drag_pointer_pos = canvas_pos
        snap = self._snap_value(value, self._drag_orientation, exclude_id=None)
        self._drag_preview_pos = snap.value
        self._snap_active = snap.snapped
        self._snap_position = snap.value if snap.snapped else None
        self._show_snap_feedback(snap)
        self._request_update()

    def end_ruler_drag(self, canvas_pos: QPointF) -> None:
        if not self._dragging or not self._drag_new_guide or self._drag_orientation is None:
            return
        in_canvas = self._is_inside_canvas(canvas_pos)
        if in_canvas and self._drag_preview_pos is not None:
            guide = Guide(
                id=self._new_guide_id(),
                orientation=self._drag_orientation,
                position=float(self._drag_preview_pos),
            )
            self._guides.append(guide)
            self._selected_id = guide.id
            self._persist_view_state()
        self._reset_drag_state()
        self._request_update()

    def handle_mouse_press(self, event: QMouseEvent, canvas_pos: QPointF) -> bool:
        if not self.guides_visible or self.guides_locked:
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        hit = self._hit_test(canvas_pos)
        if hit is None:
            if self._selected_id is not None:
                self._selected_id = None
                self._request_update()
            return False
        self._selected_id = hit.id
        self._drag_pointer_pos = canvas_pos
        self._dragging = True
        self._drag_new_guide = False
        self._drag_guide_id = hit.id
        self._drag_orientation = hit.orientation
        self._drag_original_pos = hit.position
        self._drag_preview_pos = hit.position
        self._drag_last_inside_pos = hit.position
        self._snap_active = False
        self._snap_position = None
        self._request_update()
        return True

    def handle_mouse_move(self, event: QMouseEvent, canvas_pos: QPointF) -> bool:
        if not self._dragging or self._drag_orientation is None:
            return False
        dragged = self._guide_by_id(self._drag_guide_id)
        value = (
            projected_axis(dragged, canvas_pos, self._active_canvas_spec()[:2])
            if dragged is not None and dragged.rotation % 360
            else self._axis_value(self._drag_orientation, canvas_pos)
        )
        self._drag_pointer_pos = canvas_pos
        snap = (
            SnapResult(value, False)
            if dragged is not None and dragged.rotation % 360
            else self._snap_value(value, self._drag_orientation, exclude_id=self._drag_guide_id)
        )
        new_value = snap.value
        self._snap_active = snap.snapped
        self._snap_position = snap.value if snap.snapped else None
        self._show_snap_feedback(snap)
        if self._is_inside_canvas(canvas_pos):
            self._drag_last_inside_pos = new_value
        if self._drag_new_guide:
            self._drag_preview_pos = new_value
        else:
            guide = self._guide_by_id(self._drag_guide_id)
            if guide is not None:
                guide.position = float(new_value)
        self._request_update()
        return True

    def handle_mouse_release(self, event: QMouseEvent, canvas_pos: QPointF) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if not self._dragging:
            return False
        consumed = True
        if self._drag_new_guide:
            self.end_ruler_drag(canvas_pos)
        else:
            if not self._is_inside_canvas(canvas_pos):
                if self._is_release_on_ruler(canvas_pos):
                    guide = self._guide_by_id(self._drag_guide_id)
                    if guide is not None:
                        if self._drag_last_inside_pos is not None:
                            guide.position = float(self._drag_last_inside_pos)
                        elif self._drag_original_pos is not None:
                            guide.position = float(self._drag_original_pos)
                    self._persist_view_state()
                else:
                    self._delete_selected_guide()
            else:
                self._persist_view_state()
            self._reset_drag_state()
            self._request_update()
        return consumed

    def handle_key_press(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._selected_id is not None:
            self._delete_selected_guide()
            self._request_update()
            return True
        if key == Qt.Key.Key_Escape:
            if self._dragging and not self._drag_new_guide:
                guide = self._guide_by_id(self._drag_guide_id)
                if guide is not None and self._drag_original_pos is not None:
                    guide.position = float(self._drag_original_pos)
                self._reset_drag_state()
                self._request_update()
                return True
            if self._selected_id is not None:
                self._selected_id = None
                self._request_update()
                return True
        return False

    # ------------------------------------------------------------------
    # Rendering
    def draw(self, painter: QPainter) -> None:
        if not self.guides_visible:
            return
        if not self._guides:
            if self._drag_new_guide and self._drag_preview_pos is not None:
                self._draw_preview(painter)
                self._draw_drag_position(painter)
            return
        width, height, _dpi = self._active_canvas_spec()
        base_pen = self._guide_pen(selected=False, snapped=False, preview=False)
        painter.save()
        painter.setPen(base_pen)
        for guide in self._guides:
            if guide.id == self._selected_id:
                continue
            self._draw_guide_line(painter, guide, width, height)
        if self._selected_id is not None:
            selected = self._guide_by_id(self._selected_id)
            if selected is not None:
                painter.setPen(self._guide_pen(selected=True, snapped=False, preview=False))
                self._draw_guide_line(painter, selected, width, height)
        if self._drag_new_guide and self._drag_preview_pos is not None:
            painter.setPen(self._guide_pen(selected=False, snapped=self._snap_active, preview=True))
            self._draw_preview(painter)
        elif self._snap_active and self._drag_orientation and self._snap_position is not None:
            painter.setPen(self._guide_pen(selected=False, snapped=True, preview=True))
            preview = Guide(
                id="snap", orientation=self._drag_orientation, position=self._snap_position
            )
            self._draw_guide_line(painter, preview, width, height)
        painter.restore()
        self._draw_drag_position(painter)

    def _draw_drag_position(self, painter: QPainter) -> None:
        if not self._dragging or self._drag_pointer_pos is None or self._drag_orientation is None:
            return
        guide = self._guide_by_id(self._drag_guide_id)
        value = (
            self._drag_preview_pos if self._drag_new_guide else (guide.position if guide else None)
        )
        if value is not None:
            draw_guide_position(
                painter,
                self._drag_pointer_pos,
                value,
                self._drag_orientation,
                self._ruler_state,
                self.document_dpi(),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    def _draw_preview(self, painter: QPainter) -> None:
        width, height, _dpi = self._active_canvas_spec()
        orientation = self._drag_orientation
        if orientation is None or self._drag_preview_pos is None:
            return
        guide = Guide(id="preview", orientation=orientation, position=self._drag_preview_pos)
        self._draw_guide_line(painter, guide, width, height)

    def _draw_guide_line(
        self,
        painter: QPainter,
        guide: Guide,
        width: int,
        height: int,
    ) -> None:
        if guide.rotation % 360:
            line = segment(guide, (width, height))
            if line is not None:
                painter.drawLine(QPointF(*line[0]), QPointF(*line[1]))
        elif guide.orientation == "horizontal":
            painter.drawLine(QPointF(0.0, guide.position), QPointF(float(width), guide.position))
        else:
            painter.drawLine(QPointF(guide.position, 0.0), QPointF(guide.position, float(height)))

    def _guide_pen(self, *, selected: bool, snapped: bool, preview: bool) -> QPen:
        color = QColor(self._guides_color)
        color.setAlphaF(max(0.0, min(1.0, self._guides_opacity)))
        if preview:
            color = color.lighter(125)
        if snapped:
            color = color.lighter(140)
        if selected:
            color = color.lighter(160)
        pen = QPen(color, 1.0 if not selected else 2.0)
        pen.setCosmetic(True)
        return pen

    def _axis_value(self, orientation: GuideOrientation, canvas_pos: QPointF) -> float:
        return float(canvas_pos.y() if orientation == "horizontal" else canvas_pos.x())

    def _hit_test(self, canvas_pos: QPointF) -> Guide | None:
        if not self.guides_visible or self.guides_locked:
            return None
        threshold = self._screen_to_canvas_distance(self._hit_slop_px)
        best: tuple[float, Guide] | None = None
        for guide in self._guides:
            value = (
                projected_axis(guide, canvas_pos, self._active_canvas_spec()[:2])
                if guide.rotation % 360
                else self._axis_value(guide.orientation, canvas_pos)
            )
            dist = abs(value - guide.position)
            if dist <= threshold and (best is None or dist < best[0]):
                best = (dist, guide)
        return best[1] if best else None

    def _snap_value(
        self,
        value: float,
        orientation: GuideOrientation,
        *,
        exclude_id: str | None,
    ) -> SnapResult:
        threshold_canvas = self._screen_to_canvas_distance(self._snap_threshold_px)
        candidates: list[float] = []
        width, height, _dpi = self._active_canvas_spec()
        extent = height if orientation == "horizontal" else width
        candidates.extend(
            canvas_snap_positions(
                extent, edges=self._canvas_edge_snap, center=self._canvas_center_snap
            )
        )
        if orientation == "horizontal":
            candidates.extend([pt[1] for pt in self._extra_snap_points])
        else:
            candidates.extend([pt[0] for pt in self._extra_snap_points])
        for guide in self._guides:
            if guide.rotation % 360:
                continue
            if exclude_id is not None and guide.id == exclude_id:
                continue
            if guide.orientation == orientation:
                candidates.append(float(guide.position))
        if not candidates:
            return SnapResult(value=value, snapped=False)
        best = None
        for candidate in candidates:
            dist = abs(candidate - value)
            if dist <= threshold_canvas and (best is None or dist < best[0]):
                best = (dist, candidate)
        if best is None:
            return SnapResult(value=value, snapped=False)
        return SnapResult(value=float(best[1]), snapped=True)

    def snap_position(self, x: float, y: float) -> tuple[float, float, bool, bool]:
        if not self.guides_visible:
            return x, y, False, False
        """Snap *x*/*y* to the nearest guide/canvas-edge.

        Returns ``(snapped_x, snapped_y, x_snapped, y_snapped)``.
        """
        rx = self._snap_value(x, "vertical", exclude_id=None)
        ry = self._snap_value(y, "horizontal", exclude_id=None)
        return rx.value, ry.value, rx.snapped, ry.snapped

    def snap_rotated_position(self, x, y, w=0, h=0):
        return snap_rotated(self, x, y, w, h)

    def _screen_to_canvas_distance(self, pixels: float) -> float:
        viewport = getattr(self._canvas, "viewport", None)
        if viewport is None:
            return pixels
        return float(viewport.screen_to_canvas_distance(pixels))

    def _active_canvas_spec(self) -> tuple[int, int, tuple[float, float]]:
        if self._host is not None:
            pages = getattr(self._host, "pages", None)
            if pages is not None:
                getter = getattr(pages, "get_active_canvas_spec", None)
                if callable(getter):
                    spec = getter()
                    return (int(spec.width), int(spec.height), tuple(spec.dpi))
        state = getattr(self._canvas, "state", None)
        width = int(getattr(state, "width", 1))
        height = int(getattr(state, "height", 1))
        return (max(1, width), max(1, height), (72.0, 72.0))

    def _is_inside_canvas(self, canvas_pos: QPointF) -> bool:
        width, height, _dpi = self._active_canvas_spec()
        return 0.0 <= canvas_pos.x() <= width and 0.0 <= canvas_pos.y() <= height

    def _is_release_on_ruler(self, canvas_pos: QPointF) -> bool:
        orientation = self._drag_orientation
        if orientation is None:
            return False
        threshold_canvas = float(self._ruler_thickness_px)
        viewport = getattr(self._canvas, "viewport", None)
        if viewport is not None:
            to_canvas = getattr(viewport, "screen_to_canvas_distance", None)
            if callable(to_canvas):
                threshold_canvas = float(to_canvas(self._ruler_thickness_px))
        if orientation == "horizontal":
            return -threshold_canvas <= canvas_pos.y() < 0.0
        return -threshold_canvas <= canvas_pos.x() < 0.0

    def _guide_by_id(self, guide_id: str | None) -> Guide | None:
        if guide_id is None:
            return None
        for guide in self._guides:
            if guide.id == guide_id:
                return guide
        return None

    def _new_guide_id(self) -> str:
        return f"g_{uuid4().hex[:8]}"

    def _delete_selected_guide(self) -> None:
        if self.guides_locked:
            return
        if self._selected_id is None:
            return
        self._guides = [guide for guide in self._guides if guide.id != self._selected_id]
        self._selected_id = None
        self._persist_view_state()

    def _reset_drag_state(self) -> None:
        feedback = getattr(self._canvas, "_guide_snap_feedback", None)
        if feedback is not None:
            feedback.clear()
        self._dragging = False
        self._drag_pointer_pos = None
        self._drag_guide_id = None
        self._drag_orientation = None
        self._drag_original_pos = None
        self._drag_preview_pos = None
        self._drag_last_inside_pos = None
        self._drag_new_guide = False
        self._snap_active = False
        self._snap_position = None

    def _show_snap_feedback(self, snap: SnapResult) -> None:
        feedback = getattr(self._canvas, "_guide_snap_feedback", None)
        if feedback is not None:
            feedback.set_targets(((self._drag_orientation, snap.value),) if snap.snapped else ())

    def _request_update(self) -> None:
        update = getattr(self._canvas, "update", None)
        if callable(update):
            update_guide_overlay(self._canvas)
        if self._ruler_update_cb is not None:
            self._ruler_update_cb()

    # ------------------------------------------------------------------
    # Page state persistence
    def _load_active_page_state(self) -> None:
        if self._host is None:
            return
        pages = getattr(self._host, "pages", None)
        if pages is None:
            return
        get_active = getattr(pages, "get_active_page_id", None)
        getter = getattr(pages, "get_page_view_state", None)
        if not callable(get_active) or not callable(getter):
            return
        page_id = get_active()
        view_state = getter(page_id)
        self._apply_view_state(view_state)

    def _apply_view_state(self, view_state: dict[str, Any] | None) -> None:
        self._guides = []
        self._selected_id = None
        if isinstance(view_state, dict):
            origin = view_state.get("ruler_origin")
            if isinstance(origin, (list, tuple)) and len(origin) >= 2:
                try:
                    self._ruler_state.origin = QPointF(float(origin[0]), float(origin[1]))
                except (TypeError, ValueError):
                    self._ruler_state.origin = QPointF(0.0, 0.0)
            unit = view_state.get("ruler_unit")
            if unit in ("px", "mm", "inch"):
                self._ruler_state.unit = unit
            guides_raw = view_state.get("guides")
            if isinstance(guides_raw, list):
                for entry in guides_raw:
                    if not isinstance(entry, dict):
                        continue
                    orientation = entry.get("orientation")
                    position = entry.get("position")
                    gid = entry.get("id")
                    if orientation not in ("horizontal", "vertical"):
                        continue
                    try:
                        pos_val = float(cast(float, position))
                        if not math.isfinite(pos_val):
                            continue
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(gid, str) or not gid:
                        gid = self._new_guide_id()
                    rotation, pivot = stored_transform(entry)
                    self._guides.append(
                        Guide(
                            id=gid,
                            orientation=orientation,
                            position=pos_val,
                            rotation=rotation % 360,
                            pivot=pivot,
                        )
                    )
        self._request_update()

    def _persist_view_state(self) -> None:
        if self._host is None:
            return
        pages = getattr(self._host, "pages", None)
        if pages is None:
            return
        getter = getattr(pages, "get_active_page_id", None)
        if not callable(getter):
            return
        # Merge rather than replace: the page's view state is shared with the
        # per-page viewport (CT-06 B4-c6), and a plain set would drop
        # whatever the other writer stored.  Editing a guide *is* an edit, so
        # this write keeps marking the page dirty.
        state = {
            "ruler_origin": [self._ruler_state.origin.x(), self._ruler_state.origin.y()],
            "ruler_unit": self._ruler_state.unit,
            "guides": [
                {
                    "id": guide.id,
                    "orientation": guide.orientation,
                    "position": float(guide.position),
                    **(
                        {
                            "rotation": guide.rotation,
                            "pivot": list(guide.pivot) if guide.pivot else None,
                        }
                        if guide.rotation % 360
                        else {}
                    ),
                }
                for guide in self._guides
            ],
        }
        page_id = getter()
        merge = getattr(pages, "update_page_view_state", None)
        if callable(merge):
            merge(page_id, state, mark_dirty=True)
            return
        setter = getattr(pages, "set_page_view_state", None)
        if callable(setter):
            setter(page_id, state)

    def _subscribe_page_events(self) -> None:
        if self._host is None:
            return
        bus = getattr(self._host, "events", None)
        if bus is None:
            return
        subscribe = getattr(bus, "on", None)
        if not callable(subscribe):
            subscribe = getattr(bus, "subscribe", None)
        if callable(subscribe):
            subscribe(Events.ACTIVE_PAGE_CHANGED, self._on_active_page_changed)

    def _on_active_page_changed(self, payload: object) -> None:
        page_id = None
        if isinstance(payload, dict):
            page_id = payload.get("page_id")
        if not isinstance(page_id, str):
            self._load_active_page_state()
            return
        pages = getattr(self._host, "pages", None)
        if pages is None:
            return
        getter = getattr(pages, "get_page_view_state", None)
        if not callable(getter):
            return
        view_state = getter(page_id)
        self._apply_view_state(view_state)

    # ------------------------------------------------------------------
    # Settings updates
    def _apply_guide_interaction_setting(self, key: str, value: object) -> None:
        if key in ("view/canvas_edge_snap", "view/canvas_center_snap"):
            setattr(
                self,
                "_canvas_edge_snap" if key == "view/canvas_edge_snap" else "_canvas_center_snap",
                bool(value),
            )
            return
        if self._dragging:
            guide = self._guide_by_id(self._drag_guide_id)
            if guide is not None and self._drag_original_pos is not None:
                guide.position = self._drag_original_pos
            self._reset_drag_state()
        if key == "view/guides_visible":
            self.guides_visible = bool(value)
        else:
            self.guides_locked = bool(value)
        self._selected_id = None
        self._request_update()

    def _on_setting_changed(self, key: str, value: object) -> None:
        if key in (
            "view/guides_visible",
            "view/guides_locked",
            "view/canvas_edge_snap",
            "view/canvas_center_snap",
        ):
            self._apply_guide_interaction_setting(key, value)
            return
        if key == "view/guides_color":
            self._guides_color = QColor(str(value))
            self._request_update()
        elif key == "view/guides_opacity":
            try:
                self._guides_opacity = float(cast(float, value))
            except (TypeError, ValueError):
                self._guides_opacity = 0.8
            self._request_update()
        elif key == "view/guides_hit_slop_px":
            try:
                self._hit_slop_px = int(cast(int, value))
            except (TypeError, ValueError):
                self._hit_slop_px = 6
        elif key == "view/guides_snap_threshold_px":
            try:
                self._snap_threshold_px = int(cast(int, value))
            except (TypeError, ValueError):
                self._snap_threshold_px = 8
        elif key == "view/ruler_thickness_px":
            try:
                self._ruler_thickness_px = int(cast(int, value))
            except (TypeError, ValueError):
                self._ruler_thickness_px = 18
        elif key == "view/ruler_unit":
            if value in ("px", "mm", "inch"):
                self._ruler_state.unit = value
                self._persist_view_state()
                self._request_update()
