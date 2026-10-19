"""Screen-Space Vector Overlay マネージャ (Phase 1 SSO Session 2/3).

仕様: docs/dev/plan/future/Feature/resolution_independent_vector/01_design_spec.md
       §5.1 / §5.2 / §5.5 / §5.6 / §5.7 / §5.8 / §6.1 / §12.1 (rev.6)

Topmost vector layer をズーム時にシャープ表示するための QPainter Screen-Space
Overlay 実装。canvas-space ラスタライズ ((A) パス) を skip し、widget 解像度で
QPainter による direct raster + drawImage 合成を行う ((B) パス)。

GL 依存はない (QImage / QPainter のみ)。eligibility 遷移検知 / Pan-only fast
path / QImage 再利用 / `_qpath_cache` の prune を含む。
"""

from __future__ import annotations

import logging
import math
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter, QTransform

from . import screen_space_overlay_pan
from .projection_state import flatten_all_layers_with_parents
from .screen_space_overlay_render_coordinator import ScreenSpaceOverlayRenderCoordinator

logger = logging.getLogger(__name__)

# PD-3 計測用: ログ出力間隔 (サンプル数)
_TIMING_LOG_INTERVAL = 100

# ローリングバッファの最大長 (パーセンタイル集計用サンプル上限)
_TIMING_BUFFER_MAXLEN = 100
_RENDER_DIAGNOSTICS_TRUTHY = {"1", "true", "yes", "on"}


def _render_diagnostics_enabled() -> bool:
    return os.environ.get("LOOMSTROKE_RENDER_DIAGNOSTICS", "").strip().lower() in (
        _RENDER_DIAGNOSTICS_TRUTHY
    )


# SSO は widget 解像度の一時 QImage にベクターを描くため、左上の
# image 境界外にある stroke / anchor のにじみが可視領域へ入るケースを
# 余白なしでは拾えない。zoom に応じた logical overscan で端部欠けを防ぐ。
_MIN_EDGE_OVERSCAN_LOGICAL = 256
_MAX_EDGE_OVERSCAN_LOGICAL = 512
_EDGE_OVERSCAN_ZOOM_FACTOR = 12.0
_EDGE_OVERSCAN_BASE = 32.0

__all__ = [
    "OverlayCacheEntry",
    "ScreenSpaceOverlayManager",
    "percentile",
]


def percentile(samples: list[float], p: float) -> float:
    """Nearest-rank percentile. 空リストは 0.0 を返す。

    sorted + index 計算で十分。numpy 依存を避けるため手実装。
    """
    if not samples:
        return 0.0
    ordered = sorted(samples)
    n = len(ordered)
    rank = round(p / 100.0 * (n - 1))
    rank = max(0, min(rank, n - 1))
    return ordered[rank]


@dataclass
class OverlayCacheEntry:
    """SSO 経由で raster 済みの 1 layer 分キャッシュ。

    invariant_key (zoom/rotation/dpr) が一致 + rendered_layer_version が一致
    なら再利用。pan_key 不一致なら translate fast path で drawImage する。
    """

    layer_id: str
    image: QImage
    invariant_key: tuple[float, ...]  # (zoom, rotation, dpr)
    pan_key: tuple[float, ...]  # (center_x, center_y)
    viewport_state: dict[str, Any] = field(default_factory=dict)
    rendered_layer_version: int = 0
    effective_dpr: float = 1.0


