"""Render coordinator for Screen-Space Vector Overlay cache paths."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QTransform


class ScreenSpaceOverlayTimingDiagnostics:
    """Record SSO render timings and periodic summaries."""

    def __init__(self, manager: Any, *, timing_log_interval: int) -> None:
        self._manager = manager
        self._timing_log_interval = timing_log_interval

    def begin(self) -> float | None:
        return time.perf_counter() if self._manager._debug_timing_enabled else None

    def record_preview(self, start: float | None) -> None:
        if start is None:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self._manager._preview_render_times.append(elapsed_ms)
        self._manager._preview_sample_counter += 1
        if self._manager._preview_sample_counter % self._timing_log_interval == 0:
            self._manager._log_timing_summary(
                "preview",
                list(self._manager._preview_render_times),
            )

    def record_eligible(self, start: float | None) -> None:
        if start is None:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self._manager._eligible_render_times.append(elapsed_ms)
        self._manager._eligible_sample_counter += 1
        if self._manager._eligible_sample_counter % self._timing_log_interval == 0:
            self._manager._log_timing_summary(
                "eligible",
                list(self._manager._eligible_render_times),
            )


class ScreenSpaceOverlayPreviewRenderer:
    """Render the active vector preview into the SSO raster cache."""

    def __init__(
        self,
        manager: Any,
        *,
        timing: ScreenSpaceOverlayTimingDiagnostics,
    ) -> None:
        self._manager = manager
        self._timing = timing

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
        if not self._manager._enabled:
            return
        preview = getattr(canvas, "_vector_preview", None)
        if not preview:
            return

        preview_lid = getattr(canvas, "_vector_preview_layer_id", None)
        flat = self._manager._ensure_flat(canvas, flat)
        base_layer = (
            self._manager._find_layer_by_id(canvas, preview_lid, flat=flat) if preview_lid else None
        )
        if base_layer is None:
            return
        if not self._manager.is_eligible(
            base_layer,
            self._manager.above_layers_of(canvas, base_layer.id, flat=flat),
        ):
            return

        if self._manager._collect_diagnostics():
            self._manager._preview_render_count += 1
        t0 = self._timing.begin()

        normalized_output_offset = self._manager._normalize_output_offset(output_offset)
        state = viewport.get_state()
        raster_pad_x, raster_pad_y, logical_w, logical_h = self._manager._overlay_raster_geometry(
            state,
            normalized_output_offset,
        )
        eff_dpr, pw, ph = self._manager._compute_effective_dpr(dpr, logical_w, logical_h)
        img = self._manager._preview_image
        if img is None or img.width() != pw or img.height() != ph:
            img = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
            self._manager._preview_image = img
        img.fill(Qt.GlobalColor.transparent)
        img.setDevicePixelRatio(eff_dpr)

        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        base_transform = (
            canvas_transform if canvas_transform is not None else viewport.get_transform()
        )
        p.setTransform(
            self._manager._transform_with_raster_padding(
                base_transform,
                raster_pad_x,
                raster_pad_y,
            )
        )
        selection = getattr(canvas, "_vector_selection", None)
        draw_fn = getattr(canvas, "_draw_vector_objects", None)
        if callable(draw_fn):
            draw_fn(p, base_layer.id, [preview], selection)
        p.end()

        painter.save()
        painter.resetTransform()
        self._manager._draw_image_cropped_to_nonnegative_target(
            painter,
            img,
            x=-float(raster_pad_x),
            y=-float(raster_pad_y),
        )
        painter.restore()

        self._timing.record_preview(t0)


class ScreenSpaceOverlayRenderCoordinator:
    """Coordinate SSO eligible-layer and preview raster passes."""

    def __init__(
        self,
        manager: Any,
        *,
        entry_factory: Callable[..., Any],
        timing_log_interval: int,
    ) -> None:
        self._manager = manager
        self._entry_factory = entry_factory
        self._timing_log_interval = timing_log_interval
        self._timing = ScreenSpaceOverlayTimingDiagnostics(
            manager,
            timing_log_interval=timing_log_interval,
        )
        self._preview_renderer = ScreenSpaceOverlayPreviewRenderer(
            manager,
            timing=self._timing,
        )

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
        collect_diagnostics = self._manager._collect_diagnostics()
        if not self._manager._enabled:
            self._manager._record_eligible_layer_count(collect_diagnostics, 0)
            return
        if self._manager._widget_w_logical <= 0 or self._manager._widget_h_logical <= 0:
            self._manager._record_eligible_layer_count(collect_diagnostics, 0)
            return

        eligible = self._manager.collect_eligible(canvas, flat=flat)
        self._manager._record_eligible_layer_count(collect_diagnostics, len(eligible))
        active_ids = {layer.id for layer in eligible}
        self._manager._prune_stale_entries(active_ids)
        if not eligible:
            return

        state = viewport.get_state()
        normalized_output_offset = self._manager._normalize_output_offset(output_offset)
        raster_pad_x, raster_pad_y, _, _ = self._manager._overlay_raster_geometry(
            state,
            normalized_output_offset,
        )
        invariant_key = self._manager._compute_invariant_key(
            state,
            dpr,
            output_offset=normalized_output_offset,
            raster_padding=(raster_pad_x, raster_pad_y),
        )
        pan_key = (
            round(float(state.get("center_x", 0.0)), 6),
            round(float(state.get("center_y", 0.0)), 6),
        )

        for layer in eligible:
            self._render_or_reuse_eligible_layer(
                painter,
                canvas,
                viewport,
                dpr,
                layer,
                state,
                invariant_key,
                pan_key,
                collect_diagnostics=collect_diagnostics,
                raster_padding=(raster_pad_x, raster_pad_y),
                output_offset=normalized_output_offset,
                canvas_transform=canvas_transform,
            )

    def _render_or_reuse_eligible_layer(
        self,
        painter: QPainter,
        canvas: Any,
        viewport: Any,
        dpr: float,
        layer: Any,
        state: dict[str, Any],
        invariant_key: tuple[float, ...],
        pan_key: tuple[float, ...],
        *,
        collect_diagnostics: bool,
        raster_padding: tuple[float, float],
        output_offset: tuple[float, float],
        canvas_transform: QTransform | None,
    ) -> None:
        lid = layer.id
        entry = self._manager._entries.get(lid)
        layer_ver = int(getattr(layer, "texture_version", 0))
        raster_pad_x, raster_pad_y = raster_padding

        if (
            entry is not None
            and entry.invariant_key == invariant_key
            and entry.rendered_layer_version == layer_ver
        ):
            if entry.pan_key == pan_key:
                self._manager._record_eligible_reuse(collect_diagnostics)
                self._manager._draw_entry(
                    painter,
                    entry,
                    dx=0.0,
                    dy=0.0,
                    raster_padding=(raster_pad_x, raster_pad_y),
                )
                return
            dx, dy = self._manager._pan_delta_logical(viewport, entry.viewport_state, state)
            if self._manager._entry_covers_pan_delta(
                entry,
                dx,
                dy,
                raster_padding=(raster_pad_x, raster_pad_y),
            ):
                self._manager._record_eligible_reuse(collect_diagnostics, pan_reuse=True)
                self._manager._draw_entry(
                    painter,
                    entry,
                    dx=dx,
                    dy=dy,
                    raster_padding=(raster_pad_x, raster_pad_y),
                )
                return

        reuse = entry.image if entry is not None else None
        if entry is None or entry.rendered_layer_version != layer_ver:
            self._manager._maybe_prune_qpath_cache(canvas, layer)
        img, eff_dpr = self.render_layer_to_overlay(
            layer,
            canvas,
            viewport,
            dpr,
            reuse,
            output_offset=output_offset,
            canvas_transform=canvas_transform,
        )
        self._manager._record_eligible_render(collect_diagnostics)
        new_entry = self._entry_factory(
            layer_id=lid,
            image=img,
            invariant_key=invariant_key,
            pan_key=pan_key,
            viewport_state=dict(state),
            rendered_layer_version=layer_ver,
            effective_dpr=eff_dpr,
        )
        self._manager._entries[lid] = new_entry
        self._manager._draw_entry(
            painter,
            new_entry,
            dx=0.0,
            dy=0.0,
            raster_padding=(raster_pad_x, raster_pad_y),
        )

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
        self._preview_renderer.render_preview(
            painter,
            canvas,
            viewport,
            dpr,
            flat=flat,
            output_offset=output_offset,
            canvas_transform=canvas_transform,
        )

    def render_layer_to_overlay(
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
        t0 = self._timing.begin()

        normalized_output_offset = self._manager._normalize_output_offset(output_offset)
        state = viewport.get_state()
        raster_pad_x, raster_pad_y, logical_w, logical_h = self._manager._overlay_raster_geometry(
            state,
            normalized_output_offset,
        )
        eff_dpr, pw, ph = self._manager._compute_effective_dpr(dpr, logical_w, logical_h)

        if reuse_image is not None and reuse_image.width() == pw and reuse_image.height() == ph:
            img = reuse_image
            img.fill(Qt.GlobalColor.transparent)
        else:
            img = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
        img.setDevicePixelRatio(eff_dpr)

        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        base_transform = (
            canvas_transform if canvas_transform is not None else viewport.get_transform()
        )
        p.setTransform(
            self._manager._transform_with_raster_padding(
                base_transform,
                raster_pad_x,
                raster_pad_y,
            )
        )

        objects = list(getattr(layer, "vector_objects", []))
        preview = getattr(canvas, "_vector_preview", None)
        preview_lid = getattr(canvas, "_vector_preview_layer_id", None)
        if preview and (preview_lid is None or preview_lid == layer.id):
            preview_id = getattr(preview, "id", None)
            objects = [o for o in objects if getattr(o, "id", None) != preview_id]

        selection = getattr(canvas, "_vector_selection", None)
        draw_fn = getattr(canvas, "_draw_vector_objects", None)
        if callable(draw_fn) and objects:
            draw_fn(p, layer.id, objects, selection)

        p.end()

        self._timing.record_eligible(t0)

        return img, eff_dpr
