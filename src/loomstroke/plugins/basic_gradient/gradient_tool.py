"""Gradient fill tool plugin with interactive pin-based stop editing."""

from __future__ import annotations

import contextlib
import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np

from ...core.commands import Command, StrokePoint
from ...host_api import HostAPI
from ...plugins.sdk.base import PluginMetadata, PluginType
from ...plugins.sdk.tool import (
    ContextToolbarSpec,
    PropertySpec,
    PropertyType,
    ToolDescriptor,
    ToolMetadata,
    ToolSettings,
)
from ..common.tool_icons import resolve_tool_icon_path
from .gradient_command import (
    GRADIENT_LINEAR,
    GRADIENT_RADIAL,
    GradientCommand,
    GradientStops,
    _get_target_array,
    _render_gradient,
)

logger = logging.getLogger(__name__)

_GRADIENT_CTX_KEYS = ["gradient_type", "color_a", "color_b", "middle_stops", "dither"]

try:
    from PySide6.QtCore import QCoreApplication, QPointF, Qt  # noqa: PLC0415
    from PySide6.QtGui import (  # noqa: PLC0415
        QBrush,
        QColor,
        QImage,
        QPainter,
        QPainterPath,
        QPen,
    )
except Exception:  # pragma: no cover
    QCoreApplication = None  # type: ignore[assignment,misc]  # optional PySide6 fallback
    QImage = None  # type: ignore[assignment,misc]  # optional PySide6 fallback
    QPainter = None  # type: ignore[assignment,misc]  # optional PySide6 fallback

_MAX_MIDDLE_STOPS = 3

# Interaction states
_IDLE = "idle"
_DRAGGING = "dragging"
_EDITING = "editing"
_MARKER_DRAG = "marker_drag"

# Visual constants (screen pixels)
_PIN_RADIUS_PX = 9
_PIN_HIT_RADIUS_PX = 14
_PIN_HEIGHT_FACTOR = 1.7
_CLICK_THRESHOLD_PX = 4

# Blend mode string → QPainter.CompositionMode mapping
_BLEND_MODE_MAP: dict[str, Any] = {}
if QPainter is not None:
    _CM = QPainter.CompositionMode
    _BLEND_MODE_MAP = {
        "normal": _CM.CompositionMode_SourceOver,
        "darken": _CM.CompositionMode_Darken,
        "multiply": _CM.CompositionMode_Multiply,
        "color_burn": _CM.CompositionMode_ColorBurn,
        "lighten": _CM.CompositionMode_Lighten,
        "screen": _CM.CompositionMode_Screen,
        "color_dodge": _CM.CompositionMode_ColorDodge,
        "linear_dodge": _CM.CompositionMode_Plus,
        "overlay": _CM.CompositionMode_Overlay,
        "soft_light": _CM.CompositionMode_SoftLight,
        "hard_light": _CM.CompositionMode_HardLight,
        "difference": _CM.CompositionMode_Difference,
        "exclusion": _CM.CompositionMode_Exclusion,
    }


def _ts(text: str) -> str:
    if QCoreApplication is None:
        return text
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("BasicGradientPlugin", text, use_fallback=True)


_GRADIENT_TYPE_LABELS = {
    GRADIENT_LINEAR: _ts("Linear"),
    GRADIENT_RADIAL: _ts("Radial"),
}


def _rgba_f_to_255(c: tuple[float, ...]) -> tuple[float, float, float, float]:
    """Convert 0.0-1.0 RGBA to 0-255 range."""
    return (c[0] * 255.0, c[1] * 255.0, c[2] * 255.0, c[3] * 255.0)


@dataclass(slots=True)
class _StopInfo:
    """Internal representation of a gradient stop with its attribute slot."""

    position: float
    color: tuple[float, float, float, float]  # RGBA 0.0-1.0
    slot: str  # "start", "end", "1", "2", "3"


