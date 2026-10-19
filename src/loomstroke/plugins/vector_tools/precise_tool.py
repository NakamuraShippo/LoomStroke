from __future__ import annotations

import contextlib
import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor

from ...core.color_state import ColorDescriptor
from ...core.native_cmyk.vector_paints import capture_paint, capture_vector_paints
from ...host_api import StrokePoint as HostStrokePoint
from ..common.vector_hit_test_geometry import VectorHitTestGeometry
from ..sdk import PropertySpec, PropertyType, ToolDescriptor, ToolMetadata
from ..sdk.base import PluginMetadata, PluginType
from ..sdk.cursors import apply_hover_cursor
from ..sdk.tool import ContextToolbarSpec
from .common import (
    VECTOR_IDS_META_KEY,
    VECTOR_STROKE_PROFILE_META_KEY,
    Anchor,
    VectorCreateCommand,
    VectorPath,
    VectorReplaceCommand,
    VectorStyle,
    copy_vector_object,
    ensure_vector_element_ids,
    make_vector_object,
    normalize_rgb,
)
from .cubic_hit_test import CubicHitTest
from .parametric_shapes import is_editable_parametric_shape

logger = logging.getLogger(__name__)

_MAX_STROKE_PROFILE_SAMPLES = 256
_MAX_COMMIT_STROKE_SAMPLES = 4096
_MAX_PREVIEW_STROKE_SAMPLES = 512
_PROFILE_EPSILON = 1e-4
_TILT_LIMIT_DEGREES = 90.0


def _point_near_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float, threshold: float
) -> bool:
    """Return True if point (px, py) is within *threshold* of segment AB."""
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-6:
        return False
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2 <= threshold * threshold


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("VectorPreciseTool", text, use_fallback=True)


@dataclass
class PreciseSettings:
    stroke_width: float = 2.0
    stroke_color: tuple[int, int, int] = (0, 0, 0)
    fill_enabled: bool = False
    fill_color: tuple[int, int, int] | None = None
    auto_close: bool = True
    edit_mode: str = "anchor"
    anchor_density: float = 1.0  # 0.1-1.0, lower = fewer anchors
    handle_smoothness: float = 0.0  # 0.0-5.0, higher = fewer anchors (curve fitting)


