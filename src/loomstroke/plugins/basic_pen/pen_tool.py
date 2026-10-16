# ruff: noqa: I001

"""

Basic pen tool implementation.



Provides painting functionality with pressure and tilt support.

Complies with Master.md plugin SDK specifications.

"""

from __future__ import annotations

import logging
import math
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPointF, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDockWidget, QFileDialog, QMessageBox, QWidget

from ...core.commands import (
    BrushSettings,
    PaintCommand,
    StampBrushCommand,
    invalidate_tip_cache,
)
from ...core.dab_variation import DabVariation
from ...core.dynamic_brush_settings import DynamicBrushSettings
from ...core.pen_taper_command import PenTaperCommand
from ...core.pen_taper_session import PenTaperSession
from ...core.pressure_stroke_point import PressureStrokePoint
from ...host_api import StrokePoint as HostStrokePoint
from ...ui._dock_safe import is_valid
from ..sdk import (
    PluginBase,
    PropertySpec,
    PropertyType,
    ToolDescriptor,
    ToolMetadata,
    ToolPreset,
)
from ..sdk.drawing_assist import ToolAssistBridge
from ..sdk.tool import ContextToolbarSpec
from .abr_importer import ABRImporter
from .brush_manager_panel import BrushManagerPanel
from .brush_store import BrushStore, _now_iso
from .pen_brush_settings import _PenBrushSettings
from .pen_dynamics_properties import PenDynamicsProperties
from .pen_dynamics_settings import PenDynamicsSettings
from .pen_preset_controller import PenPresetController
from .pressure_response_properties import PressureResponseProperties
from .pressure_response_settings import PressureResponseSettings
from .recontour.controller import RecontourController
from .tip_cache import TipCache

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("BasicPen", text, use_fallback=True)


from .pen_settings import PenSettings as PenSettings  # noqa: PLC0414