class BasicGradientPlugin:
    """Gradient fill tool with interactive drag-line and pin-marker editing."""

    def __init__(self) -> None:
        self.host_api: HostAPI | None = None
        self._pending_commands: list[Command] = []

        # Interaction state
        self._state: str = _IDLE
        self._line_start: tuple[float, float] | None = None
        self._line_end: tuple[float, float] | None = None
        self._dragging_marker_idx: int | None = None
        self._marker_drag_origin: tuple[float, float] | None = None
        self._click_origin: tuple[float, float] | None = None
        self._ctrl_held: bool = False

        # Settings (also source-of-truth for property bar)
        self.gradient_type: str = GRADIENT_LINEAR
        self.dither: bool = True
        self.color_a: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
        self.color_b: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
        self.middle_stops: int = 0
        self.stop_1_pos: float = 0.25
        self.stop_1_color: tuple[float, float, float, float] = (0.5, 0.5, 0.5, 1.0)
        self.stop_2_pos: float = 0.50
        self.stop_2_color: tuple[float, float, float, float] = (0.5, 0.5, 0.5, 1.0)
        self.stop_3_pos: float = 0.75
        self.stop_3_color: tuple[float, float, float, float] = (0.5, 0.5, 0.5, 1.0)

        # Live-preview backup (original layer buffer before gradient applied)
        self._preview_backup: np.ndarray | None = None
        self._preview_layer_id: str | None = None

        self.metadata = PluginMetadata(
            id="loomstroke.basic_gradient",
            name=_ts("Basic Gradient"),
            version="1.0.0",
            description=_ts("Linear and radial gradient fill tool"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )

    # ------------------------------------------------------------------
    # Descriptor / metadata
    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="basic_gradient",
            display_name=_ts("Gradient"),
            cursor="crosshair",
            category="fill",
            order=16,
        )

    def tool_metadata(self) -> ToolMetadata:
        descriptor = self.descriptor()
        icon_path = resolve_tool_icon_path(descriptor.id)
        icon_svg = str(icon_path) if icon_path else descriptor.icon_svg

        props: list[PropertySpec] = [
            PropertySpec(
                key="gradient_type",
                display_name=_ts("Type"),
                type=PropertyType.ENUM,
                enum_items=[
                    _GRADIENT_TYPE_LABELS[GRADIENT_LINEAR],
                    _GRADIENT_TYPE_LABELS[GRADIENT_RADIAL],
                ],
                default_value=_GRADIENT_TYPE_LABELS[GRADIENT_LINEAR],
            ),
            PropertySpec(
                key="color_a",
                display_name=_ts("Start Color"),
                type=PropertyType.COLOR,
                default_value=(0.0, 0.0, 0.0, 1.0),
            ),
            PropertySpec(
                key="color_b",
                display_name=_ts("End Color"),
                type=PropertyType.COLOR,
                default_value=(1.0, 1.0, 1.0, 1.0),
            ),
            PropertySpec(
                key="middle_stops",
                display_name=_ts("Middle Stops"),
                type=PropertyType.INT,
                min_value=0,
                max_value=_MAX_MIDDLE_STOPS,
                default_value=0,
                step=1,
            ),
        ]
        for i in range(1, _MAX_MIDDLE_STOPS + 1):
            props.append(
                PropertySpec(
                    key=f"stop_{i}_pos",
                    display_name=_ts(f"Stop {i} Pos"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=i / (_MAX_MIDDLE_STOPS + 1),
                    step=0.01,
                )
            )
            props.append(
                PropertySpec(
                    key=f"stop_{i}_color",
                    display_name=_ts(f"Stop {i} Color"),
                    type=PropertyType.COLOR,
                    default_value=(0.5, 0.5, 0.5, 1.0),
                )
            )
        props.append(
            PropertySpec(
                key="dither",
                display_name=_ts("Dither"),
                type=PropertyType.BOOL,
                default_value=True,
            )
        )

        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category=descriptor.category or "fill",
            tags=["gradient", "fill"],
            icon_svg=icon_svg,
            group_id="fill.basic",
            group_label=_ts("Fill"),
            group_order=30,
            properties=props,
        )

    def context_toolbar_spec(self) -> ContextToolbarSpec | None:
        return ContextToolbarSpec(
            show_trigger="preview_start",
            property_keys=["gradient_type", "color_a", "color_b", "middle_stops", "dither"],
            anchor_mode="stroke_bbox",
            mode="preview",
            on_param_preview=None,
            on_commit=self._apply_gradient,
            on_cancel=self._cancel_editing,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    def activate(self, host: HostAPI) -> None:
        self.host_api = host
        if not self._tool_registered(host):
            register_tool = getattr(host, "register_tool", None)
            if callable(register_tool):
                register_tool(self)

    def enable(self) -> None:
        if self.host_api:
            self.host_api.events.emit("tool.changed", {"tool": "basic_gradient"})

    def disable(self) -> None:
        self._cancel_editing()

    def deactivate(self) -> None:
        self._cancel_editing()
        if self.host_api:
            unregister = getattr(self.host_api, "unregister_tool", None)
            if callable(unregister):
                unregister(self.descriptor().id)
        self.host_api = None

    # ------------------------------------------------------------------
    # Pointer handlers (state machine)
    def on_pointer_down(  # noqa: PLR0912
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del alt, meta
        self._ctrl_held = ctrl
        px, py = pt.pos[0], pt.pos[1]

        if self._state == _IDLE and not self._layer_supports_gradient():
            return

        if self._state == _IDLE:
            self._line_start = (px, py)
            self._line_end = (px, py)
            self._state = _DRAGGING
            self._install_overlay()
            self._take_preview_backup()

        elif self._state == _EDITING:
            hit = self._hit_test_marker(px, py)
            if hit is not None:
                if ctrl:
                    # Ctrl+click: remove stop (if not start/end)
                    self._remove_stop(hit)
                    self._refresh_overlay()
                else:
                    self._dragging_marker_idx = hit
                    self._marker_drag_origin = (px, py)
                    self._state = _MARKER_DRAG
            elif self._is_on_line(px, py):
                # Click on line: add stop
                t = self._project_on_line(px, py)
                color = self._interpolate_color_at(t)
                self._add_stop(t, color)
                self._refresh_overlay()
            else:
                # Click elsewhere: apply current gradient
                self._apply_gradient()

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        last: StrokePoint | None = None
        for pt in pts:
            last = pt
        if last is None:
            return

        if self._state == _DRAGGING:
            self._line_end = (last.pos[0], last.pos[1])
            self._refresh_overlay()

        elif self._state == _MARKER_DRAG and self._dragging_marker_idx is not None:
            t = self._project_on_line(last.pos[0], last.pos[1])
            self._set_stop_position(self._dragging_marker_idx, t)
            self._refresh_overlay()

    def on_pointer_up(self, pt: StrokePoint) -> None:
        px, py = pt.pos[0], pt.pos[1]

        if self._state == _DRAGGING:
            self._line_end = (px, py)
            x0, y0 = self._line_start or (0, 0)
            if abs(px - x0) < 2 and abs(py - y0) < 2:
                self._restore_preview_backup()
                self._reset_to_idle()
                return
            self._state = _EDITING
            self._refresh_overlay()
            if self.host_api:
                self.host_api.events.emit("tool.preview_start", {"tool_id": "basic_gradient"})

        elif self._state == _MARKER_DRAG:
            idx = self._dragging_marker_idx
            self._dragging_marker_idx = None
            self._state = _EDITING
            # Distinguish click from drag
            if idx is not None and self._marker_drag_origin is not None:
                ox, oy = self._marker_drag_origin
                dist = math.sqrt((px - ox) ** 2 + (py - oy) ** 2)
                if dist < _CLICK_THRESHOLD_PX / self._current_zoom():
                    self._open_color_picker(idx)
            self._marker_drag_origin = None

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        del pt

    def on_key(self, key: str, pressed: bool) -> bool:
        if not pressed:
            return False
        key_lower = key.lower()
        if self._state == _EDITING:
            if key_lower in ("return", "enter"):
                self._apply_gradient()
                return True
            if key_lower in ("esc", "escape"):
                self._cancel_editing()
                return True
        return False

    def on_wheel(self, delta: float, pt: StrokePoint) -> bool:
        del delta, pt
        return False

    # ------------------------------------------------------------------
    # Undo guard — block history undo/redo while preview is active
    def can_undo(self) -> bool:
        return self._state in (_DRAGGING, _EDITING, _MARKER_DRAG)

    def on_undo(self) -> bool:
        # Consume the request so ToolManager does not forward to history
        return True

    # ------------------------------------------------------------------
    # Command building
    def build_commands(self) -> list[Command]:
        if not self._pending_commands:
            return []
        commands = list(self._pending_commands)
        self._pending_commands.clear()
        return commands

    def _connect_clear_on_recompose(self) -> None:
        """One-shot connection: clear preview after next composited_changed."""
        if not self.host_api:
            self._clear_preview_now()
            return
        canvas = getattr(self.host_api, "canvas", None)
        signal = getattr(canvas, "composited_changed", None) if canvas else None
        if signal is None:
            self._clear_preview_now()
            return
        signal.connect(self._on_recompose_after_apply)

    def _on_recompose_after_apply(self) -> None:
        """Called once after recompose; disconnect and clear preview."""
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        signal = getattr(canvas, "composited_changed", None) if canvas else None
        if signal is not None:
            with contextlib.suppress(Exception):
                signal.disconnect(self._on_recompose_after_apply)
        self._clear_preview_now()

    def _clear_preview_now(self) -> None:
        widget = self._get_canvas_widget()
        if widget is not None:
            widget.set_tool_preview(None)

    # ------------------------------------------------------------------
    # Settings
    def get_settings(self) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "gradient_type": _GRADIENT_TYPE_LABELS.get(
                self.gradient_type, _GRADIENT_TYPE_LABELS[GRADIENT_LINEAR]
            ),
            "dither": self.dither,
            "color_a": self.color_a,
            "color_b": self.color_b,
            "middle_stops": self.middle_stops,
        }
        for i in range(1, _MAX_MIDDLE_STOPS + 1):
            settings[f"stop_{i}_pos"] = getattr(self, f"stop_{i}_pos")
            settings[f"stop_{i}_color"] = getattr(self, f"stop_{i}_color")
        return settings

    def set_settings(self, settings: ToolSettings | dict[str, Any]) -> None:
        if isinstance(settings, dict):
            self._apply_setting_dict(settings)

    def set_setting(self, key: str, value: Any) -> None:
        self._apply_setting_dict({key: value})

    def create_options_widget(self) -> Any:
        return None

    def _apply_setting_dict(self, payload: dict[str, Any]) -> None:
        if "gradient_type" in payload:
            raw = payload["gradient_type"]
            for k, v in _GRADIENT_TYPE_LABELS.items():
                if raw in (v, k):
                    self.gradient_type = k
                    break
        if "dither" in payload:
            self.dither = bool(payload["dither"])
        if "color_a" in payload:
            self.color_a = _parse_color(payload["color_a"])
        if "color_b" in payload:
            self.color_b = _parse_color(payload["color_b"])
        if "middle_stops" in payload:
            self.middle_stops = max(0, min(_MAX_MIDDLE_STOPS, int(payload["middle_stops"])))
        for i in range(1, _MAX_MIDDLE_STOPS + 1):
            pk, ck = f"stop_{i}_pos", f"stop_{i}_color"
            if pk in payload:
                setattr(self, pk, max(0.0, min(1.0, float(payload[pk]))))
            if ck in payload:
                setattr(self, ck, _parse_color(payload[ck]))
        # If in editing mode, refresh the overlay
        if self._state in (_EDITING, _MARKER_DRAG):
            self._refresh_overlay()

    # ------------------------------------------------------------------
    # Stop management
    def _editing_stops(self) -> list[_StopInfo]:
        """Build ordered stop list from current settings."""
        stops = [_StopInfo(0.0, self.color_a, "start")]
        for i in range(1, min(self.middle_stops, _MAX_MIDDLE_STOPS) + 1):
            stops.append(
                _StopInfo(
                    getattr(self, f"stop_{i}_pos"),
                    getattr(self, f"stop_{i}_color"),
                    str(i),
                )
            )
        stops.append(_StopInfo(1.0, self.color_b, "end"))
        stops.sort(key=lambda s: s.position)
        return stops

    def _build_stops_255(self) -> GradientStops | None:
        if self.middle_stops <= 0:
            return None
        stops: GradientStops = []
        for i in range(1, min(self.middle_stops, _MAX_MIDDLE_STOPS) + 1):
            pos = getattr(self, f"stop_{i}_pos", 0.5)
            color = getattr(self, f"stop_{i}_color", (0.5, 0.5, 0.5, 1.0))
            stops.append((pos, _rgba_f_to_255(color)))
        return stops if stops else None

    def _set_stop_color(self, idx: int, color: tuple[float, float, float, float]) -> None:
        stops = self._editing_stops()
        if idx < 0 or idx >= len(stops):
            return
        s = stops[idx]
        if s.slot == "start":
            self.color_a = color
        elif s.slot == "end":
            self.color_b = color
        else:
            setattr(self, f"stop_{s.slot}_color", color)

    def _set_stop_position(self, idx: int, new_pos: float) -> None:
        stops = self._editing_stops()
        if idx < 0 or idx >= len(stops):
            return
        s = stops[idx]
        if s.slot in ("start", "end"):
            return  # Cannot move endpoints
        setattr(self, f"stop_{s.slot}_pos", max(0.01, min(0.99, new_pos)))

    def _add_stop(self, position: float, color: tuple[float, float, float, float]) -> None:
        if self.middle_stops >= _MAX_MIDDLE_STOPS:
            return
        self.middle_stops += 1
        n = self.middle_stops
        setattr(self, f"stop_{n}_pos", position)
        setattr(self, f"stop_{n}_color", color)

    def _remove_stop(self, idx: int) -> None:
        stops = self._editing_stops()
        if idx < 0 or idx >= len(stops):
            return
        s = stops[idx]
        if s.slot in ("start", "end"):
            return
        slot_num = int(s.slot)
        # Shift subsequent slots down
        for i in range(slot_num, self.middle_stops):
            if i + 1 <= _MAX_MIDDLE_STOPS:
                setattr(self, f"stop_{i}_pos", getattr(self, f"stop_{i + 1}_pos"))
                setattr(self, f"stop_{i}_color", getattr(self, f"stop_{i + 1}_color"))
        self.middle_stops -= 1

    def _interpolate_color_at(self, t: float) -> tuple[float, float, float, float]:
        stops = self._editing_stops()
        for i in range(len(stops) - 1):
            if stops[i].position <= t <= stops[i + 1].position:
                span = stops[i + 1].position - stops[i].position
                if span < 1e-6:
                    return stops[i].color
                lt = (t - stops[i].position) / span
                c0, c1 = stops[i].color, stops[i + 1].color
                return tuple(c0[ch] + (c1[ch] - c0[ch]) * lt for ch in range(4))  # type: ignore[return-value]  # variable-length tuple vs 4-tuple
        return stops[-1].color if stops else self.color_a

    # ------------------------------------------------------------------
    # Apply / cancel
    def _apply_gradient(self) -> None:
        if self._line_start and self._line_end and self.host_api:
            x0, y0 = self._line_start
            x1, y1 = self._line_end
            ca = _rgba_f_to_255(self.color_a)
            cb = _rgba_f_to_255(self.color_b)
            command = GradientCommand(
                self._active_layer_id(),
                x0,
                y0,
                x1,
                y1,
                ca,
                cb,
                gradient_type=self.gradient_type,
                dither=self.dither,
                stops=self._build_stops_255(),
            )
            # Buffer already contains the gradient from preview;
            # inject backup so apply() is a no-op but undo works.
            if self._preview_backup is not None:
                command.set_pre_applied(self._preview_backup)
            self._pending_commands.append(command)
        self._preview_backup = None
        self._preview_layer_id = None
        self._reset_to_idle()

    def _cancel_editing(self) -> None:
        self._restore_preview_backup()
        self._reset_to_idle()

    def _reset_to_idle(self, *, keep_preview: bool = False) -> None:
        self._state = _IDLE
        self._line_start = None
        self._line_end = None
        self._dragging_marker_idx = None
        self._marker_drag_origin = None
        if keep_preview:
            # Remove only the line/pin overlay; keep preview image until
            # the command is flushed to avoid flicker.
            widget = self._get_canvas_widget()
            if widget is not None and hasattr(widget, "set_tool_overlay_fn"):
                widget.set_tool_overlay_fn(None)
        else:
            self._clear_all_overlays()

    # ------------------------------------------------------------------
    # Preview / overlay
    def _refresh_overlay(self) -> None:
        if not self._line_start or not self._line_end:
            return
        x0, y0 = self._line_start
        x1, y1 = self._line_end
        self._update_preview(x0, y0, x1, y1)

    def _update_preview(self, x0: float, y0: float, x1: float, y1: float) -> None:
        if self._preview_backup is None:
            return
        layer, target = self._resolve_preview_layer()
        if layer is None or target is None:
            return

        from ...core.commands import _is_qimage_layer, _layer_transform  # noqa: PLC0415

        # Restore original pixels so each preview is computed from scratch
        target[:] = self._preview_backup

        height, width = target.shape[:2]
        mapping = _layer_transform(layer).to_affine()
        if mapping.inverse() is None:
            return
        ca, cb = _rgba_f_to_255(self.color_a), _rgba_f_to_255(self.color_b)
        gradient = _render_gradient(
            width,
            height,
            x0,
            y0,
            x1,
            y1,
            ca,
            cb,
            self.gradient_type,
            self.dither,
            stops=self._build_stops_255(),
            pixel_transform=mapping,
        )

        # Source-Over composite (same logic as GradientCommand.apply)
        is_qimage = _is_qimage_layer(layer)
        if is_qimage:
            r, g, b = gradient[:, :, 0], gradient[:, :, 1], gradient[:, :, 2]
            a = gradient[:, :, 3] / 255.0
            src_b = (b * a).astype(np.uint8)
            src_g = (g * a).astype(np.uint8)
            src_r = (r * a).astype(np.uint8)
            dst_a = target[:, :, 3].astype(np.float32) / 255.0
            out_a = a + dst_a * (1.0 - a)
            for ch_src, ch_idx in [(src_b, 0), (src_g, 1), (src_r, 2)]:
                dst_ch = target[:, :, ch_idx].astype(np.float32)
                target[:, :, ch_idx] = np.clip(ch_src + dst_ch * (1.0 - a), 0.0, 255.0).astype(
                    np.uint8
                )
            target[:, :, 3] = np.clip(out_a * 255.0, 0.0, 255.0).astype(np.uint8)
        else:
            src_a = gradient[:, :, 3] / 255.0
            dst_a = target[:, :, 3] / 255.0
            out_a = src_a + dst_a * (1.0 - src_a)
            for ch in range(3):
                safe_a = np.where(out_a > 0, out_a, 1.0)
                target[:, :, ch] = np.where(
                    out_a > 0,
                    (gradient[:, :, ch] * src_a + target[:, :, ch] * dst_a * (1.0 - src_a))
                    / safe_a,
                    0.0,
                )
            target[:, :, 3] = out_a * 255.0

        # Notify GPU pipeline
        tv = getattr(layer, "texture_version", None)
        if tv is not None:
            layer.texture_version = tv + 1
        if hasattr(layer, "mark_dirty"):
            with contextlib.suppress(Exception):
                layer.mark_dirty(0, 0, width, height)
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if canvas is not None:
            canvas._recompose()  # noqa: SLF001

    def _install_overlay(self) -> None:
        widget = self._get_canvas_widget()
        if widget is not None and hasattr(widget, "set_tool_overlay_fn"):
            widget.set_tool_overlay_fn(self._paint_overlay)

    def _clear_all_overlays(self) -> None:
        widget = self._get_canvas_widget()
        if widget is not None:
            widget.set_tool_preview(None)
            if hasattr(widget, "set_tool_overlay_fn"):
                widget.set_tool_overlay_fn(None)

    # ------------------------------------------------------------------
    # Layer-buffer preview helpers

    def _resolve_preview_layer(self) -> tuple[Any, np.ndarray | None]:
        """Return (layer, numpy_buffer) for the preview target layer."""
        if not self.host_api or self._preview_layer_id is None:
            return None, None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None, None
        from ...core.commands import _resolve_target_layer  # noqa: PLC0415

        layer = _resolve_target_layer(canvas, self._preview_layer_id)
        if layer is None:
            return None, None
        target = _get_target_array(layer)
        return layer, target

    def _take_preview_backup(self) -> None:
        """Snapshot the active layer buffer before gradient preview."""
        self._preview_layer_id = self._active_layer_id()
        _layer, target = self._resolve_preview_layer()
        if target is not None:
            self._preview_backup = target.copy()
        else:
            self._preview_backup = None

    def _restore_preview_backup(self) -> None:
        """Restore original layer buffer and trigger recompose."""
        if self._preview_backup is None:
            return
        layer, target = self._resolve_preview_layer()
        if layer is not None and target is not None:
            target[:] = self._preview_backup
            tv = getattr(layer, "texture_version", None)
            if tv is not None:
                layer.texture_version = tv + 1
            if hasattr(layer, "mark_dirty"):
                h, w = target.shape[:2]
                with contextlib.suppress(Exception):
                    layer.mark_dirty(0, 0, w, h)
            canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
            if canvas is not None:
                canvas._recompose()  # noqa: SLF001
        self._preview_backup = None
        self._preview_layer_id = None

    # ------------------------------------------------------------------
    # Overlay painting
    def _paint_overlay(self, painter: Any) -> None:  # noqa: PLR0912
        """Draw the gradient line and pin markers on the canvas."""
        if QPainter is None or not isinstance(painter, QPainter):
            return
        if not self._line_start or not self._line_end:
            return

        x0, y0 = self._line_start
        x1, y1 = self._line_end

        # Compute inverse scale for constant-screen-size elements
        tr = painter.transform()
        scale = math.sqrt(tr.m11() ** 2 + tr.m21() ** 2)
        inv = 1.0 / max(scale, 0.01)

        # --- Draw gradient line (always visible during drag and editing) ---
        line_pen = QPen(QColor(255, 255, 255, 220), 3.0 * inv)
        painter.setPen(line_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        # Inner dashed line
        dash_pen = QPen(QColor(40, 40, 40, 200), 1.5 * inv)
        dash_pen.setDashPattern([6, 4])
        painter.setPen(dash_pen)
        painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))

        # --- Draw pin markers (only in editing / marker drag states) ---
        if self._state not in (_EDITING, _MARKER_DRAG):
            return

        stops = self._editing_stops()
        line_dx, line_dy = x1 - x0, y1 - y0
        line_len = math.sqrt(line_dx**2 + line_dy**2)
        if line_len < 1:
            return

        # Normal direction (pins extend perpendicular to line)
        nx, ny = -line_dy / line_len, line_dx / line_len

        pin_r = _PIN_RADIUS_PX * inv
        pin_h = pin_r * _PIN_HEIGHT_FACTOR

        for i, stop in enumerate(stops):
            # Position on line
            lx = x0 + stop.position * line_dx
            ly = y0 + stop.position * line_dy
            # Circle center offset perpendicular to line
            cx = lx + nx * pin_h
            cy = ly + ny * pin_h

            fill = QColor.fromRgbF(
                min(1.0, stop.color[0]),
                min(1.0, stop.color[1]),
                min(1.0, stop.color[2]),
                min(1.0, stop.color[3]),
            )
            is_dragging = self._state == _MARKER_DRAG and self._dragging_marker_idx == i
            _draw_pin(
                painter,
                lx,
                ly,
                cx,
                cy,
                pin_r,
                fill,
                border_width=2.5 * inv if is_dragging else 1.5 * inv,
            )

    # ------------------------------------------------------------------
    # Color picker
    def _open_color_picker(self, idx: int) -> None:
        from loomstroke.ui.widgets.color_picker_popup import ColorPickerPopup  # noqa: PLC0415

        stops = self._editing_stops()
        if idx < 0 or idx >= len(stops):
            return
        c = stops[idx].color
        old = QColor.fromRgbF(
            min(1.0, c[0]),
            min(1.0, c[1]),
            min(1.0, c[2]),
            min(1.0, c[3]),
        )
        mw = None
        if self.host_api:
            ui = getattr(self.host_api, "ui", None)
            getter = getattr(ui, "get_main_window", None) if ui else None
            if callable(getter):
                with contextlib.suppress(Exception):
                    mw = getter()
        new = ColorPickerPopup.pick_color(old, mw)
        if new is not None and new.isValid():
            self._set_stop_color(idx, (new.redF(), new.greenF(), new.blueF(), new.alphaF()))
            self._refresh_overlay()

    # ------------------------------------------------------------------
    # Hit testing / geometry
    def _hit_test_marker(self, px: float, py: float) -> int | None:
        if not self._line_start or not self._line_end:
            return None
        x0, y0 = self._line_start
        x1, y1 = self._line_end
        zoom = self._current_zoom()
        hit_r = _PIN_HIT_RADIUS_PX / zoom
        stops = self._editing_stops()
        dx, dy = x1 - x0, y1 - y0
        line_len = math.sqrt(dx * dx + dy * dy)

        # Compute pin circle center offset (same as _paint_overlay)
        if line_len >= 1:
            nx, ny = -dy / line_len, dx / line_len
            pin_h = (_PIN_RADIUS_PX / zoom) * _PIN_HEIGHT_FACTOR
        else:
            nx, ny = 0.0, 0.0
            pin_h = 0.0

        # Check in reverse so top-drawn markers are hit first
        for i in range(len(stops) - 1, -1, -1):
            # Line anchor point
            lx = x0 + stops[i].position * dx
            ly = y0 + stops[i].position * dy
            # Pin circle center (offset perpendicular to line)
            cx = lx + nx * pin_h
            cy = ly + ny * pin_h
            # Hit if click is near circle center OR near line anchor
            dist_circle = math.sqrt((px - cx) ** 2 + (py - cy) ** 2)
            dist_anchor = math.sqrt((px - lx) ** 2 + (py - ly) ** 2)
            if dist_circle <= hit_r or dist_anchor <= hit_r:
                return i
        return None

    def _is_on_line(self, px: float, py: float) -> bool:
        return self._distance_to_line(px, py) <= _PIN_HIT_RADIUS_PX / self._current_zoom()

    def _distance_to_line(self, px: float, py: float) -> float:
        t = self._project_on_line(px, py)
        if not self._line_start or not self._line_end:
            return float("inf")
        x0, y0 = self._line_start
        x1, y1 = self._line_end
        cx = x0 + t * (x1 - x0)
        cy = y0 + t * (y1 - y0)
        return math.sqrt((px - cx) ** 2 + (py - cy) ** 2)

    def _project_on_line(self, px: float, py: float) -> float:
        if not self._line_start or not self._line_end:
            return 0.0
        x0, y0 = self._line_start
        x1, y1 = self._line_end
        dx, dy = x1 - x0, y1 - y0
        ln2 = dx * dx + dy * dy
        if ln2 < 1:
            return 0.0
        return max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / ln2))

    # ------------------------------------------------------------------
    # Canvas helpers
    def _current_zoom(self) -> float:
        widget = self._get_canvas_widget()
        if widget is not None:
            vp = getattr(widget, "viewport", None)
            if vp is not None:
                z = getattr(vp, "zoom", None) or getattr(vp, "_zoom", None)
                if callable(z):
                    return max(float(z()), 0.01)
                if z is not None:
                    return max(float(z), 0.01)
        return 1.0

    def _canvas_size(self) -> tuple[int, int] | None:
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        getter = getattr(canvas, "get_canvas_size", None)
        if callable(getter):
            try:
                size = getter()
                if isinstance(size, tuple) and len(size) == 2 and size[0] > 0 and size[1] > 0:
                    return size
            except Exception:
                logger.debug("canvas size getter failed", exc_info=True)
        # Fallback to direct attributes
        w = getattr(canvas, "_width", None) or getattr(canvas, "width", None)
        h = getattr(canvas, "_height", None) or getattr(canvas, "height", None)
        if callable(w):
            w = w()
        if callable(h):
            h = h()
        if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
            return (w, h)
        return None

    def _get_canvas_widget(self) -> Any:
        if not self.host_api:
            return None
        ui = getattr(self.host_api, "ui", None)
        getter = getattr(ui, "get_main_window", None) if ui else None
        if not callable(getter):
            return None
        try:
            mw = getter()
        except Exception:
            return None
        if mw is None:
            return None
        widget = getattr(mw, "canvas", None)
        if widget is not None and hasattr(widget, "set_tool_preview"):
            return widget
        return None

    def _layer_supports_gradient(self) -> bool:
        """Return True if the active layer can receive a gradient fill.

        Raster layers and mask-edit mode are allowed.
        Vector, effect, and group layers are blocked.
        """
        info = self._active_layer_info()
        if not info:
            return True  # fallback: allow if info unavailable
        layer_type = info.get("layer_type", "raster")
        if layer_type == "raster":
            return True
        if layer_type == "mask":
            return True
        # Check mask edit mode (mask is part of a parent layer,
        # not a standalone layer type in layer_info).
        return bool(self._is_mask_edit_mode())

    def _is_mask_edit_mode(self) -> bool:
        """Return True if the active layer is in mask editing mode."""
        if not self.host_api:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        layer = getattr(canvas, "_active_layer", None)
        if callable(layer):
            try:
                layer = layer()
            except Exception:
                return False
        if layer is None:
            # Fallback: access via _layers[_active_index]
            layers = getattr(canvas, "_layers", None)
            idx = getattr(canvas, "_active_index", None)
            if isinstance(layers, list) and isinstance(idx, int) and 0 <= idx < len(layers):
                layer = layers[idx]
        if layer is None:
            return False
        return bool(getattr(layer, "mask_edit_mode", False))

    def _active_layer_info(self) -> dict[str, Any] | None:
        """Return the active layer's info dict from canvas service."""
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        layer_ref_getter = getattr(canvas, "current_layer", None)
        if not callable(layer_ref_getter):
            return None
        try:
            layer_ref = layer_ref_getter()
            layer_id = getattr(layer_ref, "id", None)
            if not layer_id:
                return None
            from ...core.tool_layer_info import tool_layer_info  # noqa: PLC0415

            return tool_layer_info(canvas, layer_id)
        except Exception:
            logger.debug("layer info lookup failed", exc_info=True)
        return None

    def _active_layer_composition_mode(self) -> Any:
        """Return QPainter.CompositionMode for the active layer's blend mode."""
        if QPainter is None:
            return None
        default = QPainter.CompositionMode.CompositionMode_SourceOver
        info = self._active_layer_info()
        if not info:
            return default
        raw = info.get("blend_mode", "normal")
        return _BLEND_MODE_MAP.get(raw, default)

    def _active_layer_opacity(self) -> float:
        """Return the active layer's opacity (0.0-1.0)."""
        info = self._active_layer_info()
        if not info:
            return 1.0
        return max(0.0, min(1.0, float(info.get("opacity", 1.0))))

    def _active_layer_id(self) -> str | None:
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        getter = getattr(canvas, "current_layer", None)
        if not callable(getter):
            return None
        try:
            layer = getter()
        except Exception:
            return None
        return getattr(layer, "id", None)

    def _tool_registered(self, host: HostAPI) -> bool:
        tool_id = self.descriptor().id
        manager = getattr(host, "_tool_manager", None)
        if manager and hasattr(manager, "get_registered_tools"):
            try:
                for desc in manager.get_registered_tools():
                    if desc.id == tool_id:
                        return True
            except Exception:
                logger.debug("get_registered_tools failed", exc_info=True)
        pending = getattr(host, "_pending_tools", None)
        if isinstance(pending, list):
            for tool in pending:
                if tool is self:
                    return True
                descriptor_fn = getattr(tool, "descriptor", None)
                if callable(descriptor_fn):
                    try:
                        if descriptor_fn().id == tool_id:
                            return True
                    except Exception:
                        continue
        return False


