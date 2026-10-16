"""ContextToolbarManager — core-side lifecycle manager for the context toolbar.

Handles show/hide triggers, position calculation, commit/cancel,
stroke BBox accumulation, and key interception.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QTimer, Signal
from PySide6.QtWidgets import QWidget

from loomstroke.core.events import Events
from loomstroke.plugins.sdk.tool import ContextToolbarSpec, PropertySpec
from loomstroke.plugins.sdk.transform import HANDLE_EDGE_SIZE, HANDLE_ROTATION_OFFSET

if TYPE_CHECKING:
    from loomstroke.core.canvas_service_simple import SimpleCanvasService
    from loomstroke.core.event_bus import EventBus
    from loomstroke.core.tool_manager import ToolManager
    from loomstroke.host_api import StrokePoint
    from loomstroke.ui.canvas.canvas_widget import CanvasWidget
    from loomstroke.ui.canvas.ruler_frame import RulerCanvasFrame

logger = logging.getLogger(__name__)

# Layout constants
_TOOLBAR_OFFSET_Y = 28
_VIEWPORT_MARGIN = 4

# Rotation handle clearance (scene units). The rotation handle is drawn above
# the bbox at (cx, top - HANDLE_ROTATION_OFFSET) with a roughly
# HANDLE_EDGE_SIZE/2 pixel radius. When we expand the anchor rect upward by
# this amount in scene coords, the scene→view transform scales it by the
# current zoom automatically, so the toolbar placement always keeps
# _TOOLBAR_OFFSET_Y pixel gap above the rotation widget regardless of zoom
# (prevents toolbar overlapping the rotation widget at high zoom).
_ROTATION_HANDLE_PAD_SCENE = int(HANDLE_ROTATION_OFFSET + HANDLE_EDGE_SIZE * 0.5)

# Trigger → EventBus event mapping
_TRIGGER_EVENT_MAP: dict[str, str] = {
    "selection_created": Events.SELECTION_CHANGED,
    "preview_start": "tool.preview_start",
    "text_editing": "tool.text_editing",
}


class ContextToolbarManager(QObject):
    """Core-side lifecycle manager for the floating context toolbar."""

    toolbar_shown = Signal()
    toolbar_hidden = Signal()

    def __init__(
        self,
        ruler_frame: RulerCanvasFrame,
        canvas_widget: CanvasWidget,
        tool_manager: ToolManager,
        canvas_service: SimpleCanvasService,
        event_bus: EventBus,
    ) -> None:
        super().__init__(ruler_frame)
        self._ruler_frame = ruler_frame
        self._canvas_widget = canvas_widget
        self._tool_manager = tool_manager
        self._canvas_service = canvas_service
        self._event_bus = event_bus

        # Current state
        self._current_tool_id: str | None = None
        self._current_spec: ContextToolbarSpec | None = None
        self._current_property_specs: list[PropertySpec] = []
        self._toolbar_visible = False
        self._consumed_key: str | None = None  # Track press for symmetric release

        # Stroke BBox accumulation
        self._stroke_bbox: QRect | None = None

        # Toolbar widget (lazy)
        self._toolbar_widget: _ToolbarRef | None = None

        # External providers
        self._external_specs: dict[str, tuple[ContextToolbarSpec, list[PropertySpec]]] = {}
        self._active_provider_id: str | None = None  # tool_id or external provider_id
        self._active_spec: ContextToolbarSpec | None = None

        # Connect stroke signals
        canvas_widget.stroke_started.connect(self._on_stroke_started)
        canvas_widget.stroke_updated.connect(self._on_stroke_updated)
        canvas_widget.stroke_ended.connect(self._on_stroke_ended)

        # Connect viewport changes for position updates
        canvas_widget.viewport_changed.connect(lambda _: self.update_position())

        # Keep strong references to bound methods passed to EventBus.
        # EventBus stores weakrefs; without strong refs the GC collects
        # bound methods immediately and the handlers never fire.
        from collections.abc import Callable as _Callable

        self._eb_handlers: list[_Callable[[Any], Any]] = [
            self._on_tool_changed,
            self._on_selection_changed,
            self._on_preview_start_event,
            self._on_layer_selected,
            self._on_transform_start,
        ]

        # Connect default commit triggers
        event_bus.on(Events.LAYER_SELECTED, self._eb_handlers[3])
        event_bus.on(Events.TOOL_CHANGED, self._eb_handlers[0])

        # Connect selection changed
        event_bus.on(Events.SELECTION_CHANGED, self._eb_handlers[1])

        # Connect EventBus triggers for preview_start / text_editing
        event_bus.on("tool.preview_start", self._eb_handlers[2])
        event_bus.on("tool.text_editing", self._eb_handlers[2])

        # Hide toolbar during transform operations
        event_bus.on("tool.transform_start", self._eb_handlers[4])

        # Connect tool_setting_changed for syncing toolbar values
        tool_manager.tool_setting_changed.connect(self._on_tool_setting_changed)
        tool_action_completed = getattr(tool_manager, "tool_action_completed", None)
        if tool_action_completed is not None and hasattr(tool_action_completed, "connect"):
            tool_action_completed.connect(self._on_tool_action_completed)

        # Install event filter on ruler_frame for outside-click detection
        ruler_frame.installEventFilter(self)

    # ------------------------------------------------------------------
    # Toolbar widget access
    # ------------------------------------------------------------------

    def _ensure_toolbar(self) -> Any:
        """Lazily create the toolbar widget."""
        if self._toolbar_widget is None:
            from loomstroke.ui.context_toolbar.toolbar_widget import ContextToolbar

            tb = ContextToolbar(parent=self._ruler_frame)
            self._toolbar_widget = _ToolbarRef(tb)
        return self._toolbar_widget.widget

    def _get_plugin(self, tool_id: str) -> Any:
        """Get a tool plugin by ID via ToolManager internals."""
        tools = getattr(self._tool_manager, "_tools", {})
        state = tools.get(tool_id)
        return getattr(state, "plugin", None) if state else None

    # ------------------------------------------------------------------
    # Tool lifecycle
    # ------------------------------------------------------------------

    def on_tool_activated(self, tool_id: str) -> None:
        """Called when a new tool becomes active."""
        if self._toolbar_visible and self._active_provider_id != tool_id:
            self._do_commit_or_hide()

        self._current_tool_id = tool_id
        plugin = self._get_plugin(tool_id)
        if plugin is None:
            self._current_spec = None
            self._current_property_specs = []
            return

        spec_fn = getattr(plugin, "context_toolbar_spec", None)
        spec = spec_fn() if spec_fn else None
        if spec is None or not spec.enabled:
            self._current_spec = None
            self._current_property_specs = []
            return

        self._current_spec = spec
        # Resolve property_specs from ToolMetadata
        metadata = self._tool_manager.get_tool_metadata(tool_id)
        if metadata:
            if spec.property_keys is not None:
                key_set = set(spec.property_keys)
                key_order = {k: i for i, k in enumerate(spec.property_keys)}
                self._current_property_specs = sorted(
                    [p for p in metadata.properties if p.key in key_set],
                    key=lambda p: key_order.get(p.key, 999),
                )
            else:
                # Auto: is_common=True properties
                self._current_property_specs = [p for p in metadata.properties if p.is_common]
        else:
            self._current_property_specs = []

        # Declarative toolbars must be available as soon as the user
        # selects a tool from the toolbar. Their duplicated fixed-bar
        # controls are filtered out elsewhere, so waiting for pointer_up
        # / selection_created would leave parameters temporarily hidden.
        if self._should_show_on_tool_activation(spec):
            self._show_toolbar(tool_id, spec, self._current_property_specs)
            return

        # If the new tool already has a selected object or selection,
        # show its toolbar immediately.
        if (spec.show_trigger == "pointer_up" and self._selected_object_bbox() is not None) or (
            spec.show_trigger == "selection_created" and self._has_selection()
        ):
            self._show_toolbar(tool_id, spec, self._current_property_specs)

    @staticmethod
    def _should_show_on_tool_activation(spec: ContextToolbarSpec) -> bool:
        if not spec.enabled:
            return False
        if spec.show_trigger == "tool_active":
            return True
        return spec.build_widget_fn is None

    # ------------------------------------------------------------------
    # Show triggers
    # ------------------------------------------------------------------

    @staticmethod
    def _pt_xy(pt: StrokePoint) -> tuple[int, int]:
        """Extract integer (x, y) from a StrokePoint (host_api variant)."""
        pos = getattr(pt, "pos", None)
        if pos is not None:
            return int(pos[0]), int(pos[1])
        # Fallback for core.commands.StrokePoint which has .x / .y
        return int(getattr(pt, "x", 0)), int(getattr(pt, "y", 0))

    def _on_stroke_started(self, pt: StrokePoint) -> None:
        """Reset BBox; hide toolbar during stroke (pointer_up triggers only)."""
        px, py = self._pt_xy(pt)
        self._stroke_bbox = QRect(px, py, 1, 1)
        if self._toolbar_visible:
            # Auto-hide for pointer_up and selection_created triggered toolbars.
            # text_editing / preview_start / tool_active toolbars are managed by
            # their own lifecycle and should not be dismissed by strokes.
            spec = self._displayed_spec()
            if spec and spec.show_trigger in ("pointer_up", "selection_created"):
                self._do_commit_or_hide()

    def _on_stroke_updated(self, pts: list[StrokePoint]) -> None:
        """Expand BBox with new stroke points."""
        if self._stroke_bbox is None:
            return
        for pt in pts:
            px, py = self._pt_xy(pt)
            bbox = self._stroke_bbox
            x1 = min(bbox.left(), px)
            y1 = min(bbox.top(), py)
            x2 = max(bbox.right(), px)
            y2 = max(bbox.bottom(), py)
            self._stroke_bbox = QRect(x1, y1, x2 - x1 + 1, y2 - y1 + 1)

    def _on_stroke_ended(self, pt: StrokePoint) -> None:
        """Finalize BBox; show toolbar if trigger is pointer_up."""
        if self._stroke_bbox is not None:
            px, py = self._pt_xy(pt)
            bbox = self._stroke_bbox
            x1 = min(bbox.left(), px)
            y1 = min(bbox.top(), py)
            x2 = max(bbox.right(), px)
            y2 = max(bbox.bottom(), py)
            self._stroke_bbox = QRect(x1, y1, x2 - x1 + 1, y2 - y1 + 1)

        spec = self._current_spec
        if spec and spec.show_trigger == "pointer_up" and self._current_tool_id:
            self._show_toolbar(self._current_tool_id, spec, self._current_property_specs)

    def _on_selection_changed(self, payload: dict[str, Any]) -> None:
        """Handle selection.changed event."""
        active_spec = self._displayed_spec()
        if not payload.get("has_selection", False):
            if active_spec and active_spec.show_trigger == "tool_active":
                return
            if self._has_selection():
                return
            if self._toolbar_visible:
                self.hide()
            return
        spec = self._current_spec
        if spec and spec.show_trigger == "selection_created" and self._current_tool_id:
            self._show_toolbar(self._current_tool_id, spec, self._current_property_specs)

    def _on_preview_start_event(self, payload: dict[str, Any]) -> None:
        """Handle tool.preview_start / tool.text_editing events."""
        tool_id = payload.get("tool_id")
        if not tool_id or tool_id != self._current_tool_id:
            return
        spec = self._current_spec
        if not spec:
            return
        # Check if this event matches the show_trigger
        event_name = _TRIGGER_EVENT_MAP.get(spec.show_trigger)
        if event_name in ("tool.preview_start", "tool.text_editing"):
            self._show_toolbar(tool_id, spec, self._current_property_specs)

    # ------------------------------------------------------------------
    # Show / hide
    # ------------------------------------------------------------------

    def _show_toolbar(
        self,
        provider_id: str,
        spec: ContextToolbarSpec,
        property_specs: list[PropertySpec],
    ) -> None:
        """Build and show the toolbar."""
        has_content = property_specs or spec.custom_widgets or spec.build_widget_fn is not None
        if not has_content:
            return

        # If already showing the same provider, just update position
        if self._toolbar_visible and self._active_provider_id == provider_id:
            self._active_spec = spec
            self._apply_context_action_states(provider_id)
            self.update_position()
            self._request_canvas_underlay_refresh()
            return

        toolbar = self._ensure_toolbar()
        toolbar.build_from_spec(
            spec,
            property_specs,
            on_changed=self._on_toolbar_param_changed,
            on_action=self._on_toolbar_action_clicked,
        )

        # Sync current values from ToolManager
        if provider_id == self._current_tool_id:
            if spec.build_widget_fn is not None:
                # Custom widget — sync all known settings at once
                self._sync_custom_widget_values(provider_id, toolbar)
            else:
                for ps in property_specs:
                    value = self._tool_manager.get_tool_setting(provider_id, ps.key)
                    if value is not None:
                        toolbar.set_value(ps.key, value)

        self._apply_context_action_states(provider_id)

        self._active_provider_id = provider_id
        self._active_spec = spec
        self._toolbar_visible = True
        toolbar.setVisible(True)
        toolbar.raise_()
        self.update_position()
        self.toolbar_shown.emit()
        self._request_canvas_underlay_refresh()

    def hide(self) -> None:
        """Hide the toolbar without commit/cancel."""
        if not self._toolbar_visible:
            return
        self._toolbar_visible = False
        if self._toolbar_widget:
            self._toolbar_widget.widget.setVisible(False)
        self._active_provider_id = None
        self._active_spec = None
        self.toolbar_hidden.emit()
        self._request_canvas_underlay_refresh()

    def _request_canvas_underlay_refresh(self) -> None:
        self._request_canvas_underlay_refresh_now()
        QTimer.singleShot(0, self._request_canvas_underlay_refresh_now)
        QTimer.singleShot(16, self._request_canvas_underlay_refresh_now)

    def _request_canvas_underlay_refresh_now(self) -> None:
        refresh = getattr(self._canvas_widget, "request_native_surface_refresh", None)
        try:
            if callable(refresh):
                refresh()
                return
            update = getattr(self._canvas_widget, "update", None)
            if callable(update):
                update()
        except RuntimeError:
            return

    def commit_current(self) -> None:
        """Commit and hide."""
        spec = self._displayed_spec()
        if spec and spec.mode == "preview" and spec.on_commit:
            try:
                spec.on_commit()
            except Exception:
                logger.exception("on_commit failed")
        self.hide()

    def cancel_current(self) -> None:
        """Cancel and hide."""
        spec = self._displayed_spec()
        if spec and spec.mode == "preview" and spec.on_cancel:
            try:
                spec.on_cancel()
            except Exception:
                logger.exception("on_cancel failed")
        self.hide()

    def _do_commit_or_hide(self) -> None:
        """Commit if preview mode, otherwise just hide."""
        spec = self._displayed_spec()
        if spec and spec.mode == "preview":
            self.commit_current()
        else:
            self.hide()

    # ------------------------------------------------------------------
    # Position calculation
    # ------------------------------------------------------------------

    def update_position(self) -> None:
        """Recalculate toolbar position based on placement mode."""
        if not self._toolbar_visible or self._toolbar_widget is None:
            return

        toolbar = self._toolbar_widget.widget
        toolbar_size = toolbar.sizeHint()
        frame_rect = self._ruler_frame.rect()
        spec = self._displayed_spec()
        placement = spec.placement if spec else "anchor"

        if placement in ("top_center", "bottom_center"):
            canvas_view = self._canvas_image_rect_in_frame()
            cx = self._pick_anchor_cx(canvas_view, toolbar_size, frame_rect)
            if placement == "top_center":
                ty = canvas_view.top() - toolbar_size.height() - _TOOLBAR_OFFSET_Y
            else:
                ty = canvas_view.bottom() + _TOOLBAR_OFFSET_Y
        else:
            # "anchor" — follow the object/selection. _resolve_anchor_rect
            # handles rotation-handle padding internally (only when the
            # rotation widget is currently drawn for this anchor).
            anchor = self._resolve_anchor_rect()
            view_rect = self._scene_to_ruler_frame(anchor) if anchor is not None else None

            if view_rect is not None:
                cx = self._pick_anchor_cx(view_rect, toolbar_size, frame_rect)
                ty = self._pick_anchor_ty(view_rect, toolbar_size, frame_rect)
            else:
                cx = (frame_rect.width() - toolbar_size.width()) // 2
                ty = _VIEWPORT_MARGIN

        # Clamp to frame bounds
        cx = max(
            _VIEWPORT_MARGIN, min(cx, frame_rect.width() - toolbar_size.width() - _VIEWPORT_MARGIN)
        )
        ty = max(
            _VIEWPORT_MARGIN,
            min(ty, frame_rect.height() - toolbar_size.height() - _VIEWPORT_MARGIN),
        )

        target_pos = self._toolbar_local_to_window_pos(toolbar, QPoint(cx, ty))
        if toolbar.pos() != target_pos:
            toolbar.move(target_pos)
            self._request_canvas_underlay_refresh()

    def _toolbar_local_to_window_pos(self, toolbar: QWidget, local_pos: QPoint) -> QPoint:
        if toolbar.isWindow():
            return self._ruler_frame.mapToGlobal(local_pos)
        return local_pos

    def _pick_anchor_cx(
        self,
        view_rect: QRect,
        toolbar_size: QSize,
        frame_rect: QRect,
    ) -> int:
        """Horizontal position based on the VISIBLE portion of the anchor.

        At high zoom the anchor may extend beyond the viewport horizontally
        (or its scene-transformed center may be off-screen depending on pan).
        Using ``view_rect.center().x()`` directly then causes the outer clamp
        to pin the toolbar to the left/right edge of the frame.  Clipping to
        the visible portion keeps the toolbar centered on whatever part of
        the anchor is actually in view, matching the user's expectation that
        the toolbar tracks the visible object rather than the off-screen
        geometry.
        """
        visible = view_rect.intersected(frame_rect)
        if visible.isEmpty():
            # Anchor entirely off-screen — center on viewport.
            return (frame_rect.width() - toolbar_size.width()) // 2
        return visible.center().x() - toolbar_size.width() // 2

    def _pick_anchor_ty(
        self,
        view_rect: QRect,
        toolbar_size: QSize,
        frame_rect: QRect,
    ) -> int:
        """Choose ty for the anchor placement: above, else flip below.

        At high zoom the anchor top is often above the viewport edge, which
        clamps the "above" position to y=_VIEWPORT_MARGIN and causes the
        toolbar to overlap the rotation widget / bbox interior.  When that
        happens, try placing the toolbar *below* the visible anchor bottom
        instead.  If neither fits, fall through to the outer clamp so the
        toolbar at least stays on screen.
        """
        toolbar_h = toolbar_size.height()
        ty_above = view_rect.top() - toolbar_h - _TOOLBAR_OFFSET_Y
        if ty_above >= _VIEWPORT_MARGIN:
            return ty_above

        # Not enough room above — try flipping below the visible bottom.
        visible_bottom = min(view_rect.bottom(), frame_rect.height() - _VIEWPORT_MARGIN)
        ty_below = visible_bottom + _TOOLBAR_OFFSET_Y
        if ty_below + toolbar_h <= frame_rect.height() - _VIEWPORT_MARGIN:
            return ty_below

        # Neither above nor below has room (anchor fills viewport).  Fall back
        # to the "above" attempt so the outer clamp pins it to the top edge.
        return ty_above

    def _resolve_anchor_rect(self) -> QRect | None:
        """Get the anchor rectangle in scene coordinates.

        If a rotation widget is currently drawn above the anchor (vector
        selection in transform mode), the returned rect is expanded upward
        by ``_ROTATION_HANDLE_PAD_SCENE`` scene units so the toolbar position
        clears the widget at any zoom level. Other selection modes (pixel
        marching ants, no-transform vector edits) leave the rect unchanged.
        """
        spec = self._displayed_spec()
        base = self._compute_base_anchor_rect(spec)
        if base is None:
            return None
        # viewport_top is a synthetic anchor — never has a rotation widget.
        if spec is not None and spec.anchor_mode == "viewport_top":
            return base
        if not self._has_rotation_widget_drawn():
            return base
        return QRect(
            base.left(),
            base.top() - _ROTATION_HANDLE_PAD_SCENE,
            base.width(),
            base.height() + _ROTATION_HANDLE_PAD_SCENE,
        )

    def _compute_base_anchor_rect(self, spec: ContextToolbarSpec | None) -> QRect | None:
        """Resolve the base (un-padded) anchor rect for the given spec."""
        if spec is None:
            return self._selected_object_bbox() or self._active_layer_bbox()

        mode = spec.anchor_mode
        if mode == "stroke_bbox":
            return self._selected_object_bbox() or self._active_layer_bbox()
        if mode == "selection_bbox":
            bounds = self._canvas_service.get_selection_bounds()
            anchor = (
                bounds
                if bounds is not None and bounds.width() > 0 and bounds.height() > 0
                else self._selected_object_bbox() or self._active_layer_bbox()
            )
            return anchor
        if mode == "viewport_top":
            w = self._ruler_frame.width()
            return QRect(w // 4, 0, w // 2, 1)
        if mode == "custom":
            plugin = self._get_plugin(self._current_tool_id or "")
            if plugin:
                fn = getattr(plugin, "get_context_anchor_rect", None)
                if fn:
                    rect = fn()
                    if rect is not None:
                        return cast("QRect", rect)
        return self._selected_object_bbox() or self._active_layer_bbox()

    def _has_rotation_widget_drawn(self) -> bool:
        """Return True if a transform overlay (incl. rotation handle) is
        currently drawn above the active selection.

        ``canvas_service._draw_vector_transform_overlay`` draws the rotation
        handle at ``(cx, top - HANDLE_ROTATION_OFFSET)`` whenever
        ``_vector_selection`` carries a ``transform_bounds`` entry. Pixel
        selections (marching ants) and no-transform editing do not set
        ``transform_bounds``, so this check skips the unnecessary padding.

        O(1): two dict lookups + a length check. Safe to call per frame.
        """
        selection = getattr(self._canvas_service, "_vector_selection", None)
        if isinstance(selection, dict):
            bounds = selection.get("transform_bounds")
            if isinstance(bounds, (tuple, list)) and len(bounds) >= 4:
                return True
        return False

    def _canvas_image_rect_in_frame(self) -> QRect:
        """Return the canvas image rect in RulerCanvasFrame coordinates."""
        size_fn = getattr(self._canvas_service, "get_canvas_size", None)
        if callable(size_fn):
            try:
                cw, ch = size_fn()
                scene_rect = QRect(0, 0, int(cw), int(ch))
                view_rect = self._scene_to_ruler_frame(scene_rect)
                if view_rect is not None:
                    return view_rect
            except Exception:
                logger.debug("canvas size to ruler frame failed", exc_info=True)
        # Fallback: full frame
        return self._ruler_frame.rect()

    def _has_selection(self) -> bool:
        """Check if a pixel or vector selection currently exists."""
        bounds = self._canvas_service.get_selection_bounds()
        if bounds is not None and bounds.width() > 0 and bounds.height() > 0:
            return True
        return self._selected_object_bbox() is not None

    def _selected_object_bbox(self) -> QRect | None:
        """Return the bbox of the currently selected vector/text object.

        Resolution order:
        1. ``_vector_selection["transform_bounds"]`` — set by tools like
           basic_move / basic_selection when entering transform mode.
        2. Lookup by ``_vector_selection["id"]`` — tools such as
           path_tool / precise_tool set only the id (no transform_bounds)
           when creating a new path.  We locate the vec object in its layer
           and compute its bbox via ``canvas_service._compute_vector_bbox``.
        """
        selection = getattr(self._canvas_service, "_vector_selection", None)
        if not isinstance(selection, dict):
            return None

        # 1. transform_bounds (basic_move / basic_selection / etc.)
        # Format: (min_x, min_y, max_x, max_y) — same as compute_bbox() / the
        # canvas_service._draw_vector_transform_overlay interpretation.
        bounds = selection.get("transform_bounds")
        if bounds is not None and len(bounds) >= 4:
            min_x, min_y, max_x, max_y = bounds[:4]
            w = int(max_x - min_x)
            h = int(max_y - min_y)
            if w > 0 and h > 0:
                return QRect(int(min_x), int(min_y), w, h)

        # 2. Lookup by id (path_tool / precise_tool / etc. that set only id)
        vec_id = selection.get("id")
        if isinstance(vec_id, str) and vec_id:
            layer_id = selection.get("layer_id")
            rect = self._compute_vec_bbox_by_id(vec_id, layer_id)
            if rect is not None:
                return rect

        return None

    def _compute_vec_bbox_by_id(self, vec_id: str, layer_id: str | None) -> QRect | None:
        """Locate vec by id across layers and compute its scene-coord bbox.

        Used when ``_vector_selection`` has only ``id`` (no transform_bounds).
        Falls back to None if the vec isn't found or its bbox is degenerate.
        """
        canvas_service = self._canvas_service
        layers = getattr(canvas_service, "_layers", None) or []
        compute = getattr(canvas_service, "_compute_vector_bbox", None)
        if not callable(compute):
            return None

        # Search the declared layer first, then the rest.
        candidates: list[Any] = []
        if layer_id:
            for layer in layers:
                if getattr(layer, "id", None) == layer_id:
                    candidates.append(layer)
                    break
        for layer in layers:
            if layer not in candidates:
                candidates.append(layer)

        for layer in candidates:
            vector_objects = getattr(layer, "vector_objects", None) or []
            for vec in vector_objects:
                if getattr(vec, "id", None) != vec_id:
                    continue
                try:
                    bounds = compute(vec)
                except Exception:
                    return None
                if bounds is None:
                    return None
                x0, y0, x1, y1 = bounds
                w = int(x1 - x0)
                h = int(y1 - y0)
                if w <= 0 or h <= 0:
                    return None
                return QRect(int(x0), int(y0), w, h)
        return None

    def _active_layer_bbox(self) -> QRect | None:
        """Return the active layer's bounding box in scene coordinates.

        Falls back to canvas size, then to the accumulated stroke bbox.
        """
        # Try layer content bounds (lightweight for vector layers)
        bounds_fn = getattr(self._canvas_service, "get_layer_content_bounds", None)
        if callable(bounds_fn):
            try:
                bounds = bounds_fn(None)  # None = active layer
                if bounds is not None:
                    x, y, w, h = bounds
                    return QRect(int(x), int(y), int(w), int(h))
            except Exception:
                logger.debug("layer content bounds lookup failed", exc_info=True)
        # Fallback: full canvas size
        size_fn = getattr(self._canvas_service, "get_canvas_size", None)
        if callable(size_fn):
            try:
                cw, ch = size_fn()
                return QRect(0, 0, int(cw), int(ch))
            except Exception:
                logger.debug("canvas size fallback lookup failed", exc_info=True)
        # Last resort: stroke bbox
        return self._stroke_bbox

    def _scene_to_ruler_frame(self, scene_rect: QRect) -> QRect | None:
        """Transform scene coordinates to RulerCanvasFrame widget coordinates."""
        vp = self._canvas_widget.viewport
        if vp is None:
            return scene_rect

        # Apply view transform (zoom + pan)
        transform = vp.get_transform()
        top_left = transform.map(QPoint(scene_rect.left(), scene_rect.top()))
        bottom_right = transform.map(QPoint(scene_rect.right(), scene_rect.bottom()))

        # Offset for canvas position within RulerCanvasFrame
        canvas_pos = self._canvas_widget.mapTo(self._ruler_frame, QPoint(0, 0))
        return QRect(
            top_left.x() + canvas_pos.x(),
            top_left.y() + canvas_pos.y(),
            bottom_right.x() - top_left.x(),
            bottom_right.y() - top_left.y(),
        )

    def _sync_custom_widget_values(self, provider_id: str, toolbar: Any) -> None:
        """Push all current tool settings into a custom-built toolbar widget."""
        plugin = self._get_plugin(provider_id)
        if plugin is None:
            return
        get_settings = getattr(plugin, "get_settings", None)
        if not callable(get_settings):
            return
        try:
            settings = get_settings()
        except Exception:
            logger.debug("get_settings() failed for %s", provider_id, exc_info=True)
            return
        if isinstance(settings, dict):
            for key, value in settings.items():
                toolbar.set_value(key, value)

    # ------------------------------------------------------------------
    # Parameter sync
    # ------------------------------------------------------------------

    def _on_toolbar_param_changed(self, key: str, value: Any) -> None:
        """User changed a value in the toolbar."""
        tool_id = self._active_provider_id
        if not tool_id:
            return
        # Update via ToolManager (triggers tool_setting_changed signal)
        self._tool_manager.set_tool_setting(tool_id, key, value)
        # Preview callback
        spec = self._displayed_spec()
        if spec and spec.on_param_preview:
            try:
                spec.on_param_preview(key, value)
            except Exception:
                logger.exception("on_param_preview failed")

    def _on_toolbar_action_clicked(self, action_key: str) -> None:
        """Dispatch a context-toolbar button through the active tool action path."""
        tool_id = self._active_provider_id
        if not tool_id or tool_id != self._current_tool_id:
            return
        try:
            scheduler = getattr(self._tool_manager, "schedule_tool_action_async", None)
            if callable(scheduler):
                scheduler(tool_id, action_key, None)
            else:
                self._tool_manager.dispatch_tool_action(tool_id, action_key, None)
        except Exception:
            logger.exception("context toolbar action failed: %s", action_key)
        self.update_position()

    def _on_tool_setting_changed(self, tool_id: str, key: str, value: object) -> None:
        """Sync toolbar when settings change externally (e.g. from property bar)."""
        if not self._toolbar_visible or tool_id != self._active_provider_id:
            return
        if self._toolbar_widget:
            self._toolbar_widget.widget.set_value(key, value)
            self._apply_context_action_states(tool_id)

    def _on_tool_action_completed(self, tool_id: str, action_key: str, handled: bool) -> None:
        """Refresh action state after a visible toolbar action finishes."""
        del action_key, handled
        if not self._toolbar_visible:
            return
        if tool_id != self._active_provider_id or tool_id != self._current_tool_id:
            return
        self._apply_context_action_states(tool_id)
        self.update_position()

    def _apply_context_action_states(self, provider_id: str) -> None:
        """Apply plugin-provided action enabled state to the current toolbar."""
        if self._toolbar_widget is None:
            return
        plugin = self._get_plugin(provider_id)
        if plugin is None:
            return
        snapshot_fn = getattr(plugin, "get_context_actions_snapshot", None)
        if not callable(snapshot_fn):
            return
        try:
            snapshot = snapshot_fn()
        except Exception:
            logger.debug("context action snapshot failed for %s", provider_id, exc_info=True)
            return
        if not isinstance(snapshot, Mapping):
            return
        actions = snapshot.get("actions")
        if not isinstance(actions, list):
            return
        apply_states = getattr(self._toolbar_widget.widget, "apply_action_states", None)
        if callable(apply_states):
            apply_states(actions)

    # ------------------------------------------------------------------
    # Key handling (§7.1)
    # ------------------------------------------------------------------

    def handle_key(self, key: str, pressed: bool) -> bool:  # noqa: PLR0911
        """Intercept Enter/Esc when toolbar is visible.

        Called from CanvasWidget.keyPressEvent/keyReleaseEvent before
        _dispatch_tool_key. Returns True if the key was consumed.
        """
        if not self._toolbar_visible:
            return False

        key_lower = key.lower()

        if not pressed:
            # Consume release if we consumed the press
            if self._consumed_key == key_lower:
                self._consumed_key = None
                return True
            return False

        spec = self._displayed_spec()
        if spec and spec.mode == "preview":
            if key_lower in ("return", "enter"):
                self._consumed_key = key_lower
                self.commit_current()
                return True
            if key_lower in ("esc", "escape"):
                self._consumed_key = key_lower
                self.cancel_current()
                return True
        elif spec and spec.mode == "settings":
            # tool_active toolbars stay visible — don't dismiss on Enter/Esc
            if spec.show_trigger == "tool_active":
                return False
            if key_lower in ("return", "enter", "esc", "escape"):
                self._consumed_key = key_lower
                self.hide()
                return True

        return False

    # ------------------------------------------------------------------
    # Event filter — outside click detection (§7.1)
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and self._toolbar_visible
            and self._toolbar_widget
        ):
            click_pos = event.pos()  # type: ignore[attr-defined]  # QMouseEvent at runtime
            toolbar = self._toolbar_widget.widget
            if self._toolbar_contains_ruler_frame_pos(toolbar, click_pos):
                return False  # Click inside toolbar — let it through
            # tool_active toolbars stay visible — only dismissed by tool switch
            spec = self._displayed_spec()
            if spec and spec.show_trigger == "tool_active":
                return False
            self._do_commit_or_hide()
            return False  # Don't consume — allow normal processing
        return False

    def _toolbar_contains_ruler_frame_pos(self, toolbar: QWidget, pos: QPoint) -> bool:
        if toolbar.isWindow():
            return toolbar.frameGeometry().contains(self._ruler_frame.mapToGlobal(pos))
        return toolbar.geometry().contains(pos)

    # ------------------------------------------------------------------
    # Default trigger handlers
    # ------------------------------------------------------------------

    def _on_tool_changed(self, _payload: Any = None) -> None:
        """Hide/commit on tool switch."""
        if self._toolbar_visible:
            self._do_commit_or_hide()

    def _on_transform_start(self, _payload: Any = None) -> None:
        """Hide toolbar when a transform operation begins."""
        if self._toolbar_visible:
            self.hide()

    def _on_layer_selected(self, _payload: Any = None) -> None:
        """Hide/commit on layer switch — but only for pointer_up triggers.

        text_editing / preview_start toolbars manage their own lifecycle;
        the layer may change when the tool refocuses a text object on click.
        """
        if not self._toolbar_visible:
            return
        spec = self._displayed_spec()
        if spec and spec.show_trigger in ("text_editing", "preview_start", "tool_active"):
            return
        self._do_commit_or_hide()

    def _displayed_spec(self) -> ContextToolbarSpec | None:
        return self._active_spec or self._current_spec

    # ------------------------------------------------------------------
    # External provider API
    # ------------------------------------------------------------------

    def register_external_spec(
        self,
        provider_id: str,
        spec: ContextToolbarSpec,
        property_specs: list[PropertySpec] | None = None,
    ) -> None:
        self._external_specs[provider_id] = (spec, property_specs or [])

    def show_external(self, provider_id: str, anchor_rect: QRect) -> None:
        entry = self._external_specs.get(provider_id)
        if entry is None:
            return
        spec, pspecs = entry
        self._stroke_bbox = anchor_rect
        self._show_toolbar(provider_id, spec, pspecs)

    def hide_external(self, provider_id: str) -> None:
        if self._toolbar_visible and self._active_provider_id == provider_id:
            self.hide()


class _ToolbarRef:
    """Thin wrapper to avoid circular import at module level."""

    __slots__ = ("widget",)

    def __init__(self, widget: Any) -> None:
        self.widget = widget