class BasicPenTool:
    """

    Basic pen tool with pressure sensitivity and settings.



    Provides core painting functionality with configurable brush properties.

    """

    _MAX_SEGMENT_POINTS = 32

    _TILT_LIMIT = 60.0

    # Spacing range that the stamp_density UI slider maps onto (logarithmic, bijective).
    # density=0.0 ↔ spacing=_DENSITY_SPACING_MAX (sparse stamps, ~3 brush diameters apart)
    # density=1.0 ↔ spacing=_DENSITY_SPACING_MIN (densest stamps)
    # Log mapping gives perceptually uniform slider: each unit of density multiplies
    # stamps-per-diameter by the same ratio (Weber-Fechner). These bounds also gate
    # the programmatic spacing clamp in set_settings; stamp_density is the only
    # user-facing UI property (the old "spacing" PropertySpec was removed because
    # density and spacing expose the same underlying value in two ways).
    _DENSITY_SPACING_MIN = 0.01
    _DENSITY_SPACING_MAX = 3.0
    _DENSITY_LOG_RATIO = math.log(_DENSITY_SPACING_MIN / _DENSITY_SPACING_MAX)

    # Sub-pixel tolerance for "same position" comparisons (旧#9). Below this
    # distance two stroke-point tuples are treated as visually identical so
    # minor float rounding in interpolation does not produce a duplicate
    # terminal stamp at release time.
    _POSITION_EPS = 1e-6

    @classmethod
    def _positions_near(cls, a: tuple[float, float], b: tuple[float, float]) -> bool:
        """True if two positions differ by less than _POSITION_EPS per axis."""

        return abs(a[0] - b[0]) < cls._POSITION_EPS and abs(a[1] - b[1]) < cls._POSITION_EPS

    @classmethod
    def _clamp_tilt(cls, tilt: Any) -> tuple[float, float] | None:
        """Parse and clamp a tilt value to [-_TILT_LIMIT, _TILT_LIMIT].

        Returns None for falsy or unparseable input so callers that treat
        "no tilt" and "bad tilt" the same way can share one code path.
        """

        if not tilt:
            return None
        try:
            tilt_x = float(tilt[0])
            tilt_y = float(tilt[1])
        except (TypeError, ValueError, IndexError):
            return None
        tilt_x = max(-cls._TILT_LIMIT, min(cls._TILT_LIMIT, tilt_x))
        tilt_y = max(-cls._TILT_LIMIT, min(cls._TILT_LIMIT, tilt_y))
        return (tilt_x, tilt_y)

    # Tool signals - disabled for now as BasicPenTool doesn't inherit from QObject

    # settings_changed = Signal(dict)

    # stroke_preview = Signal(object)  # StrokePoint or path

    def __init__(self, host_api=None):
        """Initialize basic pen tool."""

        self.host_api = host_api

        # Tool settings

        self.settings = PenSettings()
        self._pressure_response = PressureResponseSettings()
        self._dynamics = PenDynamicsSettings()

        # Stroke state

        self.stroke_points: list[HostStrokePoint] = []

        self.is_stroking = False

        self.last_point: QPointF | None = None

        self._smoothed_pos: QPointF | None = None

        self.distance_accumulator = 0.0

        self._tip_path: str | None = None

        self._hardness_mode: str = "radial"

        # Painting state

        self.painter_path = QPainterPath()

        self.temp_painter_path = QPainterPath()

        # Pending commands buffer for ToolManager pull

        self._pending_commands: list[Any] = []

        self._last_submitted_index = 0

        self._stroke_id = 0

        # Per-instance session id avoids merge_key collisions when the tool
        # is re-instantiated while prior commands (undo stack, gallery) are
        # still alive with stroke_id values starting from 0.
        self._session_uuid = uuid.uuid4().hex[:8]
        self._assist = ToolAssistBridge("basic_pen")
        self._recontour = RecontourController(self)
        self._taper: PenTaperSession | None = None

        self._transaction_active = False

        self._perf_debug = False

        self._perf_sample_every = 60

        self._perf_warn_steps = 64

        self._perf_move_count = 0

        self._perf_max_steps = 0

        self._perf_max_points = 0

        self._perf_total_points = 0

        self._perf_stroke_start = 0.0

        self._perf_last_warn = 0.0

        logger.info("BasicPenTool initialized")

    @classmethod
    def _spacing_from_density(cls, value: float) -> float:

        density = max(0.0, min(1.0, float(value)))

        # Log mapping: spacing = SPACING_MAX * (SPACING_MIN / SPACING_MAX)^density
        return cls._DENSITY_SPACING_MAX * math.exp(density * cls._DENSITY_LOG_RATIO)

    @classmethod
    def _density_from_spacing(cls, value: float) -> float:

        spacing = max(cls._DENSITY_SPACING_MIN, min(cls._DENSITY_SPACING_MAX, float(value)))

        # Inverse of the log mapping above.
        return math.log(spacing / cls._DENSITY_SPACING_MAX) / cls._DENSITY_LOG_RATIO

    def _active_color_rgb(self) -> tuple[int, int, int]:

        if self.host_api and hasattr(self.host_api, "get_active_color"):

            try:

                descriptor = self.host_api.get_active_color()  # type: ignore[attr-defined]  # host_api duck-typed

            except Exception:

                descriptor = None

            if descriptor is not None:

                rgba = getattr(descriptor, "rgba", None)

                if rgba:

                    r = max(0, min(255, int(round(rgba[0] * 255))))

                    g = max(0, min(255, int(round(rgba[1] * 255))))

                    b = max(0, min(255, int(round(rgba[2] * 255))))

                    if hasattr(self.settings.color, "setRgb"):

                        try:

                            self.settings.color.setRgb(r, g, b, 255)

                        except Exception:

                            logger.warning(
                                "basic_pen: QColor.setRgb failed on active color push",
                                exc_info=True,
                            )

                    return (r, g, b)

        if hasattr(self.settings.color, "getRgb"):

            try:

                r, g, b, *_ = self.settings.color.getRgb()

                return (int(r), int(g), int(b))

            except Exception:

                logger.warning(
                    "basic_pen: QColor.getRgb failed; falling back to tuple inspection",
                    exc_info=True,
                )

        color_value = getattr(self.settings, "color", None)

        if isinstance(color_value, tuple) and len(color_value) >= 3:

            return (int(color_value[0]), int(color_value[1]), int(color_value[2]))

        return (0, 0, 0)

    def descriptor(self) -> ToolDescriptor:
        """Get tool descriptor."""

        return ToolDescriptor(
            id="basic_pen",
            display_name=_ts("Basic Pen"),
            category="brush",
            cursor="crosshair",
            shortcut="B",
            shortcut_category=_ts("Tools"),
            shortcut_description=_ts("Activate Basic Pen"),
            secondary_shortcut="Shift+B",
            secondary_shortcut_description=_ts("Alternate brush binding"),
            order=1,
        )

    def tool_metadata(self) -> ToolMetadata:
        """Describe tool properties/presets for the host UI."""

        descriptor = self.descriptor()

        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category=descriptor.category or "brush",
            tags=["brush", "pen", "basic", "sketch"],
            icon_svg=descriptor.icon_svg,
            group_id="brush.basic",
            group_label=_ts("Brushes"),
            group_order=10,
            properties=[
                PropertySpec(
                    key="size",
                    display_name=_ts("Size"),
                    type=PropertyType.FLOAT,
                    min_value=1.0,
                    max_value=128.0,
                    default_value=self.settings.size,
                    step=1.0,
                    is_common=True,
                ),
                PropertySpec(
                    key="opacity",
                    display_name=_ts("Opacity"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=self.settings.opacity,
                    step=0.01,
                    is_common=True,
                ),
                PropertySpec(
                    key="hardness",
                    display_name=_ts("Hardness"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=self.settings.hardness,
                    step=0.01,
                    is_common=True,
                ),
                PropertySpec(
                    key="stamp_density",
                    display_name=_ts("Stamp Density"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=self._density_from_spacing(self.settings.spacing),
                    step=0.01,
                    is_common=True,
                ),
                PropertySpec(
                    key="pressure_sensitive",
                    display_name=_ts("Pressure Sensitive"),
                    type=PropertyType.BOOL,
                    default_value=True,
                    is_common=True,
                ),
                *PressureResponseProperties.build(self._pressure_response),
                *PenDynamicsProperties.build(self._dynamics),
                PropertySpec(
                    key="flow",
                    display_name=_ts("Flow"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=self.settings.flow,
                    step=0.05,
                ),
                PropertySpec(
                    key="tone_range",
                    display_name=_ts("Tone Range"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=self.settings.tone_range,
                    step=0.05,
                ),
                PropertySpec(
                    key="tone_gamma",
                    display_name=_ts("Tone Gamma"),
                    type=PropertyType.FLOAT,
                    min_value=0.1,
                    max_value=2.0,
                    default_value=self.settings.tone_gamma,
                    step=0.05,
                ),
                PropertySpec(
                    key="smoothing",
                    display_name=_ts("Stabilization"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=self.settings.smoothing,
                    step=0.05,
                ),
                PropertySpec(
                    key="tilt_angle",
                    display_name=_ts("Tilt Shading"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.tilt_angle,
                ),
                PropertySpec(
                    key="recontour",
                    display_name=_ts("Recontour"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.recontour,
                ),
                PropertySpec(
                    key="taper_start_length",
                    display_name=_ts("Taper Start Length"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=2000.0,
                    default_value=self.settings.taper_start_length,
                    step=1.0,
                ),
                PropertySpec(
                    key="taper_end_length",
                    display_name=_ts("Taper End Length"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=2000.0,
                    default_value=self.settings.taper_end_length,
                    step=1.0,
                ),
                PropertySpec(
                    key="recontour_smoothness",
                    display_name=_ts("RecontourSmoothness (next stroke)"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=100.0,
                    default_value=self.settings.recontour_smoothness,
                    step=1.0,
                ),
            ],
            presets=[
                ToolPreset(
                    name=_ts("Sketch"),
                    values={
                        "size": 6.0,
                        "opacity": 0.7,
                        "hardness": 0.45,
                        "spacing": 0.2,
                        "flow": 0.8,
                        "smoothing": 0.1,
                        "pressure_sensitive": True,
                    },
                ),
                ToolPreset(
                    name=_ts("Inking"),
                    values={
                        "size": 12.0,
                        "opacity": 1.0,
                        "hardness": 0.9,
                        "spacing": 0.05,
                        "flow": 1.0,
                        "smoothing": 0.35,
                        "pressure_sensitive": True,
                        "tilt_angle": False,
                    },
                ),
                ToolPreset(
                    name=_ts("Marker"),
                    values={
                        "size": 20.0,
                        "opacity": 0.8,
                        "hardness": 0.25,
                        "spacing": 0.12,
                        "flow": 0.6,
                        "smoothing": 0.05,
                        "pressure_sensitive": False,
                        "tilt_angle": True,
                    },
                ),
            ],
        )

    def context_toolbar_spec(self) -> ContextToolbarSpec | None:
        return ContextToolbarSpec(
            show_trigger="tool_active",
            property_keys=["size", "opacity", "hardness"],
            placement="top_center",
            mode="settings",
        )

    def activate(self, host_api) -> None:
        """Activate tool with host API.

        Note: saved tool settings are loaded by BasicPenPlugin.activate()
        AFTER _apply_initial_swatch(), because the swatch apply overrides
        size/opacity/etc. with its own params. User preferences must be
        re-applied on top of the swatch state.
        """

        self.host_api = host_api

    def enable(self) -> None:
        """Enable the tool (make it active)."""

        logger.info("Basic pen tool enabled")
        self._recontour.bind()

    def disable(self) -> None:
        """Disable the tool."""

        logger.info("Basic pen tool disabled")
        self._recontour.finish()
        self._recontour.unbind()
        if self._taper is not None:
            self._taper.close()
            self._taper = None

        # End any pending transaction immediately so that a deferred
        # QTimer callback does not accidentally close a *different*
        # tool's transaction after this tool has been switched out.
        if self._transaction_active:
            canvas = getattr(self.host_api, "canvas", None)
            if canvas is not None and hasattr(canvas, "end_transaction"):
                try:
                    canvas.end_transaction()
                except Exception:
                    logger.warning(
                        "basic_pen: canvas.end_transaction failed during tool disable; "
                        "undo boundary for the last stroke may be corrupted",
                        exc_info=True,
                    )
            self._transaction_active = False

        # Cancel any ongoing stroke

        if self.is_stroking:

            self.is_stroking = False

            self.stroke_points.clear()

    def deactivate(self) -> None:
        """Deactivate the tool."""

        # Persist current tool settings BEFORE disable() / host_api teardown.
        # Mirrors the eraser tool's deactivate flow.
        settings_mgr = getattr(self.host_api, "settings", None) if self.host_api else None
        setter = getattr(settings_mgr, "set", None) if settings_mgr else None
        if callable(setter):
            try:
                setter("tools.basic_pen", self._save_settings())
            except Exception:
                logger.warning(
                    "basic_pen: settings save failed on deactivate; tool state may "
                    "not persist to the next session",
                    exc_info=True,
                )

        # Close any open transaction and cancel in-flight stroke BEFORE
        # dropping host_api. Otherwise _schedule_end_transaction's deferred
        # closure observes host_api=None and leaks the transaction on the
        # canvas side.
        self.disable()

        self.host_api = None

        logger.info("Basic pen tool deactivated")

    def get_settings(self) -> dict[str, Any]:
        """Get current tool settings."""

        return {
            "size": self.settings.size,
            "opacity": self.settings.opacity,
            "hardness": self.settings.hardness,
            "spacing": self.settings.spacing,
            "stamp_density": self._density_from_spacing(self.settings.spacing),
            "color": self.settings.color.name(),
            "pressure_sensitive": self.settings.pressure_sensitive,
            "tilt_angle": self.settings.tilt_angle,
            "smoothing": self.settings.smoothing,
            "recontour": self.settings.recontour,
            "recontour_smoothness": self.settings.recontour_smoothness,
            "flow": self.settings.flow,
            "tone_range": self.settings.tone_range,
            "tone_gamma": self.settings.tone_gamma,
            "taper_start_length": self.settings.taper_start_length,
            "taper_end_length": self.settings.taper_end_length,
            **self._pressure_response.values(),
            **self._dynamics.values(),
        }

    def set_settings(self, settings: dict[str, Any]) -> None:  # noqa: PLR0912
        """Update tool settings."""
        self._pressure_response.update(settings)
        self._dynamics.update(settings)

        if "size" in settings:

            self.settings.size = max(0.1, min(1000.0, float(settings["size"])))

        if "opacity" in settings:

            self.settings.opacity = max(0.0, min(1.0, float(settings["opacity"])))

        if "hardness" in settings:

            self.settings.hardness = max(0.0, min(1.0, float(settings["hardness"])))

        if "spacing" in settings:

            self.settings.spacing = max(
                self._DENSITY_SPACING_MIN,
                min(self._DENSITY_SPACING_MAX, float(settings["spacing"])),
            )

        if "stamp_density" in settings:

            self.settings.spacing = self._spacing_from_density(settings["stamp_density"])

        if "color" in settings:

            color_value = settings["color"]

            if isinstance(color_value, str):

                self.settings.color = QColor(color_value)

            elif isinstance(color_value, (list, tuple)) and len(color_value) >= 3:

                self.settings.color = QColor(*color_value[:4])  # Support RGBA

        if "pressure_sensitive" in settings:

            self.settings.pressure_sensitive = bool(settings["pressure_sensitive"])

        if "tilt_angle" in settings:

            self.settings.tilt_angle = bool(settings["tilt_angle"])

        if "smoothing" in settings:

            self.settings.smoothing = max(0.0, min(1.0, float(settings["smoothing"])))

        if "recontour" in settings:
            self.settings.recontour = bool(settings["recontour"])
            if not self.settings.recontour and not self.is_stroking:
                self._recontour.finish()
        if "recontour_smoothness" in settings:
            value = float(settings["recontour_smoothness"])
            if math.isfinite(value):
                self.settings.recontour_smoothness = max(0.0, min(100.0, value))

        if "flow" in settings:

            self.settings.flow = max(0.0, min(1.0, float(settings["flow"])))

        if "tone_range" in settings:

            self.settings.tone_range = max(0.0, min(1.0, float(settings["tone_range"])))

        if "tone_gamma" in settings:

            self.settings.tone_gamma = max(0.1, min(2.0, float(settings["tone_gamma"])))

        for key in ("taper_start_length", "taper_end_length"):
            if key in settings:
                value = float(settings[key])
                if math.isfinite(value):
                    setattr(self.settings, key, max(0.0, min(2000.0, value)))

        # Emit settings changed signal - disabled (no QObject inheritance)

        # self.settings_changed.emit(self.get_settings())

        logger.debug(f"Pen settings updated: {settings}")

    def get_params(self) -> dict[str, Any]:

        return {
            **self._pressure_response.values(),
            **self._dynamics.values(),
            "size": self.settings.size,
            "opacity": self.settings.opacity,
            "flow": self.settings.flow,
            "hardness": self.settings.hardness,
            "spacing": self.settings.spacing,
            "tone_range": self.settings.tone_range,
            "tone_gamma": self.settings.tone_gamma,
            "taper_start_length": self.settings.taper_start_length,
            "taper_end_length": self.settings.taper_end_length,
        }

    def apply_params(self, params: dict[str, Any]) -> None:

        self.set_settings(params)

    def set_tip_path(self, abs_path: str | None) -> None:

        self._tip_path = abs_path

    def get_tip_path(self) -> str | None:

        return self._tip_path

    def set_hardness_mode(self, mode: str) -> None:

        self._hardness_mode = mode

    def set_setting(self, key: str, value: Any) -> None:
        """Update a single tool setting."""

        self.set_settings({key: value})

        if key == "stamp_density":

            self._sync_setting("spacing", self.settings.spacing)

        elif key == "spacing":

            self._sync_setting("stamp_density", self._density_from_spacing(self.settings.spacing))

    def _sync_setting(self, key: str, value: Any) -> None:

        host = self.host_api

        if host is None:

            return

        tool_id = self.descriptor().id

        tool_manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)

        sync_tool_setting = (
            getattr(tool_manager, "sync_tool_setting", None) if tool_manager else None
        )

        if callable(sync_tool_setting):

            try:

                sync_tool_setting(tool_id, key, value)

            except Exception:

                logger.warning(
                    "basic_pen: tool_manager.sync_tool_setting failed; UI slider "
                    "may not reflect the new value (falling back to events.publish)",
                    exc_info=True,
                )

            return

        events = getattr(host, "events", None)

        publish = getattr(events, "publish", None) if events else None

        if callable(publish):

            publish(
                "tool.settings_changed",
                {"tool_id": tool_id, "key": key, "value": value, "sync_only": True},
            )

    def on_press(self, point: HostStrokePoint) -> bool:
        """Handle pointer press event (legacy path)."""

        self.on_pointer_down(point)

        return True

    def on_move(self, point: HostStrokePoint) -> bool:
        """Handle pointer move event (legacy path)."""

        self.on_pointer_move([point])

        return True

    def on_release(self, point: HostStrokePoint) -> bool:
        """Handle pointer release event (legacy path)."""

        self.on_pointer_up(point)

        return True

    def on_key_press(self, key: int, modifiers: int) -> bool:
        """Handle key press events."""

        # Handle tool-specific shortcuts

        from PySide6.QtCore import Qt

        if key == Qt.Key.Key_Alt:

            # Alt key for color picking mode

            return True

        elif key == Qt.Key.Key_Shift:

            # Shift key for straight line mode

            return True

        return False

    def on_key_release(self, key: int, modifiers: int) -> bool:
        """Handle key release events."""

        return False

    def on_key(self, key: str, pressed: bool) -> bool:
        """Handle tool key events from the input dispatch."""

        return self._recontour.key(key, pressed)

    def paint_preview(self, painter: QPainter, rect) -> None:
        """Paint tool preview/cursor."""

        if self.last_point is None:

            return

        # Draw brush cursor

        painter.save()

        # Set up pen for cursor

        pen = QPen(QColor(255, 255, 255, 128))  # Semi-transparent white

        pen.setWidth(1)

        painter.setPen(pen)

        # Draw circle representing brush size

        radius = self.settings.size / 2

        # drawEllipse needs center point and radius

        painter.drawEllipse(self.last_point, radius, radius)

        # Draw crosshair if small brush

        if radius < 10:

            painter.drawLine(
                self.last_point.x() - 5,
                self.last_point.y(),
                self.last_point.x() + 5,
                self.last_point.y(),
            )

            painter.drawLine(
                self.last_point.x(),
                self.last_point.y() - 5,
                self.last_point.x(),
                self.last_point.y() + 5,
            )

        painter.restore()

    def _calculate_distance(self, p1: QPointF, p2: QPointF) -> float:
        """Calculate distance between two points."""

        return math.hypot(p2.x() - p1.x(), p2.y() - p1.y())

    def _ema_alpha(self) -> float:

        smoothing = max(0.0, min(1.0, float(self.settings.smoothing)))

        return max(0.1, 1.0 - 0.9 * smoothing)

    def _apply_ema_smoothing(self, raw: QPointF) -> QPointF:

        if self._smoothed_pos is None:

            self._smoothed_pos = QPointF(raw.x(), raw.y())

            return QPointF(raw.x(), raw.y())

        alpha = self._ema_alpha()

        smoothed = QPointF(
            self._smoothed_pos.x() + (raw.x() - self._smoothed_pos.x()) * alpha,
            self._smoothed_pos.y() + (raw.y() - self._smoothed_pos.y()) * alpha,
        )

        self._smoothed_pos = smoothed

        return smoothed

    def _point_with_pos(self, point: HostStrokePoint, pos: QPointF) -> HostStrokePoint:

        return HostStrokePoint(
            pos=(pos.x(), pos.y()),
            pressure=point.pressure,
            tilt=point.tilt,
            time=point.time,
        )

    def _begin_transaction(self) -> None:

        if self._transaction_active or not self.host_api:

            return

        canvas = getattr(self.host_api, "canvas", None)

        if canvas is None or not hasattr(canvas, "begin_transaction"):

            return

        try:

            canvas.begin_transaction("Pen Stroke")

        except Exception:

            logger.warning(
                "basic_pen: canvas.begin_transaction failed; this stroke will not be "
                "undo-grouped and may require multiple Ctrl+Z to remove",
                exc_info=True,
            )

            return

        self._transaction_active = True

    def _schedule_end_transaction(self) -> None:

        if not self._transaction_active:

            return

        canvas = getattr(self.host_api, "canvas", None)

        if canvas is None or not hasattr(canvas, "end_transaction"):

            self._transaction_active = False

            return

        try:

            from PySide6.QtCore import QTimer

        except Exception:

            try:

                canvas.end_transaction()

            except Exception:

                logger.warning(
                    "basic_pen: canvas.end_transaction failed in qtimer fallback; "
                    "next stroke may merge with the current one in undo history",
                    exc_info=True,
                )

            self._transaction_active = False

            return

        # Capture the stroke ID at schedule time so the callback can detect
        # whether a new stroke started before the timer fires.  If stroke_id
        # has advanced, _transaction_active was reset and re-set by the new
        # stroke's _begin_transaction — calling end_transaction here would
        # incorrectly terminate stroke N+1's transaction.
        scheduled_stroke_id = self._stroke_id
        # Also capture canvas identity: after deactivate / document switch
        # the host_api may hand out a different canvas object. Firing
        # end_transaction on a stale canvas could corrupt unrelated state.
        scheduled_canvas_id = id(canvas)

        def _end() -> None:
            if not self._transaction_active:
                return
            if self._stroke_id != scheduled_stroke_id:
                # A new stroke began before this timer fired; skip.
                return
            current_canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
            if current_canvas is None or id(current_canvas) != scheduled_canvas_id:
                # Canvas was replaced (document switch / deactivate); do not
                # touch the stale canvas, just drop the transaction flag.
                self._transaction_active = False
                return
            try:

                canvas.end_transaction()

            except Exception:

                logger.warning(
                    "basic_pen: canvas.end_transaction failed on scheduled close; "
                    "next stroke may merge with the current one in undo history",
                    exc_info=True,
                )

            self._transaction_active = False

        QTimer.singleShot(0, _end)

    def _current_layer_id(self) -> str | None:

        if not self.host_api:

            return None

        canvas = getattr(self.host_api, "canvas", None)

        if canvas is None or not hasattr(canvas, "current_layer"):

            return None

        try:

            layer = canvas.current_layer()

        except Exception:

            return None

        return getattr(layer, "id", None)

    def _merge_key(self) -> str:

        return f"basic_pen_stroke_{self._session_uuid}_{self._stroke_id}"

    def _command_tilt(self, point: HostStrokePoint) -> tuple[float, float]:

        if not self.settings.tilt_angle:

            return (0.0, 0.0)

        return self._clamp_tilt(point.tilt) or (0.0, 0.0)

    def _build_paint_command(self, points: list[HostStrokePoint]) -> Any | None:

        if not points:

            return None

        cmd_points = []

        for pt in points:

            tilt_x, tilt_y = self._command_tilt(pt)
            size_pressure, opacity_scale, tone_pressure = self._pressure_response.response(
                pt.pressure, enabled=self.settings.pressure_sensitive
            )

            cmd_points.append(
                PressureStrokePoint(
                    x=pt.pos[0],
                    y=pt.pos[1],
                    pressure=size_pressure,
                    opacity_scale=opacity_scale,
                    tone_pressure=tone_pressure,
                    tilt_x=tilt_x,
                    tilt_y=tilt_y,
                    rotation=0.0,
                    timestamp=pt.time if pt.time else 0.0,
                )
            )

        brush = BrushSettings(
            size=float(self.settings.size),
            opacity=float(self.settings.opacity),
            hardness=float(self.settings.hardness),
            spacing=float(self.settings.spacing),
            flow=float(self.settings.flow),
            tone_range=float(self.settings.tone_range),
            tone_gamma=float(self.settings.tone_gamma),
            color=self._active_color_rgb(),
            anti_alias=True,
        )

        if self._dynamics.enabled:
            from dataclasses import asdict  # noqa: PLC0415

            cmd_points = DabVariation.apply(
                cmd_points,
                self._dynamics.values(),
                size=brush.size,
                color=brush.color[:3],
                stroke_id=self._stroke_id,
            )
            values = asdict(brush)
            values["size"] = brush.size * (1 + self._dynamics.size_variation) * 1.5
            dynamic_brush = DynamicBrushSettings(
                **values,
                dab_size=brush.size,
                hardness_mode=self._hardness_mode,
                round_tip=not self._tip_path,
                texture_strength=self._dynamics.texture_strength,
                texture_scale=self._dynamics.texture_scale,
                variation_seed=self._dynamics.variation_seed,
                color_mixing=self._dynamics.color_mixing,
            )
            return StampBrushCommand(
                cmd_points,
                dynamic_brush,
                tip_path=self._tip_path or "",
                layer_id=self._current_layer_id(),
                merge_key=self._merge_key(),
                hardness_mode=self._hardness_mode,
            )

        if self._tip_path:

            return StampBrushCommand(
                points=cmd_points,
                brush=_PenBrushSettings.from_brush(brush, self._hardness_mode),
                tip_path=self._tip_path,
                hardness_mode=self._hardness_mode,
                layer_id=self._current_layer_id(),
                merge_key=self._merge_key(),
            )

        return PaintCommand(
            points=cmd_points,
            brush=brush,
            layer_id=self._current_layer_id(),
            merge_key=self._merge_key(),
        )

    def _queue_segment_commands(self, segment: list[HostStrokePoint], *, max_points: int) -> None:

        if not segment:

            return

        if len(segment) <= max_points:

            self._pending_commands.extend(
                self._wrap_stroke_commands(
                    self._assist.commands(segment, self._build_paint_command)
                )
            )

            return

        step = max(1, max_points - 1)

        start = 0

        total = len(segment)

        while start < total - 1:

            end = min(start + max_points, total)

            chunk = segment[start:end]

            self._pending_commands.extend(
                self._wrap_stroke_commands(self._assist.commands(chunk, self._build_paint_command))
            )

            if end >= total:

                break

            start = end - 1

    def _wrap_stroke_commands(self, commands: list[Any]) -> list[Any]:
        if self._taper is None:
            return self._recontour.wrap(commands)
        return [
            PenTaperCommand(self._taper, stroke=cmd, lane=index, recontour=self._recontour.record)
            for index, cmd in enumerate(commands)
        ]

    def _report_taper_error(self, session: PenTaperSession) -> None:
        notify = getattr(getattr(self.host_api, "ui", None), "show_notification", None)
        if session.error and callable(notify):
            notify(_ts(session.error), level="warning")

    def _segment_point_limit(self) -> int:
        """Pick a segment size that balances stroke smoothness and responsiveness."""

        if not self._tip_path:

            return self._MAX_SEGMENT_POINTS

        # Thresholds expressed in spacing units (not density) so they stay
        # stable across density-mapping changes. The numeric values mirror the
        # pre-log-mapping density thresholds 0.85 and 0.6 under the original
        # linear 1-d formula (spacing ≈ 0.1585 and 0.406 respectively).
        spacing = float(self.settings.spacing)

        if spacing <= 0.16:

            return 128

        if spacing <= 0.40:

            return 96

        return 64

    def _emit_incremental_commands(self) -> None:

        if not self.stroke_points:

            return

        if self._last_submitted_index >= len(self.stroke_points):

            return

        start_index = max(self._last_submitted_index - 1, 0)

        segment = self.stroke_points[start_index:]

        self._queue_segment_commands(segment, max_points=self._segment_point_limit())

        self._last_submitted_index = len(self.stroke_points)

    def _add_stroke_point(self, point: HostStrokePoint) -> None:
        """Add a stroke point with proper processing."""

        # Normalize pressure so downstream rendering always receives a safe value.
        # When pressure sensitivity is disabled, pin to 1.0 regardless of input —
        # this is what the Marker preset and the "Pressure Sensitive" UI toggle
        # promise. Tilt_angle does the analogous thing in _command_tilt.

        if self.settings.pressure_sensitive:
            normalized_pressure = max(min(point.pressure, 1.0), 0.0)
        else:
            normalized_pressure = 1.0

        tilt = self._clamp_tilt(point.tilt)

        adjusted_point = HostStrokePoint(
            pos=point.pos,
            pressure=normalized_pressure,
            tilt=tilt,
            time=point.time,
        )

        self.stroke_points.append(adjusted_point)

        if self._perf_debug:

            count = len(self.stroke_points)

            self._perf_max_points = max(self._perf_max_points, count)

            self._perf_total_points += 1

        if self.host_api and hasattr(self.host_api, "apply_stroke_point"):

            self.host_api.apply_stroke_point(adjusted_point)

    def _add_interpolated_points(
        self, start: QPointF, end: QPointF, point: HostStrokePoint
    ) -> None:

        sx = float(start.x())
        sy = float(start.y())
        dx = float(end.x()) - sx
        dy = float(end.y()) - sy

        distance = math.hypot(dx, dy)

        if distance <= 0:

            return

        step = max(1.0, float(self.settings.size) * float(self.settings.spacing))

        # Stamps land at d_i = i*step - accumulator along this segment.
        # Compute the stamp count N upfront and derive each t parametrically,
        # avoiding the iterative float drift and per-loop sqrt of the
        # previous mutating-sx/sy implementation.
        stamps = int((distance + self.distance_accumulator) / step)

        for i in range(1, stamps + 1):

            d_i = i * step - self.distance_accumulator

            t = d_i / distance

            interp_point = HostStrokePoint(
                pos=(sx + dx * t, sy + dy * t),
                pressure=point.pressure,
                tilt=point.tilt,
                time=point.time,
            )

            self._add_stroke_point(interp_point)

        self.distance_accumulator = (self.distance_accumulator + distance) - stamps * step

    def _finalize_stroke(self) -> None:
        """Finalize the current stroke."""

        if not self.stroke_points:

            return

        if self._last_submitted_index < len(self.stroke_points):

            start_index = max(self._last_submitted_index - 1, 0)

            segment = self.stroke_points[start_index:]

            self._queue_segment_commands(segment, max_points=self._segment_point_limit())

            self._last_submitted_index = len(self.stroke_points)

        self.stroke_points.clear()

        self._last_submitted_index = 0

    def _pick_color(self, pt: HostStrokePoint) -> None:
        """Pick color from canvas at point position."""

        if self.host_api and hasattr(self.host_api, "get_pixel_color"):

            color = self.host_api.get_pixel_color(int(pt.pos[0]), int(pt.pos[1]))

            if color:

                self.settings.color = color

                # self.settings_changed.emit(self.get_settings())  # disabled (no QObject)

                logger.debug(f"Color picked: {color.name()}")

    def _add_line_to(self, pt: HostStrokePoint) -> None:
        """Add a straight line to the point."""

        if self.last_point is None:

            return

        # Create line points

        start = self.last_point

        end = QPointF(pt.pos[0], pt.pos[1])

        # Add line points with consistent spacing

        distance = self._calculate_distance(start, end)

        spacing = self.settings.size * self.settings.spacing

        steps = max(1, int(distance / spacing))

        for i in range(steps + 1):

            t = i / steps if steps > 0 else 0

            x = start.x() + (end.x() - start.x()) * t

            y = start.y() + (end.y() - start.y()) * t

            line_point = HostStrokePoint(
                pos=(x, y), pressure=pt.pressure, tilt=pt.tilt, time=pt.time
            )

            self._add_stroke_point(line_point)

    def _handle_paint_command(self, args: dict[str, Any]) -> Any:
        """Handle paint command."""

        return {"status": "painting", "tool": "basic_pen"}

    def _handle_pick_color_command(self, args: dict[str, Any]) -> Any:
        """Handle pick color command."""

        if "point" in args:

            point_data = args["point"]

            point = HostStrokePoint(
                pos=(point_data.get("x", 0), point_data.get("y", 0)), pressure=1.0
            )

            self._pick_color(point)

            return {"status": "color_picked", "color": self.settings.color.name()}

        return {"status": "error", "message": "No point provided"}

    def _handle_draw_line_command(self, args: dict[str, Any]) -> Any:
        """Handle draw line command."""

        if "point" in args:

            point_data = args["point"]

            point = HostStrokePoint(
                pos=(point_data.get("x", 0), point_data.get("y", 0)),
                pressure=point_data.get("pressure", 1.0),
            )

            self._add_line_to(point)

            return {"status": "line_drawn"}

        return {"status": "error", "message": "No point provided"}

    def _save_settings(self) -> dict[str, Any]:
        """Save settings to a round-trippable dictionary.

        Uses spacing (the internal representation) not stamp_density, because
        the two are inverses and storing both would be redundant. Omits
        computed/derived values. Must stay in sync with _load_settings.
        """

        return {
            **self._pressure_response.values(),
            **self._dynamics.values(),
            "recontour": self.settings.recontour,
            "recontour_smoothness": self.settings.recontour_smoothness,
            "size": self.settings.size,
            "opacity": self.settings.opacity,
            "hardness": self.settings.hardness,
            "spacing": self.settings.spacing,
            "color": self.settings.color.name(),
            "pressure_sensitive": self.settings.pressure_sensitive,
            "tilt_angle": self.settings.tilt_angle,
            "smoothing": self.settings.smoothing,
            "flow": self.settings.flow,
            "tone_range": self.settings.tone_range,
            "tone_gamma": self.settings.tone_gamma,
            "taper_start_length": self.settings.taper_start_length,
            "taper_end_length": self.settings.taper_end_length,
        }

    def _load_settings(self, settings: dict[str, Any]) -> None:
        """Load settings from dictionary."""

        self.set_settings(settings)

    def _handle_move_point(self, point: HostStrokePoint) -> None:

        current_pos = QPointF(point.pos[0], point.pos[1])

        if self.last_point is None:

            self.last_point = QPointF(current_pos.x(), current_pos.y())

            if self.settings.smoothing > 0:

                self._smoothed_pos = QPointF(current_pos.x(), current_pos.y())

            self._add_stroke_point(point)

            return

        if self.settings.smoothing > 0:

            prev_smoothed = self._smoothed_pos if self._smoothed_pos is not None else current_pos

            smoothed_pos = self._apply_ema_smoothing(current_pos)

            self._add_interpolated_points(prev_smoothed, smoothed_pos, point)

            self.painter_path.lineTo(smoothed_pos.x(), smoothed_pos.y())

            self.last_point = smoothed_pos

            return

        self._add_interpolated_points(self.last_point, current_pos, point)

        self.painter_path.lineTo(point.pos[0], point.pos[1])

        self.last_point = current_pos

    def on_pointer_down(
        self,
        pt: HostStrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        """Handle pointer down event."""

        del alt, shift, ctrl, meta

        if self._recontour.press(pt):
            return
        self._recontour.begin()
        if self._taper is not None:
            self._taper.close()
        self._taper = (
            PenTaperSession(self.settings.taper_start_length, self.settings.taper_end_length)
            if self.settings.taper_start_length > 0.0 or self.settings.taper_end_length > 0.0
            else None
        )

        self._refresh_perf_settings()

        self._reset_perf_counters()
        if self.is_stroking:

            if self._transaction_active and self.host_api:

                canvas = getattr(self.host_api, "canvas", None)

                if canvas is not None and hasattr(canvas, "end_transaction"):

                    try:

                        canvas.end_transaction()

                    except Exception:

                        logger.warning(
                            "basic_pen: canvas.end_transaction failed while "
                            "cancelling in-progress stroke on re-entry; undo "
                            "boundary for the interrupted stroke may be corrupted",
                            exc_info=True,
                        )

            self.stroke_points.clear()

            self._last_submitted_index = 0

            self._transaction_active = False

        # 前ストロークの遅延 end_transaction がまだ pending の場合、
        # QTimer コールバックを無効化する。canvas.begin_transaction() が
        # 前トランザクションを軽量フラッシュ (recompose / イベント発火なし)
        # するので、重い end_transaction がストローク中に割り込むのを防ぐ。
        self._transaction_active = False

        self.is_stroking = True

        self.stroke_points = []

        if self._pending_commands:
            # Caller contract is to call build_commands() at least once per
            # stroke. Discarding silently here masks caller bugs; log so the
            # violation is visible in diagnostics.
            logger.warning(
                "basic_pen: discarding %d un-drained commands at new stroke start; "
                "caller must invoke build_commands() between strokes",
                len(self._pending_commands),
            )

        self._pending_commands.clear()

        self._last_submitted_index = 0
        pt = self._assist.begin(self.host_api, pt)

        self.distance_accumulator = 0.0

        self._stroke_id += 1

        start_pos = QPointF(pt.pos[0], pt.pos[1])

        self.last_point = QPointF(start_pos.x(), start_pos.y())

        if self.settings.smoothing > 0:

            self._smoothed_pos = QPointF(start_pos.x(), start_pos.y())

        else:

            self._smoothed_pos = None

        self.painter_path = QPainterPath()

        self.painter_path.moveTo(start_pos.x(), start_pos.y())

        # Begin transaction BEFORE the first point so that
        # host_api.apply_stroke_point (called inside _add_stroke_point) runs
        # inside the transaction and is undo-grouped with the rest of the stroke.
        self._begin_transaction()

        self._add_stroke_point(pt)

        # Emit the initial down point immediately so the first dot appears
        # without waiting for the distance accumulator in on_pointer_move.
        self._emit_incremental_commands()

    def on_pointer_move(self, points: list[HostStrokePoint]) -> None:
        """Handle pointer move events."""

        if self._recontour.move(points):
            return
        if not self.is_stroking:

            return
        points = self._assist.points(points)

        before_count = len(self.stroke_points)

        for pt in points:

            self._handle_move_point(pt)

        if len(self.stroke_points) > before_count:

            self._emit_incremental_commands()

        if self._perf_debug:

            self._perf_move_count += 1

            if self._perf_move_count % self._perf_sample_every == 0:

                logger.info(
                    "Pen move sample: moves=%s points=%s pending=%s max_steps=%s",
                    self._perf_move_count,
                    len(self.stroke_points),
                    len(self._pending_commands),
                    self._perf_max_steps,
                )

    def on_pointer_up(self, pt: HostStrokePoint) -> None:  # noqa: PLR0912
        """Handle pointer up event."""

        if self._recontour.release(pt):
            return
        if not self.is_stroking:

            return
        pt = self._assist.point(pt)

        current_pos = QPointF(pt.pos[0], pt.pos[1])

        if self.settings.smoothing > 0:

            if self._smoothed_pos is None:

                self._smoothed_pos = QPointF(current_pos.x(), current_pos.y())

            else:

                smoothed_pos = self._apply_ema_smoothing(current_pos)

                start_pos = self.last_point if self.last_point is not None else smoothed_pos

                self._add_interpolated_points(start_pos, smoothed_pos, pt)

                self.last_point = smoothed_pos

            # Snap-interpolate from the final smoothed position to the actual
            # release position. With high smoothing, smoothed_pos can lag
            # significantly behind current_pos; without this fill the stroke
            # ends with a visible gap between the smoothed tail and the
            # release point. No-op when the two positions are already close
            # (distance-based step in _add_interpolated_points early-returns).
            if self.last_point is not None:

                self._add_interpolated_points(self.last_point, current_pos, pt)

            if not self.stroke_points or not self._positions_near(
                self.stroke_points[-1].pos, pt.pos
            ):

                self._add_stroke_point(self._point_with_pos(pt, current_pos))

            if self.last_point is not None:

                self.painter_path.lineTo(current_pos.x(), current_pos.y())

            self.last_point = current_pos

        else:

            if self.last_point is not None:

                self._add_interpolated_points(self.last_point, current_pos, pt)

            if not self.stroke_points or not self._positions_near(
                self.stroke_points[-1].pos, pt.pos
            ):

                self._add_stroke_point(pt)

            if self.last_point is not None:

                self.painter_path.lineTo(pt.pos[0], pt.pos[1])

        self._finalize_stroke()
        if self._taper is not None:
            taper_session = self._taper
            self._pending_commands.append(
                PenTaperCommand(taper_session, recontour=self._recontour.record)
            )
            QTimer.singleShot(0, lambda: self._report_taper_error(taper_session))
            self._taper = None
        self._recontour.seal()

        self.is_stroking = False
        self._assist.end()

        self.last_point = None

        self._smoothed_pos = None

        self.distance_accumulator = 0.0

        self._schedule_end_transaction()

        if self._perf_debug:

            elapsed = time.monotonic() - self._perf_stroke_start

            logger.info(
                "Pen stroke summary: moves=%s total_points=%s max_points=%s max_steps=%s duration=%.3fs",
                self._perf_move_count,
                self._perf_total_points,
                self._perf_max_points,
                self._perf_max_steps,
                elapsed,
            )

    def _reset_perf_counters(self) -> None:

        self._perf_move_count = 0

        self._perf_max_steps = 0

        self._perf_max_points = 0

        self._perf_total_points = 0

        self._perf_last_warn = 0.0

        self._perf_stroke_start = time.monotonic()

    def _refresh_perf_settings(self) -> None:

        enabled = False

        sample_every = 60

        warn_steps = 64

        settings = getattr(self.host_api, "settings", None) if self.host_api else None

        getter = getattr(settings, "get", None) if settings else None

        if callable(getter):

            try:

                enabled = bool(getter("debug/pen_perf", False))

                sample_every = int(
                    getter("debug/pen_perf_sample_every", sample_every) or sample_every
                )

                warn_steps = int(getter("debug/pen_perf_warn_steps", warn_steps) or warn_steps)

            except Exception:

                enabled = False

        self._perf_debug = enabled

        self._perf_sample_every = max(1, sample_every)

        self._perf_warn_steps = max(4, warn_steps)

    def build_commands(self) -> list[Any]:
        """Return and clear any pending commands generated by the tool."""

        out = list(self._pending_commands)

        self._pending_commands.clear()

        return out


from ...plugins.sdk.base import PluginMetadata, PluginType
from .pen_dock_layout_watcher import _DockLayoutWatcher as _DockLayoutWatcher  # noqa: PLC0414

# Plugin metadata


PLUGIN_METADATA = PluginMetadata(
    id="basic_pen",
    name=_ts("Basic Pen Tool"),
    version="1.0.0",
    description=_ts("Basic pen tool with pressure sensitivity"),
    author="LoomStroke Team",
    types=[PluginType.TOOL],
)


# Plugin entry point


def create_plugin() -> BasicPenPlugin:
    """Create and return the BasicPenPlugin wrapper."""

    return BasicPenPlugin()


from .pen_plugin import BasicPenPlugin as BasicPenPlugin  # noqa: E402, PLC0414