class ScreenSpaceOverlayManager:
    """QPainter による screen-space vector overlay (rev.4 / rev.6 R-5/R-6/R-7)."""

    MAX_DIM = 4096

    def __init__(self) -> None:
        self._entries: dict[str, OverlayCacheEntry] = {}
        self._widget_w_logical = 0
        self._widget_h_logical = 0
        # eligibility 遷移検知用 (rev.4)
        self._layer_eligibility: dict[str, bool] = {}
        # Preview pass の QImage 再利用用 (rev.4)
        self._preview_image: QImage | None = None
        self._enabled = True
        # PD-3 計測 (Phase 1b): 既定 False で perf_counter 呼び出しなし。
        # True 時は _render_layer_to_overlay / render_preview の所要時間を
        # 記録し、100 サンプル毎に DEBUG ログへ p50/p95 を出力する。
        self._debug_timing_enabled = False
        self._eligible_render_times: deque[float] = deque(maxlen=_TIMING_BUFFER_MAXLEN)
        self._preview_render_times: deque[float] = deque(maxlen=_TIMING_BUFFER_MAXLEN)
        self._eligible_sample_counter = 0
        self._preview_sample_counter = 0
        self._eligible_layer_count = 0
        self._eligible_render_count = 0
        self._eligible_reuse_count = 0
        self._eligible_pan_reuse_count = 0
        self._preview_render_count = 0
        self._render_coordinator = ScreenSpaceOverlayRenderCoordinator(
            self,
            entry_factory=OverlayCacheEntry,
            timing_log_interval=_TIMING_LOG_INTERVAL,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        if self._enabled == enabled:
            return
        self._enabled = enabled
        if not enabled:
            self.cleanup()

    def set_debug_timing(self, enabled: bool) -> None:
        """PD-3 計測 (Q-3 実測) の ON/OFF。

        True のとき ``_render_layer_to_overlay`` と ``render_preview`` の
        所要時間を記録し、100 サンプル毎に DEBUG ログで p50/p95 を出力する。
        False に戻すとバッファをクリアする。
        """
        new_enabled = bool(enabled)
        if self._debug_timing_enabled == new_enabled:
            return
        self._debug_timing_enabled = new_enabled
        if not new_enabled:
            self._eligible_render_times.clear()
            self._preview_render_times.clear()
            self._eligible_sample_counter = 0
            self._preview_sample_counter = 0

    def get_timing_stats(self) -> dict[str, Any]:
        """現在のローリングバッファから p50/p95 を算出して返す。

        サンプル 0 件の場合は ms フィールドに 0.0 を返す。
        dogfood 中に preferences / debug panel から呼び出して可視化する想定。
        """
        eligible = list(self._eligible_render_times)
        preview = list(self._preview_render_times)
        return {
            "eligible_samples": len(eligible),
            "eligible_p50_ms": percentile(eligible, 50),
            "eligible_p95_ms": percentile(eligible, 95),
            "preview_samples": len(preview),
            "preview_p50_ms": percentile(preview, 50),
            "preview_p95_ms": percentile(preview, 95),
        }

    def get_diagnostics(self) -> dict[str, Any]:
        """Return lightweight SSO counters and optional timing stats."""

        timing = self.get_timing_stats()
        counters_enabled = self._collect_diagnostics()
        return {
            "eligible_layer_count": self._eligible_layer_count if counters_enabled else 0,
            "eligible_render_count": self._eligible_render_count if counters_enabled else 0,
            "eligible_reuse_count": self._eligible_reuse_count if counters_enabled else 0,
            "eligible_pan_reuse_count": (self._eligible_pan_reuse_count if counters_enabled else 0),
            "preview_render_count": self._preview_render_count if counters_enabled else 0,
            **timing,
        }

    def _collect_diagnostics(self) -> bool:
        return self._debug_timing_enabled or _render_diagnostics_enabled()

    def _record_eligible_layer_count(self, enabled: bool, count: int) -> None:
        if enabled:
            self._eligible_layer_count = count

    def _record_eligible_reuse(self, enabled: bool, *, pan_reuse: bool = False) -> None:
        if not enabled:
            return
        self._eligible_reuse_count += 1
        if pan_reuse:
            self._eligible_pan_reuse_count += 1

    def _record_eligible_render(self, enabled: bool) -> None:
        if enabled:
            self._eligible_render_count += 1

    def _log_timing_summary(self, kind: str, samples: list[float]) -> None:
        if not samples:
            return
        logger.debug(
            "SSO %s raster timing: p50=%.2fms p95=%.2fms max=%.2fms (last %d samples)",
            kind,
            percentile(samples, 50),
            percentile(samples, 95),
            max(samples),
            len(samples),
        )

    def set_widget_size(self, w_logical: int, h_logical: int) -> None:
        """Widget サイズ変化時に呼ばれる (R-1: canvas_widget.resizeEvent から hook)。

        サイズが変わったら entry / preview image を破棄して次フレームで再生成。
        """
        if (w_logical, h_logical) == (self._widget_w_logical, self._widget_h_logical):
            return
        self._widget_w_logical = w_logical
        self._widget_h_logical = h_logical
        self._entries.clear()
        self._preview_image = None

    def cleanup(self) -> None:
        """全状態をクリア (kill switch / GL context loss / disable 時)。"""
        self._entries.clear()
        self._preview_image = None
        self._layer_eligibility.clear()
        self._eligible_layer_count = 0

    # ------------------------------------------------------------------
    # Eligibility (§5.1)
    # ------------------------------------------------------------------

    @staticmethod
    def is_eligible(layer: Any, above: list[Any]) -> bool:  # noqa: PLR0911 — guard list per §5.1
        """Phase 1 で SSO 描画対象とする条件 (§5.1)。

        SSO は ``painter.drawImage`` で full SourceOver / opacity=1.0 描画する
        ため、layer-level opacity や blend_mode は適用されない。これらが
        default 以外の場合は CST パスにフォールバックさせる必要がある
        (Codex review 指摘 — Phase 2 で QPainter.CompositionMode への mapping
        を検討可能)。

        NOTE: ``above`` は visible-only flatten の結果を想定するため、
        visibility 再チェックは原則 dead だが外部 API 安全性のため残す。
        """
        if getattr(layer, "layer_type", None) != "vector":
            return False
        from loomstroke.core.appearance.live_styles import style_signature  # noqa: PLC0415

        if style_signature(layer):
            return False
        if not getattr(layer, "visible", True):
            return False
        if getattr(layer, "layer_mask", None) is not None and getattr(layer, "mask_enabled", True):
            return False
        if getattr(layer, "clipping", False):
            return False
        # default compositing only: opacity == 1.0 + blend_mode == "normal"
        # (SSO の drawImage は SourceOver / opacity 適用なし)
        if float(getattr(layer, "opacity", 1.0)) < 1.0:
            return False
        blend = getattr(layer, "blend_mode", "normal")
        blend_str = blend.value if hasattr(blend, "value") else str(blend)
        if blend_str != "normal":
            return False
        children = getattr(layer, "children", []) or []
        if any(
            getattr(c, "layer_type", None) == "effect" and getattr(c, "visible", True)
            for c in children
        ):
            return False
        # 上位 (visible-only flatten の後続) が空なら topmost
        return not above

    def _ensure_flat(self, canvas: Any, flat: list[Any] | None) -> list[Any]:
        """R-5: flat 引数が指定されていればそれを使う。None なら canvas から再 flatten。

        ``_paint_gpu`` 側で 1 度だけ flatten した結果をバケツリレーすることで
        1 フレーム内の重複 flatten を回避する。
        """
        if flat is not None:
            return flat

        result, _ = flatten_all_layers_with_parents(getattr(canvas, "_layers", []))
        return result

    def above_layers_of(
        self, canvas: Any, layer_id: str, *, flat: list[Any] | None = None
    ) -> list[Any]:
        flat = self._ensure_flat(canvas, flat)
        for i, lay in enumerate(flat):
            if getattr(lay, "id", None) == layer_id:
                return flat[i + 1 :]
        return []

    def is_layer_eligible_now(
        self, canvas: Any, layer_id: str, *, flat: list[Any] | None = None
    ) -> bool:
        flat = self._ensure_flat(canvas, flat)
        for i, lay in enumerate(flat):
            if getattr(lay, "id", None) == layer_id:
                return self.is_eligible(lay, flat[i + 1 :])
        return False

    def collect_eligible(self, canvas: Any, *, flat: list[Any] | None = None) -> list[Any]:
        flat = self._ensure_flat(canvas, flat)
        result: list[Any] = []
        for i, layer in enumerate(flat):
            if self.is_eligible(layer, flat[i + 1 :]):
                result.append(layer)
        return result

    def sync_eligibility_transitions(
        self, canvas: Any, *, flat: list[Any] | None = None
    ) -> set[str]:
        """各 vector layer の eligibility を前フレームと比較して、
        遷移時には ``layer.texture_version`` を強制 bump する (§5.2)。

        ``_sync_layer_textures`` は texture_version 一致時に rasterize を skip
        するため、eligibility だけが変わったケースでは CST が更新されない。
        bump して (A) パスを強制走査させ CST と SSO の整合を保つ。

        Returns:
            現在 eligible な layer id の集合。R-7: 呼び出し側で
            ``self._projection_tracker.set_skipped_layer_ids(eligible_ids)``
            に渡し、projection cache の無駄 rebuild を抑制する。
        """
        if not self._enabled:
            self._layer_eligibility.clear()
            return set()
        flat = self._ensure_flat(canvas, flat)
        current: dict[str, bool] = {}
        eligible_ids: set[str] = set()
        texture_bump_ids: list[str] = []
        for i, layer in enumerate(flat):
            if getattr(layer, "layer_type", None) != "vector":
                continue
            lid = getattr(layer, "id", None)
            if lid is None:
                continue
            elig = self.is_eligible(layer, flat[i + 1 :])
            current[lid] = elig
            if elig:
                eligible_ids.add(lid)
            prev = self._layer_eligibility.get(lid)
            if prev is None or prev == elig:
                continue
            texture_bump_ids.append(lid)
            if not hasattr(canvas, "bump_layer_texture_versions"):
                try:
                    layer.texture_version = int(getattr(layer, "texture_version", 0)) + 1
                except (TypeError, AttributeError):
                    logger.debug("texture_version bump failed for %s", lid)
        bump_versions = getattr(canvas, "bump_layer_texture_versions", None)
        if callable(bump_versions) and texture_bump_ids:
            bump_versions(texture_bump_ids)
        self._layer_eligibility = current
        return eligible_ids

    # ------------------------------------------------------------------
    # Rendering: eligible layers (§5.5)
    # ------------------------------------------------------------------

    def _render(self) -> ScreenSpaceOverlayRenderCoordinator:
        coordinator = getattr(self, "_render_coordinator", None)
        if not isinstance(coordinator, ScreenSpaceOverlayRenderCoordinator):
            coordinator = ScreenSpaceOverlayRenderCoordinator(
                self,
                entry_factory=OverlayCacheEntry,
                timing_log_interval=_TIMING_LOG_INTERVAL,
            )
            self._render_coordinator = coordinator
        return coordinator

    def render_eligible_layers(
        self,
        painter: QPainter,
        canvas: Any,
        viewport: Any,
        dpr: float,
        *,
        flat: list[Any] | None = None,
        output_offset: tuple[float, float] = (0.0, 0.0),
        canvas_transform: QTransform | None = None,
    ) -> None:
        """Eligible vector layer を screen-space で描画する (§5.5)。

        Pan-only 変化なら translate fast path、その他は再 raster。
        ``_paint_gpu`` の GL 合成終了後 (``endNativePainting`` 以降) に呼ぶ。
        """
        self._render().render_eligible_layers(
            painter,
            canvas,
            viewport,
            dpr,
            flat=flat,
            output_offset=output_offset,
            canvas_transform=canvas_transform,
        )

    # ------------------------------------------------------------------
    # Rendering: preview (§5.6)
    # ------------------------------------------------------------------

    def render_preview(
        self,
        painter: QPainter,
        canvas: Any,
        viewport: Any,
        dpr: float,
        *,
        flat: list[Any] | None = None,
        output_offset: tuple[float, float] = (0.0, 0.0),
        canvas_transform: QTransform | None = None,
    ) -> None:
        """Vector preview (text/path 編集中の差分オブジェクト) を screen-space で描画。

        Base layer が eligible のときのみ実行し、z-order 整合のため毎フレーム
        fresh raster (キャッシュなし)。non-eligible base のときは (A) パスで
        CST に preview が含まれているのでこの pass は skip。

        PD-3: ``_debug_timing_enabled`` が True のとき毎フレーム raster 時間を
        記録し、100 サンプル毎に DEBUG ログへ p50/p95 を出力 (Q-3 実測対象)。
        """
        self._render().render_preview(
            painter,
            canvas,
            viewport,
            dpr,
            flat=flat,
            output_offset=output_offset,
            canvas_transform=canvas_transform,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _render_layer_to_overlay(
        self,
        layer: Any,
        canvas: Any,
        viewport: Any,
        dpr: float,
        reuse_image: QImage | None,
        *,
        output_offset: tuple[float, float] = (0.0, 0.0),
        canvas_transform: QTransform | None = None,
    ) -> tuple[QImage, float]:
        """単一 layer を screen-space QImage にラスタライズ (§5.5.2)。

        size 一致時は既存 QImage を再利用してアロケート抑制。eligible base
        での preview object は除外 (preview pass で別途描画される)。

        PD-3: ``_debug_timing_enabled`` が True のとき時間計測を行い、
        100 サンプル毎に DEBUG ログで p50/p95 を出力する。
        """
        return self._render().render_layer_to_overlay(
            layer,
            canvas,
            viewport,
            dpr,
            reuse_image,
            output_offset=output_offset,
            canvas_transform=canvas_transform,
        )

    def _compute_effective_dpr(
        self, dpr: float, widget_w: int, widget_h: int
    ) -> tuple[float, int, int]:
        """MAX_DIM cap を「画像サイズ縮小」ではなく「DPR 縮小」で適用 (§5.5.1)。

        logical 座標は widget サイズと一致するので、painter.drawImage(0, 0, img)
        が widget 全域を覆う。R-3: 公式 ``eff_dpr = dpr * min(1, MAX_DIM/longest)``。
        """
        target_w = widget_w * dpr
        target_h = widget_h * dpr
        longest = max(target_w, target_h)
        if longest > self.MAX_DIM:
            scale = self.MAX_DIM / longest
            eff_dpr = dpr * scale
        else:
            eff_dpr = dpr
        pw = max(1, round(widget_w * eff_dpr))
        ph = max(1, round(widget_h * eff_dpr))
        return eff_dpr, pw, ph

    def _compute_invariant_key(
        self,
        state: dict[str, Any],
        dpr: float,
        *,
        output_offset: tuple[float, float] = (0.0, 0.0),
        raster_padding: tuple[float, float] = (0.0, 0.0),
    ) -> tuple[float, ...]:
        """zoom/rotation/dpr のみのキー (§5.8)。これが一致なら pan は translate で対応可能。"""
        key: tuple[float, ...] = (
            round(float(state.get("zoom", 1.0)), 6),
            round(float(state.get("rotation", 0.0)), 6),
            round(float(dpr), 3),
        )
        ox, oy = self._normalize_output_offset(output_offset)
        if ox != 0.0 or oy != 0.0:
            key = (*key, round(ox, 6), round(oy, 6))
        px, py = self._normalize_raster_padding(raster_padding)
        if px != 0.0 or py != 0.0:
            return (*key, round(px, 6), round(py, 6))
        return key

    def _overlay_raster_geometry(
        self,
        state: dict[str, Any],
        output_offset: tuple[float, float] = (0.0, 0.0),
    ) -> tuple[int, int, int, int]:
        return screen_space_overlay_pan.overlay_raster_geometry(
            state,
            widget_w_logical=self._widget_w_logical,
            widget_h_logical=self._widget_h_logical,
            output_offset=output_offset,
            min_edge=_MIN_EDGE_OVERSCAN_LOGICAL,
            max_edge=_MAX_EDGE_OVERSCAN_LOGICAL,
            base=_EDGE_OVERSCAN_BASE,
            zoom_factor=_EDGE_OVERSCAN_ZOOM_FACTOR,
        )

    def _edge_overscan_logical(self, state: dict[str, Any]) -> int:
        return screen_space_overlay_pan.edge_overscan_logical(
            state,
            min_edge=_MIN_EDGE_OVERSCAN_LOGICAL,
            max_edge=_MAX_EDGE_OVERSCAN_LOGICAL,
            base=_EDGE_OVERSCAN_BASE,
            zoom_factor=_EDGE_OVERSCAN_ZOOM_FACTOR,
        )

    @staticmethod
    def _normalize_raster_padding(
        raster_padding: tuple[float, float] = (0.0, 0.0),
    ) -> tuple[float, float]:
        return screen_space_overlay_pan.normalize_raster_padding(raster_padding)

    @staticmethod
    def _normalize_output_offset(
        output_offset: tuple[float, float] = (0.0, 0.0),
    ) -> tuple[float, float]:
        return screen_space_overlay_pan.normalize_output_offset(output_offset)

    def _pan_delta_logical(
        self, viewport: Any, prev_state: dict[str, Any], curr_state: dict[str, Any]
    ) -> tuple[float, float]:
        """Pan 差分 (CS 単位) を logical widget 座標に変換 (§5.5.3)。

        viewport.get_transform() は CS → WL 変換なので、center 差分を
        WL 単位にマップするのに zoom と rotation を適用する。zoom/rotation
        は invariant なので curr_state の値を使う。
        """
        del viewport
        return screen_space_overlay_pan.pan_delta_logical(prev_state, curr_state)

    def _entry_covers_pan_delta(
        self,
        entry: OverlayCacheEntry,
        dx: float,
        dy: float,
        *,
        raster_padding: tuple[float, float] = (0.0, 0.0),
    ) -> bool:
        """Return True if a pan-reused overlay image still covers the widget."""

        return screen_space_overlay_pan.entry_covers_pan_delta(
            entry,
            dx,
            dy,
            widget_w_logical=self._widget_w_logical,
            widget_h_logical=self._widget_h_logical,
            raster_padding=raster_padding,
        )

    @staticmethod
    def _transform_with_raster_padding(
        transform: QTransform,
        pad_x: float,
        pad_y: float,
    ) -> QTransform:
        if pad_x == 0.0 and pad_y == 0.0:
            return transform
        correction = QTransform()
        correction.translate(float(pad_x), float(pad_y))
        return transform * correction

    def _draw_entry(
        self,
        painter: QPainter,
        entry: OverlayCacheEntry,
        *,
        dx: float,
        dy: float,
        raster_padding: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        """OverlayCacheEntry を painter に drawImage する。translate 量は logical 座標。"""
        painter.save()
        painter.resetTransform()
        px, py = self._normalize_raster_padding(raster_padding)
        self._draw_image_cropped_to_nonnegative_target(
            painter,
            entry.image,
            x=float(dx) - px,
            y=float(dy) - py,
        )
        painter.restore()

    @staticmethod
    def _draw_image_cropped_to_nonnegative_target(
        painter: QPainter,
        image: QImage,
        *,
        x: float,
        y: float,
    ) -> None:
        """Draw a DPR-aware image without passing negative target coordinates to Qt.

        Native transparent child windows on Windows can clip negative target
        ``drawImage`` calls against an ancestor/global backing-store region.
        Cropping the source rect keeps the visual result identical while the
        destination top-left stays inside the overlay widget.
        """
        try:
            dpr = float(image.devicePixelRatio())
        except (TypeError, ValueError):
            dpr = 1.0
        if not math.isfinite(dpr) or dpr <= 0.0:
            dpr = 1.0

        logical_w = float(image.width()) / dpr
        logical_h = float(image.height()) / dpr
        if logical_w <= 0.0 or logical_h <= 0.0:
            return

        target_x = max(0.0, float(x))
        target_y = max(0.0, float(y))
        source_x_logical = max(0.0, -float(x))
        source_y_logical = max(0.0, -float(y))
        draw_w = logical_w - source_x_logical
        draw_h = logical_h - source_y_logical
        if draw_w <= 0.0 or draw_h <= 0.0:
            return

        target = QRectF(target_x, target_y, draw_w, draw_h)
        source = QRectF(
            source_x_logical * dpr,
            source_y_logical * dpr,
            draw_w * dpr,
            draw_h * dpr,
        )
        painter.drawImage(target, image, source)

    def _prune_stale_entries(self, active_ids: set[str]) -> None:
        """active_ids に含まれない layer の entry を解放する。"""
        stale = [lid for lid in self._entries if lid not in active_ids]
        for lid in stale:
            del self._entries[lid]

    def _find_layer_by_id(
        self, canvas: Any, layer_id: str, *, flat: list[Any] | None = None
    ) -> Any | None:
        flat = self._ensure_flat(canvas, flat)
        for layer in flat:
            if getattr(layer, "id", None) == layer_id:
                return layer
        return None

    def _maybe_prune_qpath_cache(self, canvas: Any, layer: Any) -> None:
        """SSO 経路で stale qpath を prune する (R-6)。

        SSO 経路は ``_rasterize_vector_gpu`` を return True で skip するため、
        canvas_service 側の ``_qpath_cache.clear()`` / ``_prune_qpath_cache()``
        が走らない。``id(vec)`` reuse による wrong-paths バグを防ぐため
        SSO manager 側で対応する。

        O(N_objects) のため、毎フレームではなく ``texture_version`` 変化時のみ呼ぶ
        (``render_eligible_layers`` 内で管理)。

        Q-9 (Phase 2 候補): 現状は ``id()`` ベース。GC 後に Python が同じアドレスを
        別オブジェクトに再割当てした場合 stale 残留する理論的リスクがあるが、本
        prune で実害は回避済み。Phase 2 で WeakKeyDictionary 化を検討。
        """
        cache = getattr(canvas, "_qpath_cache", None)
        if cache is None:
            return
        live_ids = {id(obj) for obj in getattr(layer, "vector_objects", [])}
        preview = getattr(canvas, "_vector_preview", None)
        if preview is not None:
            live_ids.add(id(preview))
        stale = [k for k in cache if k not in live_ids]
        for k in stale:
            cache.pop(k, None)