# ------------------------------------------------------------------
# Free functions


def _parse_color(value: Any) -> tuple[float, float, float, float]:
    """Parse a color value to RGBA 0.0-1.0 tuple."""
    if isinstance(value, tuple) and len(value) >= 3:
        r, g, b = float(value[0]), float(value[1]), float(value[2])
        a = float(value[3]) if len(value) >= 4 else 1.0
        if max(r, g, b, a) > 1.0:
            r, g, b = r / 255.0, g / 255.0, b / 255.0
            if a > 1.0:
                a /= 255.0
        return (r, g, b, a)
    return (0.0, 0.0, 0.0, 1.0)


def _draw_pin(
    painter: Any,
    tip_x: float,
    tip_y: float,
    center_x: float,
    center_y: float,
    radius: float,
    fill_color: Any,
    border_width: float = 1.5,
) -> None:
    """Draw a Google-Maps-style pin marker."""
    # Direction from center to tip
    dx = tip_x - center_x
    dy = tip_y - center_y
    dist = math.sqrt(dx * dx + dy * dy)
    if dist < 0.01:
        return

    # Perpendicular for triangle base width
    px, py = -dy / dist, dx / dist
    tw = radius * 0.55  # triangle half-width

    # Triangle from tip to circle tangent area
    path = QPainterPath()
    path.moveTo(QPointF(tip_x, tip_y))
    path.lineTo(QPointF(center_x + px * tw, center_y + py * tw))
    path.lineTo(QPointF(center_x - px * tw, center_y - py * tw))
    path.closeSubpath()

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(fill_color))
    painter.drawPath(path)

    # Circle body
    painter.setPen(QPen(QColor(255, 255, 255), border_width))
    painter.setBrush(QBrush(fill_color))
    painter.drawEllipse(QPointF(center_x, center_y), radius, radius)

    # Inner dark ring for contrast
    painter.setPen(QPen(QColor(40, 40, 40, 120), border_width * 0.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(
        QPointF(center_x, center_y), radius + border_width * 0.5, radius + border_width * 0.5
    )


def create_plugin() -> BasicGradientPlugin:
    return BasicGradientPlugin()