class VectorPreciseTool:
    def __init__(self) -> None:
        self.host_api = None
        self.settings = PreciseSettings()
        self._points: list[HostStrokePoint] = []
        self._pending_objects: list = []
        self._is_drawing = False
        self._pending_commands: list[VectorCreateCommand] = []
        self._selected: tuple[str, int, int, str | None] | None = (
            None  # (vec_id, path_idx, anchor_idx, layer_id)
        )
        self._hover_cursor_set: bool = False
        self._editing_object = None
        self._drag_anchor_last: HostStrokePoint | None = None
        self._handle_drag: str | None = None  # "in" | "out"
        self._target_layer_id: str | None = None
        self._created_layer_id: str | None = None
        self._straight_segment_modifier = False

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="vector_pen_precise",
            display_name=_ts("Vector Pen (Precise)"),
            category="vector",
            cursor="pen",
            shortcut="P",
            shortcut_category="Tools",
            shortcut_description=_ts("Precise vector pen"),
            order=30,
        )

    def tool_metadata(self) -> ToolMetadata:
        d = self.descriptor()
        return ToolMetadata(
            tool_id=d.id,
            display_name=d.display_name,
            category=d.category or "vector",
            group_id="vector.tools",
            group_label=_ts("Vector"),
            properties=self._property_specs(),
        )

    def context_toolbar_spec(self) -> ContextToolbarSpec | None:
        return ContextToolbarSpec(
            show_trigger="pointer_up",
            property_keys=["stroke_width", "stroke_color", "fill_enabled", "fill_color"],
            anchor_mode="stroke_bbox",
            mode="settings",
        )

    def activate(self, host) -> None:
        self.host_api = host

    def enable(self) -> None:
        self._reset()

    def disable(self) -> None:
        self._reset()
        if self._hover_cursor_set:
            self._hover_cursor_set = apply_hover_cursor(None, is_set=self._hover_cursor_set)

    def deactivate(self) -> None:
        self._reset()
        self.host_api = None

    def on_pointer_down(
        self,
        pt: HostStrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del meta

        # Check if clicking on an existing (confirmed) vector object
        # → switch to edit tool with auto-return
        if self._try_switch_to_edit_tool(pt):
            return

        # Clicking empty area: clear any lingering vector selection
        self._clear_vector_selection()
        self._straight_segment_modifier = bool(shift)

        # Start new stroke
        self._apply_active_color()
        self._points = [pt]
        self._is_drawing = True

    def on_pointer_move(self, pts: Iterable[HostStrokePoint]) -> None:
        if self._is_drawing:
            new_pts = list(pts)
            if not new_pts:
                return
            self._points.extend(new_pts)
            self._refresh_live_preview()
            return
        if self._selected and self._editing_object:
            last = None
            for point in pts:
                last = point
            if last is None:
                return
            self._drag_anchor_last = last
            self._move_selected_anchor(last, preview_only=True)
            return

    def on_pointer_up(self, pt: HostStrokePoint) -> None:
        if not self._is_drawing:
            if self._selected and self._editing_object:
                self._move_selected_anchor(pt, preview_only=False)
                self._commit_editing_object()
            return
        self._points.append(pt)
        obj = self._build_object()
        if obj:
            self._pending_objects.append(obj)
        self._emit_pending_immediate()
        self._reset()

    def on_pointer_hover(self, pt: HostStrokePoint) -> None:

        cursor = None
        if self._selected and self._editing_object:
            cursor = QCursor(Qt.CursorShape.SizeAllCursor)
        self._hover_cursor_set = apply_hover_cursor(cursor, is_set=self._hover_cursor_set)

    def on_key(self, key: str, pressed: bool) -> bool:
        if key.lower() == "shift":
            self._straight_segment_modifier = bool(pressed)
            if self._is_drawing:
                self._refresh_live_preview()
                return True
            return False
        if not pressed:
            return False
        if key.lower() == "esc":
            self._reset()
            return True
        return False

    def on_wheel(self, delta: float, pt: HostStrokePoint) -> bool:
        del delta, pt
        return False

    def build_commands(self):
        if not self._pending_objects and not self._pending_commands:
            return []

        target_layer_id = self._ensure_target_layer_id()
        created_layer_id = self._created_layer_id
        commands: list[VectorCreateCommand] = []
        if self._pending_objects:
            commands.extend(
                VectorCreateCommand(
                    obj,
                    layer_id=target_layer_id,
                    merge_key="vector_precise",
                    created_layer_id=created_layer_id,
                )
                for obj in self._pending_objects
            )
            self._pending_objects.clear()
            self._created_layer_id = None
        if self._pending_commands:
            commands.extend(self._pending_commands)
            self._pending_commands.clear()
        self._created_layer_id = None
        return commands

    def _refresh_live_preview(self) -> None:
        """Build a temporary vector object during drawing and show as preview."""
        if not self._is_drawing or not self.host_api or not getattr(self.host_api, "canvas", None):
            return
        if not self._has_visible_stroke_segment(self._points):
            return
        obj = self._build_object(preview=True)
        if not obj:
            return
        layer_hint = self._target_layer_id or self._resolve_existing_vector_layer_id()
        if layer_hint is None:
            layer_hint = self._create_vector_layer_id()
            self._target_layer_id = layer_hint
        try:
            self.host_api.canvas._vector_preview = obj  # type: ignore[attr-defined]  # duck-typed canvas attr
            self.host_api.canvas._vector_preview_layer_id = layer_hint  # type: ignore[attr-defined]  # duck-typed canvas attr
            if hasattr(self.host_api.canvas, "notify_vector_preview_changed"):
                self.host_api.canvas.notify_vector_preview_changed()
            elif hasattr(self.host_api.canvas, "_recompose"):
                self.host_api.canvas._recompose()
        except Exception:
            logger.debug("VectorPrecise preview update failed", exc_info=True)

    def get_settings(self) -> dict[str, object]:
        return dict(vars(self.settings))

    def set_settings(self, settings: PreciseSettings | dict[str, object]) -> None:
        if isinstance(settings, PreciseSettings):
            data = vars(settings)
        elif isinstance(settings, dict):
            data = settings
        else:
            data = {}
        merged = dict(vars(self.settings))
        merged.update(data)
        merged["anchor_density"] = max(0.1, min(1.0, float(merged.get("anchor_density", 1.0))))
        merged["handle_smoothness"] = max(
            0.0, min(5.0, float(merged.get("handle_smoothness", 0.0) or 0.0))
        )
        stroke = normalize_rgb(merged.get("stroke_color")) or self.settings.stroke_color
        merged["stroke_color"] = stroke
        merged["fill_color"] = normalize_rgb(merged.get("fill_color"))
        if bool(merged.get("fill_enabled")) and merged.get("fill_color") is None:
            merged["fill_color"] = stroke
        if merged.get("fill_color") is None and merged.get("fill_enabled"):
            merged["fill_enabled"] = False
        self.settings = PreciseSettings(**merged)
        self._sync_active_color_from_settings()
        if self._is_drawing:
            self._refresh_live_preview()

    def set_setting(self, key: str, value: object) -> None:  # noqa: PLR0912
        if not hasattr(self.settings, key):
            return
        if key == "stroke_color":
            normalized = normalize_rgb(value)
            if normalized is not None:
                self.settings.stroke_color = normalized
        elif key == "fill_color":
            self.settings.fill_color = normalize_rgb(value)
            if self.settings.fill_color is None:
                self.settings.fill_enabled = False
            else:
                self.settings.fill_enabled = True
        elif key == "fill_enabled":
            self.settings.fill_enabled = bool(value)
            if self.settings.fill_enabled and self.settings.fill_color is None:
                self.settings.fill_color = self.settings.stroke_color
        else:
            setattr(self.settings, key, value)

        if key == "anchor_density":
            self.settings.anchor_density = max(0.1, min(1.0, float(value or 1.0)))
        if key == "handle_smoothness":
            self.settings.handle_smoothness = max(0.0, min(5.0, float(value or 0.0)))

        if key in ("stroke_color", "fill_color"):
            self._sync_active_color_from_settings(preserve_native=False)
        if self._is_drawing:
            self._refresh_live_preview()

    def create_options_widget(self):
        return None

    def _reset(self) -> None:
        self._points.clear()
        self._is_drawing = False
        self._pending_commands.clear()
        self._editing_object = None
        self._selected = None
        self._drag_anchor_last = None
        self._handle_drag = None
        self._clear_canvas_preview()
        self._target_layer_id = None
        self._created_layer_id = None
        self._straight_segment_modifier = False
        if self.host_api and getattr(self.host_api, "canvas", None):
            try:
                self.host_api.canvas._vector_preview = None
                self.host_api.canvas._vector_preview_layer_id = None
                if hasattr(self.host_api.canvas, "notify_vector_preview_changed"):
                    self.host_api.canvas.notify_vector_preview_changed()
                elif hasattr(self.host_api.canvas, "_recompose"):
                    self.host_api.canvas._recompose()
            except Exception:
                logger.debug("vector preview clear failed", exc_info=True)

    def _resolve_existing_vector_layer_id(self) -> str | None:
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        current_layer = getattr(canvas, "current_layer", None)
        if callable(current_layer):
            try:
                ref = current_layer()
                vector_kind = str(getattr(ref, "vector_kind", "draw") or "draw").lower()
                name = str(getattr(ref, "name", "") or "").strip().lower()
                if (
                    ref is not None
                    and getattr(ref, "layer_type", None) == "vector"
                    and vector_kind == "draw"
                    and not name.startswith("text layer")
                ):
                    return getattr(ref, "id", None)
            except Exception:
                logger.debug("current_layer lookup failed", exc_info=True)
        return None

    def _build_object(self, *, preview: bool = False):
        if not self._points:
            return None
        samples = self._dedupe_stroke_samples(self._points_for_build(preview=preview))
        positions = [sample[0] for sample in samples]
        density = max(0.1, min(1.0, float(self.settings.anchor_density)))
        smoothness = max(0.0, float(self.settings.handle_smoothness))

        straight_positions = self._straight_segment_positions(positions)
        if straight_positions is not None:
            simplified = straight_positions
            closed = False
            anchors = [
                Anchor(pos=pos, kind="corner", h_in=(0.0, 0.0), h_out=(0.0, 0.0))
                for pos in simplified
            ]
        else:
            # 1. 密度による単純な間引き (ジッター除去)
            simplified = self._simplify_positions(positions, density)

            if not simplified:
                return None

            # 2. Smoothnessの設定に応じた処理の分岐
            closed = False
            if self.settings.auto_close and len(simplified) > 2:
                dx = simplified[0][0] - simplified[-1][0]
                dy = simplified[0][1] - simplified[-1][1]
                if math.hypot(dx, dy) < 6.0:
                    closed = True
                    simplified[-1] = simplified[0]  # 座標を完全に一致させる

            if smoothness > 0.1:
                # 2a. カーブフィッティング (アンカー削減 + ベジェ曲線化)
                anchors = self._fit_curve_to_points(simplified, smoothness, closed=closed)
            else:
                # 2b. 従来の方法 (全ての点をアンカーにして、ハンドルだけ調整)
                anchors = [
                    Anchor(pos=pos, kind="corner", h_in=(0.0, 0.0), h_out=(0.0, 0.0))
                    for pos in simplified
                ]
                anchors = self._apply_handle_smoothness(anchors, closed=closed)

        if not anchors:
            return None

        path = VectorPath(anchors=anchors, closed=closed)
        style = VectorStyle(
            stroke_color=self.settings.stroke_color,
            stroke_width=self.settings.stroke_width,
            fill_enabled=self.settings.fill_enabled and self.settings.fill_color is not None,
            fill_color=self.settings.fill_color,
        )
        meta = {
            "mode": "illustrator",
            "autoClose": self.settings.auto_close,
            "editMode": self.settings.edit_mode,
            "anchorDensity": density,
            "handleSmoothness": smoothness,
        }
        stroke_profile = self._build_stroke_profile(samples)
        if stroke_profile is not None:
            meta[VECTOR_STROKE_PROFILE_META_KEY] = stroke_profile
        meta = capture_vector_paints(meta, getattr(self, "_paint_color", None), style)
        obj = make_vector_object([path], style, meta)
        return ensure_vector_element_ids(obj)

    def _straight_segment_positions(
        self,
        positions: list[tuple[float, float]],
    ) -> list[tuple[float, float]] | None:
        if not self._straight_segment_modifier or len(positions) < 2:
            return None
        start = positions[0]
        end = positions[-1]
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return None
        snapped = round(math.atan2(dy, dx) / (math.pi / 4.0)) * (math.pi / 4.0)
        constrained = (
            start[0] + math.cos(snapped) * length,
            start[1] + math.sin(snapped) * length,
        )
        return [start, constrained]

    def _dedupe_stroke_samples(
        self, points: Iterable[HostStrokePoint]
    ) -> list[tuple[tuple[float, float], float, tuple[float, float] | None]]:
        samples: list[tuple[tuple[float, float], float, tuple[float, float] | None]] = []
        for pt in points:
            pos = (float(pt.pos[0]), float(pt.pos[1]))
            if samples and samples[-1][0] == pos:
                continue
            pressure = self._normalize_pressure(getattr(pt, "pressure", 1.0))
            tilt = self._normalize_tilt(getattr(pt, "tilt", None))
            samples.append((pos, pressure, tilt))
        return samples

    def _points_for_build(self, *, preview: bool) -> Iterable[HostStrokePoint]:
        limit = _MAX_PREVIEW_STROKE_SAMPLES if preview else _MAX_COMMIT_STROKE_SAMPLES
        return self._evenly_sample_points(self._points, limit)

    @staticmethod
    def _has_visible_stroke_segment(points: list[HostStrokePoint]) -> bool:
        first: tuple[float, float] | None = None
        for pt in points:
            pos = (float(pt.pos[0]), float(pt.pos[1]))
            if first is None:
                first = pos
            elif pos != first:
                return True
        return False

    @staticmethod
    def _evenly_sample_points(
        points: list[HostStrokePoint],
        limit: int,
    ) -> list[HostStrokePoint]:
        if limit <= 0 or len(points) <= limit:
            return points
        if limit == 1:
            return [points[-1]]
        last = len(points) - 1
        indices = sorted({round(i * last / (limit - 1)) for i in range(limit)})
        return [points[index] for index in indices]

    def _build_stroke_profile(
        self, samples: list[tuple[tuple[float, float], float, tuple[float, float] | None]]
    ) -> dict[str, object] | None:
        has_pressure = any(abs(pressure - 1.0) > _PROFILE_EPSILON for _, pressure, _ in samples)
        has_tilt = any(
            tilt is not None
            and (abs(tilt[0]) > _PROFILE_EPSILON or abs(tilt[1]) > _PROFILE_EPSILON)
            for _, _, tilt in samples
        )
        if not has_pressure and not has_tilt:
            return None

        indices = self._stroke_profile_indices(len(samples))
        t_values = self._stroke_profile_t_values(samples)
        payload: dict[str, object] = {
            "schema_version": 1,
            "source": "vector_pen_precise",
            "sample_count": len(samples),
            "base_stroke_width": self._profile_float(self.settings.stroke_width),
        }
        if has_pressure:
            pressure_profile: list[dict[str, float]] = []
            width_profile: list[dict[str, float]] = []
            for idx in indices:
                t_value = self._profile_float(t_values[idx])
                pressure = self._profile_float(samples[idx][1])
                pressure_profile.append({"t": t_value, "value": pressure})
                width_profile.append({"t": t_value, "scale": pressure})
            payload["pressure"] = pressure_profile
            payload["width_profile"] = width_profile
        if has_tilt:
            tilt_profile: list[dict[str, float]] = []
            for idx in indices:
                tilt = samples[idx][2] or (0.0, 0.0)
                tilt_profile.append(
                    {
                        "t": self._profile_float(t_values[idx]),
                        "x": self._profile_float(tilt[0]),
                        "y": self._profile_float(tilt[1]),
                    }
                )
            payload["tilt"] = tilt_profile
        return payload

    @staticmethod
    def _normalize_pressure(value: object) -> float:
        try:
            pressure = float(value)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(pressure):
            return 1.0
        return max(0.0, min(1.0, pressure))

    @staticmethod
    def _normalize_tilt(value: object) -> tuple[float, float] | None:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None
        try:
            tilt_x = float(value[0])
            tilt_y = float(value[1])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(tilt_x) or not math.isfinite(tilt_y):
            return None
        tilt_x = max(-_TILT_LIMIT_DEGREES, min(_TILT_LIMIT_DEGREES, tilt_x))
        tilt_y = max(-_TILT_LIMIT_DEGREES, min(_TILT_LIMIT_DEGREES, tilt_y))
        if abs(tilt_x) <= _PROFILE_EPSILON and abs(tilt_y) <= _PROFILE_EPSILON:
            return None
        return (tilt_x, tilt_y)

    @staticmethod
    def _stroke_profile_indices(count: int) -> list[int]:
        if count <= 0:
            return []
        if count <= _MAX_STROKE_PROFILE_SAMPLES:
            return list(range(count))
        last = count - 1
        return sorted(
            {
                round(i * last / (_MAX_STROKE_PROFILE_SAMPLES - 1))
                for i in range(_MAX_STROKE_PROFILE_SAMPLES)
            }
        )

    @staticmethod
    def _stroke_profile_t_values(
        samples: list[tuple[tuple[float, float], float, tuple[float, float] | None]],
    ) -> list[float]:
        if not samples:
            return []
        if len(samples) == 1:
            return [0.0]
        cumulative = [0.0]
        total = 0.0
        for idx in range(1, len(samples)):
            prev = samples[idx - 1][0]
            current = samples[idx][0]
            total += math.hypot(current[0] - prev[0], current[1] - prev[1])
            cumulative.append(total)
        if total <= 1e-6:
            scale = len(samples) - 1
            return [idx / scale for idx in range(len(samples))]
        return [value / total for value in cumulative]

    @staticmethod
    def _profile_float(value: float) -> float:
        return round(float(value), 6)

    # --- Curve Fitting / Math Helpers ---------------------------------

    def _fit_curve_to_points(
        self, points: list[tuple[float, float]], smoothness: float, closed: bool
    ) -> list[Anchor]:
        """
        Fit a series of Bezier curves to the points.
        Reduces the number of anchors based on `smoothness` (error tolerance).
        """
        if len(points) < 2:
            return [Anchor(pos=p, kind="corner") for p in points]

        # smoothness (0-5.0) を 許容誤差 (ピクセル単位の自乗誤差など) に変換
        # 例: 0.2 -> 1px, 1.0 -> 5px, 5.0 -> 100px 程度の許容度
        # 誤差許容値が大きいほど、より強い曲線近似が行われ、アンカーが減る
        error_threshold_sq = (smoothness * 4.0) ** 2

        # 1. 接線ベクトルの計算
        # 各点における接線を、隣接点から推定する
        tangents = self._compute_tangents(points, closed)

        # 2. 再帰的にカーブをフィットさせる
        # 始点と終点の接線方向を固定し、間の点を誤差内に収められるか試行する
        anchors = self._fit_recursive(points, tangents, 0, len(points) - 1, error_threshold_sq)

        # 閉じたパスの場合の端点処理
        if closed and len(anchors) > 1:
            # 始点と終点が同じ位置だが、ハンドルが独立している場合があるため、
            # 連続性を保つために平均化するなどの処理を入れることも可能だが、
            # ここでは単純に座標一致とKindの設定を行う
            anchors[-1] = Anchor(
                pos=anchors[0].pos,
                kind=anchors[0].kind,
                h_in=anchors[-1].h_in,
                h_out=anchors[
                    -1
                ].h_out,  # 通常終点のh_outは(0,0)だが、閉じるなら連続性を持たせる余地あり
            )
            # 始点のh_inを終点から持ってくる（完全な平滑化）  # noqa: RUF003
            anchors[0] = Anchor(
                pos=anchors[0].pos,
                kind=anchors[0].kind,
                h_in=anchors[-1].h_in,
                h_out=anchors[0].h_out,
            )

        return anchors

    def _fit_recursive(self, points, tangents, first, last, error_threshold_sq) -> list[Anchor]:
        """
        改良版: 常に [first ... last] の範囲のアンカーリストを返す。
        結合時に重複（split_idx）を除去する。
        """  # noqa: RUF002
        # ベースケース
        if last - first < 2:
            # 直線の始点と終点
            p_start = points[first]
            p_end = points[last]
            return [
                Anchor(pos=p_start, kind="corner", h_in=(0, 0), h_out=(0, 0)),
                Anchor(pos=p_end, kind="corner", h_in=(0, 0), h_out=(0, 0)),
            ]

        # フィット試行
        tan_start = tangents[first]
        tan_end = tangents[last]
        dist = math.hypot(points[last][0] - points[first][0], points[last][1] - points[first][1])
        handle_len = dist / 3.0

        h_out_start = (tan_start[0] * handle_len, tan_start[1] * handle_len)
        h_in_end = (-tan_end[0] * handle_len, -tan_end[1] * handle_len)

        p0 = points[first]
        p3 = points[last]
        p1 = (p0[0] + h_out_start[0], p0[1] + h_out_start[1])
        p2 = (p3[0] + h_in_end[0], p3[1] + h_in_end[1])

        max_err_sq = 0.0
        split_idx = -1
        count = last - first
        for i in range(1, count):
            idx = first + i
            t = i / count
            pt = points[idx]
            qt = self._eval_bezier(p0, p1, p2, p3, t)
            dx = qt[0] - pt[0]
            dy = qt[1] - pt[1]
            err_sq = dx * dx + dy * dy
            if err_sq > max_err_sq:
                max_err_sq = err_sq
                split_idx = idx

        if max_err_sq < error_threshold_sq:
            # フィット成功
            # 始点と終点のアンカーを返す
            # 始点のh_in、終点のh_outは、隣接セグメントとの結合時に調整されるべきだが
            # ここではこのセグメント内での形状を決定する
            start_anchor = Anchor(pos=p0, kind="smooth", h_in=(0, 0), h_out=h_out_start)
            end_anchor = Anchor(pos=p3, kind="smooth", h_in=h_in_end, h_out=(0, 0))
            return [start_anchor, end_anchor]

        # 分割
        left = self._fit_recursive(points, tangents, first, split_idx, error_threshold_sq)
        right = self._fit_recursive(points, tangents, split_idx, last, error_threshold_sq)

        # 結合: leftの最後とrightの最初は同じ点 (split_idx)
        # left[-1] の h_in (左からの入り) と right[0] の h_out (右への出) を合成して
        # 1つのアンカーにする
        mid_in = left[-1].h_in
        mid_out = right[0].h_out

        merged_anchor = Anchor(pos=points[split_idx], kind="smooth", h_in=mid_in, h_out=mid_out)

        return [*left[:-1], merged_anchor, *right[1:]]

    def _compute_tangents(
        self, points: list[tuple[float, float]], closed: bool
    ) -> list[tuple[float, float]]:
        """各点における単位接線ベクトルを計算する (Catmull-Rom like)"""
        if not points:
            return []
        n = len(points)
        tangents = [(0.0, 0.0)] * n

        for i in range(n):
            if i == 0:
                if closed:
                    prev = points[-2]
                    next_p = points[1]
                else:
                    prev = points[0]
                    next_p = points[1] if n > 1 else points[0]
            elif i == n - 1:
                if closed:
                    prev = points[-2]
                    next_p = points[1]
                else:
                    prev = points[-2]
                    next_p = points[-1]
            else:
                prev = points[i - 1]
                next_p = points[i + 1]

            # 隣接点間のベクトル
            dx = next_p[0] - prev[0]
            dy = next_p[1] - prev[1]
            length = math.hypot(dx, dy)
            if length > 1e-6:
                tangents[i] = (dx / length, dy / length)
            else:
                tangents[i] = (0.0, 0.0)
        return tangents

    def _eval_bezier(self, p0, p1, p2, p3, t):
        mt = 1.0 - t
        mt2 = mt * mt
        mt3 = mt2 * mt
        t2 = t * t
        t3 = t2 * t

        x = mt3 * p0[0] + 3 * mt2 * t * p1[0] + 3 * mt * t2 * p2[0] + t3 * p3[0]
        y = mt3 * p0[1] + 3 * mt2 * t * p1[1] + 3 * mt * t2 * p2[1] + t3 * p3[1]
        return (x, y)

    # ------------------------------------------------------------------

    def _apply_handle_smoothness(self, anchors: list[Anchor], *, closed: bool) -> list[Anchor]:
        strength = max(
            0.0,
            min(1.0, float(getattr(self.settings, "handle_smoothness", 0.0) or 0.0)),
        )
        if strength <= 1e-6 or len(anchors) < 3:
            return anchors

        total = len(anchors)
        unique = total
        if closed and total >= 2 and anchors[0].pos == anchors[-1].pos:
            unique = total - 1
        if unique < 3:
            return anchors

        updated = list(anchors)
        for i in range(unique):
            if not closed and (i == 0 or i == unique - 1):
                continue
            prev_pos = anchors[(i - 1) % unique].pos
            curr_pos = anchors[i].pos
            next_pos = anchors[(i + 1) % unique].pos
            h_in, h_out = self._compute_smooth_handles(
                prev_pos,
                curr_pos,
                next_pos,
                strength=strength,
            )
            updated[i] = Anchor(pos=curr_pos, kind="smooth", h_in=h_in, h_out=h_out)

        if closed and unique != total:
            updated[-1] = Anchor(
                pos=updated[0].pos,
                kind=updated[0].kind,
                h_in=updated[0].h_in,
                h_out=updated[0].h_out,
            )
        return updated

    def _compute_smooth_handles(
        self,
        prev_pos: tuple[float, float],
        curr_pos: tuple[float, float],
        next_pos: tuple[float, float],
        *,
        strength: float,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        vx = float(next_pos[0] - prev_pos[0])
        vy = float(next_pos[1] - prev_pos[1])
        v_len = math.hypot(vx, vy)
        if v_len <= 1e-6:
            return (0.0, 0.0), (0.0, 0.0)
        tx = vx / v_len
        ty = vy / v_len
        d_prev = math.hypot(curr_pos[0] - prev_pos[0], curr_pos[1] - prev_pos[1])
        d_next = math.hypot(next_pos[0] - curr_pos[0], next_pos[1] - curr_pos[1])
        base = min(d_prev, d_next) / 3.0
        length = max(0.0, base * max(0.0, min(1.0, strength)))
        h_out = (tx * length, ty * length)
        h_in = (-h_out[0], -h_out[1])
        return h_in, h_out

    def _simplify_positions(
        self, positions: list[tuple[float, float]], density: float
    ) -> list[tuple[float, float]]:
        """Downsample positions based on density (0.1-1.0)."""

        if len(positions) <= 2 or density >= 0.999:
            return positions
        target = max(2, int(len(positions) * density))
        if target >= len(positions):
            return positions

        stride = max(1, len(positions) // target)
        simplified = [positions[0]]
        skip = 0
        for pos in positions[1:-1]:
            skip += 1
            if skip >= stride:
                simplified.append(pos)
                skip = 0
        simplified.append(positions[-1])

        if len(simplified) > target:
            simplified = [*simplified[: target - 1], positions[-1]]
        return simplified

    def _property_specs(self) -> list[PropertySpec]:
        return [
            PropertySpec(
                key="edit_mode",
                display_name=_ts("Edit Mode"),
                type=PropertyType.ENUM,
                enum_items=["anchor", "shape"],
                default_value=self.settings.edit_mode,
            ),
            PropertySpec(
                key="auto_close",
                display_name=_ts("Auto Close"),
                type=PropertyType.BOOL,
                default_value=self.settings.auto_close,
            ),
            PropertySpec(
                key="stroke_width",
                display_name=_ts("Stroke Width"),
                type=PropertyType.FLOAT,
                min_value=0.1,
                max_value=64.0,
                default_value=self.settings.stroke_width,
                step=0.1,
                is_common=True,
            ),
            PropertySpec(
                key="stroke_color",
                display_name=_ts("Stroke Color"),
                type=PropertyType.COLOR,
                default_value=self.settings.stroke_color,
                is_common=True,
            ),
            PropertySpec(
                key="fill_enabled",
                display_name=_ts("Fill Enabled"),
                type=PropertyType.BOOL,
                default_value=self.settings.fill_enabled,
                is_common=True,
            ),
            PropertySpec(
                key="fill_color",
                display_name=_ts("Fill Color"),
                type=PropertyType.COLOR,
                default_value=self.settings.fill_color,
                is_common=True,
            ),
            PropertySpec(
                key="anchor_density",
                display_name=_ts("Anchor Density"),
                type=PropertyType.FLOAT,
                min_value=0.1,
                max_value=1.0,
                step=0.05,
                default_value=self.settings.anchor_density,
            ),
            PropertySpec(
                key="handle_smoothness",
                display_name=_ts("Handle Smoothness"),
                type=PropertyType.FLOAT,
                min_value=0.0,
                max_value=5.0,
                step=0.1,
                default_value=self.settings.handle_smoothness,
            ),
        ]

    def _create_vector_layer_id(self) -> str | None:
        # Reuse existing vector layer if available
        existing = self._resolve_existing_vector_layer_id()
        if existing:
            return existing
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        add_layer = getattr(canvas, "add_vector_layer", None)
        if callable(add_layer):
            try:
                ref = add_layer()
                if ref is not None:
                    created = getattr(ref, "id", None)
                    if created:
                        self._created_layer_id = created
                    return created
            except Exception:
                logger.debug("vector add_vector_layer failed")
        return None

    def _ensure_target_layer_id(self) -> str | None:
        if self._target_layer_id:
            return self._target_layer_id
        if not self._pending_objects:
            return None
        created = self._create_vector_layer_id()
        self._target_layer_id = created
        return created

    def _emit_pending_immediate(self) -> None:
        if not self.host_api or not hasattr(self.host_api, "canvas"):
            return
        if not self._pending_objects:
            return
        target_layer_id = self._ensure_target_layer_id()
        created_layer_id = self._created_layer_id
        commands = [
            VectorCreateCommand(
                obj,
                layer_id=target_layer_id,
                merge_key="vector_precise",
                created_layer_id=created_layer_id,
            )
            for obj in self._pending_objects
        ]
        self._pending_objects.clear()
        self._created_layer_id = None
        if not commands:
            return
        for cmd in commands:
            if isinstance(cmd, VectorCreateCommand) and cmd.layer_id is None:
                cmd.layer_id = target_layer_id
            try:
                success = self.host_api.canvas.submit(cmd, merge_key="vector_precise")
                if not success:
                    self._pending_commands.append(cmd)
                else:
                    inserted_layer_id = (
                        getattr(cmd, "_insert_layer_id", None) or cmd.layer_id or target_layer_id
                    )
                    self._select_created_object(cmd.vector_object, inserted_layer_id)
            except Exception as exc:
                logger.warning("VectorPrecise submit failed: %s", exc)
                self._pending_commands.append(cmd)
        with contextlib.suppress(Exception):
            self._clear_canvas_preview()

    # --- Selection / editing helpers -----------------------------------
    def _vector_store(self) -> list[tuple[str | None, object]]:
        canvas = getattr(self.host_api, "canvas", None)
        if not canvas:
            return []
        getter = getattr(canvas, "get_vector_objects", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                logger.debug("get_vector_objects failed")
        store = getattr(canvas, "_vector_objects", None)
        if store is None:
            store = []
            try:
                canvas._vector_objects = store  # type: ignore[attr-defined]  # duck-typed canvas attr
            except Exception:
                logger.debug("Vector store init failed")
                return []
        return [(None, obj) for obj in store]

    def _hit_test_anchor(  # noqa: PLR0912, PLR0915
        self,
        pt: HostStrokePoint,
        *,
        prefer_anchor: bool = False,
        prefer_handle: bool = False,
    ) -> bool:
        store = self._vector_store()
        if not store:
            return False
        px, py = float(pt.pos[0]), float(pt.pos[1])
        best: tuple[str, int, int, float, str | None] | None = (
            None  # (id, path, anchor, dist2, handle)
        )
        threshold_sq = VectorHitTestGeometry(self.host_api).canvas_radius() ** 2
        if prefer_handle:
            for _layer_id, vec in store:
                if is_editable_parametric_shape(vec):
                    continue
                vec_id = getattr(vec, "id", None)
                paths = getattr(vec, "paths", None) or []
                for pi, path in enumerate(paths):
                    anchors = getattr(path, "anchors", None) or []
                    for ai, anchor in enumerate(anchors):
                        for handle_kind, vec_handle in (
                            ("in", anchor.h_in),
                            ("out", anchor.h_out),
                        ):
                            if not vec_handle:
                                continue
                            hx = anchor.pos[0] + vec_handle[0]
                            hy = anchor.pos[1] + vec_handle[1]
                            dxh = hx - px
                            dyh = hy - py
                            dist2h = dxh * dxh + dyh * dyh
                            if dist2h <= threshold_sq and (best is None or dist2h < best[3]):
                                best = (vec_id, pi, ai, dist2h, handle_kind)
            if best:
                vec_id, pi, ai, _, handle = best
                layer_id = None
                for candidate_layer_id, candidate in store:
                    if getattr(candidate, "id", None) == vec_id:
                        layer_id = candidate_layer_id
                        self._editing_object = copy_vector_object(candidate)
                        break
                if self._editing_object:
                    self._editing_object.paths[pi] = self._ensure_handles(
                        self._editing_object.paths[pi], ai
                    )
                self._selected = (vec_id, pi, ai, layer_id)
                self._handle_drag = handle
                self._update_canvas_selection(vec_id, pi, ai, layer_id, preview=True)
                return True
        if prefer_anchor:
            for _layer_id, vec in store:
                if is_editable_parametric_shape(vec):
                    continue
                vec_id = getattr(vec, "id", None)
                paths = getattr(vec, "paths", None) or []
                for pi, path in enumerate(paths):
                    anchors = getattr(path, "anchors", None) or []
                    for ai, anchor in enumerate(anchors):
                        dx = anchor.pos[0] - px
                        dy = anchor.pos[1] - py
                        dist2 = dx * dx + dy * dy
                        if dist2 <= threshold_sq and (best is None or dist2 < best[3]):
                            best = (vec_id, pi, ai, dist2, None)
            if best:
                vec_id, pi, ai, _, _ = best
                layer_id = None
                for candidate_layer_id, candidate in store:
                    if getattr(candidate, "id", None) == vec_id:
                        layer_id = candidate_layer_id
                        self._editing_object = copy_vector_object(candidate)
                        break
                if self._editing_object:
                    self._editing_object.paths[pi] = self._ensure_handles(
                        self._editing_object.paths[pi], ai
                    )
                self._selected = (vec_id, pi, ai, layer_id)
                self._handle_drag = None
                self._update_canvas_selection(vec_id, pi, ai, layer_id, preview=True)
                return True

        for layer_id, vec in store:
            if is_editable_parametric_shape(vec):
                continue
            vec_id = getattr(vec, "id", None)
            paths = getattr(vec, "paths", None) or []
            for pi, path in enumerate(paths):
                anchors = getattr(path, "anchors", None) or []
                for ai, anchor in enumerate(anchors):
                    dx = anchor.pos[0] - px
                    dy = anchor.pos[1] - py
                    dist2 = dx * dx + dy * dy
                    anchor_candidate = (
                        (vec_id, pi, ai, dist2, None) if dist2 <= threshold_sq else None
                    )
                    if prefer_anchor and anchor_candidate:
                        self._selected = (vec_id, pi, ai, layer_id)
                        self._editing_object = copy_vector_object(vec)
                        self._editing_object.paths[pi] = self._ensure_handles(
                            self._editing_object.paths[pi], ai
                        )
                        self._handle_drag = None
                        self._update_canvas_selection(vec_id, pi, ai, layer_id, preview=True)
                        return True
                    # Handle hit
                    for handle_kind, vec_handle in (
                        ("in", anchor.h_in),
                        ("out", anchor.h_out),
                    ):
                        if vec_handle:
                            hx = anchor.pos[0] + vec_handle[0]
                            hy = anchor.pos[1] + vec_handle[1]
                            dxh = hx - px
                            dyh = hy - py
                            dist2h = dxh * dxh + dyh * dyh
                            if dist2h <= threshold_sq and (best is None or dist2h < best[3]):
                                best = (vec_id, pi, ai, dist2h, handle_kind)
                    if (
                        anchor_candidate
                        and anchor_candidate[3] <= threshold_sq
                        and (
                            best is None
                            or anchor_candidate[3] < best[3]
                            or (
                                not prefer_handle
                                and best is not None
                                and anchor_candidate[3] == best[3]
                                and best[4] is not None
                            )
                            or (
                                best is not None
                                and anchor_candidate[3] == best[3]
                                and best[4] is not None
                            )
                        )
                    ):
                        best = anchor_candidate
        if best is None:
            return False
        vec_id, pi, ai, _, handle = best
        layer_id = None
        for candidate_layer_id, candidate in store:
            if getattr(candidate, "id", None) == vec_id:
                layer_id = candidate_layer_id
                break
        self._selected = (vec_id, pi, ai, layer_id)
        for _lid, candidate in store:
            if getattr(candidate, "id", None) == vec_id:
                self._editing_object = copy_vector_object(candidate)
                self._editing_object.paths[pi] = self._ensure_handles(
                    self._editing_object.paths[pi], ai
                )
                break
        self._handle_drag = handle
        self._update_canvas_selection(vec_id, pi, ai, layer_id, preview=True)
        return True

    def _move_selected_anchor(self, pt: HostStrokePoint, *, preview_only: bool) -> None:
        if not self._editing_object or not self._selected:
            return
        _, pi, ai, layer_id = self._selected
        if pi >= len(self._editing_object.paths):
            return
        path = self._editing_object.paths[pi]
        if ai >= len(path.anchors):
            return
        anchors = list(path.anchors)
        anchor = anchors[ai]

        if self._handle_drag and anchor.h_in is not None and anchor.h_out is not None:
            vec = (float(pt.pos[0]) - anchor.pos[0], float(pt.pos[1]) - anchor.pos[1])
            if self._handle_drag == "in":
                anchors[ai] = Anchor(pos=anchor.pos, kind="smooth", h_in=vec, h_out=anchor.h_out)
            else:
                anchors[ai] = Anchor(pos=anchor.pos, kind="smooth", h_in=anchor.h_in, h_out=vec)
        else:
            anchors[ai] = Anchor(
                pos=(float(pt.pos[0]), float(pt.pos[1])),
                kind=anchor.kind,
                h_in=anchor.h_in,
                h_out=anchor.h_out,
            )
        self._editing_object.paths[pi] = VectorPath(anchors=anchors, closed=path.closed)

        if preview_only and self.host_api and getattr(self.host_api, "canvas", None):
            try:
                canvas = self.host_api.canvas
                # Invalidate QPainterPath cache — object is mutated in-place
                qpath_cache = getattr(canvas, "_qpath_cache", None)
                if isinstance(qpath_cache, dict):
                    qpath_cache.pop(id(self._editing_object), None)
                canvas._vector_preview = self._editing_object  # type: ignore[attr-defined]  # duck-typed canvas attr
                canvas._vector_preview_layer_id = layer_id  # type: ignore[attr-defined]  # duck-typed canvas attr
            except Exception:
                logger.debug("vector preview update failed", exc_info=True)
        # Single _recompose via _update_canvas_selection (preview is already set)
        self._update_canvas_selection(self._selected[0], pi, ai, layer_id, preview=preview_only)

    def _commit_editing_object(self) -> None:
        if not self._editing_object or not self._selected or not self.host_api:
            return
        target_id, _, _, layer_id = self._selected
        cmd = VectorReplaceCommand(target_id, self._editing_object, layer_id=layer_id)
        try:
            self.host_api.canvas.submit(cmd, merge_key=None)
        except Exception:
            self._pending_commands.append(cmd)
        self._update_canvas_selection(
            target_id, self._selected[1], self._selected[2], layer_id, preview=False
        )
        self._clear_canvas_preview()

    def _update_canvas_selection(
        self,
        vec_id: str,
        path_idx: int,
        anchor_idx: int,
        layer_id: str | None,
        *,
        preview: bool = False,
        path_id: str | None = None,
        anchor_id: str | None = None,
    ) -> None:
        if self.host_api and getattr(self.host_api, "canvas", None):
            try:
                selection = {
                    "id": vec_id,
                    "path": path_idx,
                    "anchor": anchor_idx,
                    "layer_id": layer_id,
                    "preview": preview,
                }
                if path_id:
                    selection["path_id"] = path_id
                if anchor_id:
                    selection["anchor_id"] = anchor_id
                self.host_api.canvas._vector_selection = selection  # type: ignore[attr-defined]  # duck-typed canvas attr
                if hasattr(self.host_api.canvas, "notify_vector_preview_changed"):
                    self.host_api.canvas.notify_vector_preview_changed()
                elif hasattr(self.host_api.canvas, "_recompose"):
                    self.host_api.canvas._recompose()
            except Exception:
                logger.debug("vector selection update failed", exc_info=True)

    def _select_created_object(self, obj: object, layer_id: str | None) -> None:
        paths = getattr(obj, "paths", None) or []
        if not paths:
            return
        anchors = getattr(paths[0], "anchors", None) or []
        if not anchors:
            return
        anchor_idx = len(anchors) - 1
        path_id, anchor_id = self._selection_element_ids(obj, 0, anchor_idx)
        self._update_canvas_selection(
            getattr(obj, "id", ""),
            0,
            anchor_idx,
            layer_id,
            preview=False,
            path_id=path_id,
            anchor_id=anchor_id,
        )

    @staticmethod
    def _selection_element_ids(
        obj: object,
        path_idx: int,
        anchor_idx: int,
    ) -> tuple[str | None, str | None]:
        path_id: str | None = None
        anchor_id: str | None = None
        meta = getattr(obj, "meta", None)
        if isinstance(meta, dict):
            ids_meta = meta.get(VECTOR_IDS_META_KEY)
            if isinstance(ids_meta, dict):
                paths_meta = ids_meta.get("paths")
                if isinstance(paths_meta, list) and 0 <= path_idx < len(paths_meta):
                    path_meta = paths_meta[path_idx]
                    if isinstance(path_meta, dict):
                        raw_path_id = path_meta.get("id")
                        if isinstance(raw_path_id, str):
                            path_id = raw_path_id
                        anchors_meta = path_meta.get("anchors")
                        if (
                            isinstance(anchors_meta, list)
                            and 0 <= anchor_idx < len(anchors_meta)
                            and isinstance(anchors_meta[anchor_idx], dict)
                        ):
                            raw_anchor_id = anchors_meta[anchor_idx].get("id")
                            if isinstance(raw_anchor_id, str):
                                anchor_id = raw_anchor_id
        return path_id, anchor_id

    def _clear_vector_selection(self) -> None:
        """Clear vector selection state on canvas when switching away from this tool."""
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if canvas is None:
            return
        try:
            if hasattr(canvas, "_vector_selection"):
                canvas._vector_selection = None  # type: ignore[attr-defined]  # duck-typed canvas attr
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
            elif hasattr(canvas, "_recompose"):
                canvas._recompose()
        except Exception:
            logger.debug("vector selection clear failed", exc_info=True)

    def _try_switch_to_edit_tool(self, pt: HostStrokePoint) -> bool:
        """If pt hits an existing vector object, switch to edit tool with auto-return."""
        if not self.host_api:
            return False
        store = self._vector_store()
        if not store:
            return False
        px, py = float(pt.pos[0]), float(pt.pos[1])
        threshold = VectorHitTestGeometry(self.host_api).canvas_radius()
        for layer_id, vec in store:
            if self._point_hits_vector(px, py, vec, threshold):
                return self._activate_edit_tool_with_return(getattr(vec, "id", None), layer_id)
        return False

    @staticmethod
    def _point_hits_vector(px: float, py: float, vec: object, threshold: float) -> bool:
        return CubicHitTest.hits_vector(px, py, vec, threshold)

    def _activate_edit_tool_with_return(self, vec_id: str | None, layer_id: str | None) -> bool:
        """Switch to edit tool with auto-return to this tool."""
        host = self.host_api
        if not host:
            return False
        canvas = getattr(host, "canvas", None)
        if canvas and vec_id:
            with contextlib.suppress(Exception):
                canvas._vector_selection = {  # type: ignore[attr-defined]  # duck-typed canvas attr
                    "id": vec_id,
                    "layer_id": layer_id,
                    "auto_return_tool": "vector_pen_precise",
                    "auto_return_origin_id": vec_id,
                }
        tool_manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
        activate = getattr(tool_manager, "activate_tool", None) if tool_manager else None
        if not callable(activate):
            return False
        try:
            activate("vector_path_edit_tool")
            return True
        except Exception:
            return False

    def _clear_canvas_preview(self) -> None:
        if self.host_api and getattr(self.host_api, "canvas", None):
            try:
                if hasattr(self.host_api.canvas, "_vector_preview"):
                    self.host_api.canvas._vector_preview = None  # type: ignore[attr-defined]  # duck-typed canvas attr
                    self.host_api.canvas._vector_preview_layer_id = None  # type: ignore[attr-defined]  # duck-typed canvas attr
                    if hasattr(self.host_api.canvas, "notify_vector_preview_changed"):
                        self.host_api.canvas.notify_vector_preview_changed()
                    elif hasattr(self.host_api.canvas, "_recompose"):
                        self.host_api.canvas._recompose()
            except Exception:
                logger.debug("canvas preview clear failed", exc_info=True)

    def _ensure_handles(self, path: VectorPath, anchor_idx: int) -> VectorPath:
        anchors = list(path.anchors)
        if anchor_idx >= len(anchors):
            return path
        anchor = anchors[anchor_idx]
        if anchor.h_in and anchor.h_out:
            return path
        prev_anchor = anchors[anchor_idx - 1] if anchor_idx > 0 else None
        next_anchor = anchors[anchor_idx + 1] if anchor_idx + 1 < len(anchors) else None
        vec_x = 12.0
        vec_y = 0.0
        if prev_anchor and next_anchor:
            dx = next_anchor.pos[0] - prev_anchor.pos[0]
            dy = next_anchor.pos[1] - prev_anchor.pos[1]
            vec_x = dx * 0.3
            vec_y = dy * 0.3
        anchors[anchor_idx] = Anchor(
            pos=anchor.pos,
            kind="smooth",
            h_in=(-vec_x, -vec_y),
            h_out=(vec_x, vec_y),
        )
        return VectorPath(anchors=anchors, closed=path.closed)

    def _apply_active_color(self) -> None:
        host = self.host_api
        if not host or not getattr(host, "color_state", None):
            return
        try:
            self._paint_color = host.color_state.current
            rgba = self._paint_color.rgba
            rgb = tuple(int(max(0, min(255, round(c * 255)))) for c in rgba[:3])
            self.settings.stroke_color = rgb
            if self.settings.fill_enabled and self.settings.fill_color is None:
                self.settings.fill_color = rgb
        except Exception:
            logger.debug("VectorPrecise active color fetch failed", exc_info=True)

    def _sync_active_color_from_settings(self, *, preserve_native: bool = True) -> None:
        host = self.host_api
        if not host or not getattr(host, "color_state", None):
            return
        if preserve_native and capture_paint(host.color_state.current) is not None:
            return
        try:
            rgb = self.settings.stroke_color
            if rgb is None:
                return
            r, g, b = rgb
            # stroke_color is always stored as 0-255 ints by normalize_rgb
            r, g, b = (max(0, min(255, round(c))) for c in (r, g, b))
            host.color_state.apply(ColorDescriptor((r / 255, g / 255, b / 255, 1.0)))
        except Exception:
            logger.debug("VectorPrecise color push failed", exc_info=True)


class VectorPrecisePlugin:
    def __init__(self) -> None:
        self.metadata = PluginMetadata(
            id="vector_pen_precise",
            name=_ts("Vector Pen (Precise)"),
            version="0.1.0",
            description=_ts("Illustrator-like precise vector pen"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )
        self.tool = VectorPreciseTool()

    def __getattr__(self, name: str):
        return getattr(self.tool, name)

    def activate(self, host_api) -> None:
        self.tool.activate(host_api)

    def deactivate(self) -> None:
        self.tool.deactivate()


PLUGIN_METADATA = VectorPrecisePlugin().metadata


def create_plugin() -> VectorPrecisePlugin:
    return VectorPrecisePlugin()
