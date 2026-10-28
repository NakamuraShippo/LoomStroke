"""Native wgpu surface lifecycle coordinator for CanvasViewportHost."""

from __future__ import annotations

import ctypes
import logging
import os
import time
from collections.abc import Callable
from typing import Any, cast

from PySide6.QtCore import QObject, QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

from loomstroke.ui._dock_safe import is_valid as _is_qt_object_valid

from .native_surface_visibility import native_surface_obscured
from .native_window_capture import capture_composed_native_surface

logger = logging.getLogger(__name__)


def _flush_desktop_composition() -> bool:
    """Wait for queued Windows surface updates before sampling live pixels."""
    if os.name != "nt":
        return False
    try:
        result = ctypes.windll.dwmapi.DwmFlush()
    except (AttributeError, OSError):
        logger.debug("Desktop composition synchronization unavailable", exc_info=True)
        return False
    if result != 0:
        logger.debug("Desktop composition synchronization failed: %#x", result & 0xFFFFFFFF)
        return False
    return True


class _NativeProbeCancelled(Exception):
    """Unwind a probe before releasing a presenter invalidated by nested events."""


class NativeVisualProbeCoordinator:
    """Coordinate native visual probe scheduling, retries, and fallback."""

    def __init__(
        self,
        host: Any,
        *,
        timer_single_shot: Callable[[int, Callable[[], None]], None],
        not_ready_reason: str,
        transient_mismatch_reason: str,
        deferred_retries: int,
        present_error_types: tuple[type[BaseException], ...],
        capture_attempts: int,
        capture_settle_seconds: float,
        smoke_clear_rgba: tuple[float, ...],
    ) -> None:
        self._host = host
        self._timer_single_shot = timer_single_shot
        self._not_ready_reason = not_ready_reason
        self._transient_mismatch_reason = transient_mismatch_reason
        self._deferred_retries = deferred_retries
        self._present_error_types = present_error_types
        self._capture_attempts = capture_attempts
        self._capture_settle_seconds = capture_settle_seconds
        self._smoke_clear_rgba = smoke_clear_rgba
        self._cancelled = False

    def reset_state(self) -> None:
        self._cancelled = False
        self._host._native_visual_probe_passed = False
        self._host._native_visual_probe_pending = False
        self._host._native_visual_probe_running = False
        self._host._native_visual_probe_retry_count = 0
        self._host._native_scene_presented_after_probe = False

    def cancel(self) -> None:
        self._cancelled = True

    def _check_cancelled(self) -> None:
        if self._cancelled:
            raise _NativeProbeCancelled

    def _process_probe_events(self) -> None:
        QApplication.processEvents()
        self._check_cancelled()

    def schedule(self) -> None:
        if not self._host.native_surface_active:
            return
        if (
            self._host._native_visual_probe_pending
            or self._host._native_visual_probe_passed
            or self._host._native_visual_probe_running
        ):
            return
        self._host._native_visual_probe_pending = True
        self._timer_single_shot(0, self._host._run_deferred_native_visual_probe)

    def run_deferred(self) -> None:
        self._host._native_visual_probe_pending = False
        # A queued probe can outlive File Close / hide. Once an in-progress
        # probe has settled, wait for the next show instead of exhausting
        # readiness retries and disabling native for the whole session.
        if (
            not _is_qt_object_valid(self._host)
            or not _is_qt_object_valid(self._host._native_surface)
            or not _is_qt_object_valid(self._host._overlay_surface)
            or not self._host.native_surface_active
            or (not self._host.isVisible() and not self._host._native_visual_probe_running)
        ):
            return
        if self._host._native_visual_probe_running:
            self._host._native_visual_probe_pending = True
            self._timer_single_shot(16, self._host._run_deferred_native_visual_probe)
            return
        if not self._host._native_surface_ready_for_visual_probe():
            self._retry_or_fallback(self._not_ready_reason, retry_limit=self._deferred_retries)
            return
        presenter = self._host._ensure_native_presenter()
        if presenter is None:
            self._host._activate_legacy_fallback(
                self._host._native_disabled_reason or "wgpu native surface unavailable"
            )
            return
        reason = self._host._run_native_visual_probe(presenter)
        if reason is not None:
            if reason in {
                self._not_ready_reason,
                self._transient_mismatch_reason,
            }:
                if self._defer_obscured_probe(reason) or self._retry_or_fallback(
                    reason, retry_limit=self._deferred_retries
                ):
                    return
            else:
                self._host._activate_legacy_fallback(reason)
                return
        self._host._native_visual_probe_retry_count = 0
        self._host._request_native_present(force_full_recompose=True)

    def _defer_obscured_probe(self, reason: str) -> bool:
        if reason != self._not_ready_reason or not native_surface_obscured(
            self._host._native_surface
        ):
            return False
        # Foreign pixels cannot diagnose our surface. Leave the failure
        # budget intact and validate when the desktop becomes visible.
        self._host._native_visual_probe_pending = True
        self._timer_single_shot(250, self._host._run_deferred_native_visual_probe)
        return True

    def run_probe_body(self, presenter: Any) -> str | None:
        if self._host._native_visual_probe_running:
            return self._not_ready_reason
        if getattr(self._host, "_last_native_visual_probe_status", "") == "deferred" and (
            native_surface_obscured(self._host._native_surface)
        ):
            # The desktop still belongs to another app. Poll visibility only;
            # repeated activation and GPU/capture work would disturb its use.
            self.record_visual_probe(
                presenter,
                status="deferred",
                reason=self._not_ready_reason,
                capture_supported=getattr(
                    self._host, "_native_live_surface_capture_supported", False
                ),
            )
            return self._not_ready_reason
        self._host._native_visual_probe_running = True
        self._host._native_scene_presented_after_probe = False
        overlay_was_visible = (
            _is_qt_object_valid(self._host._overlay_surface)
            and self._host._overlay_surface.isVisible()
        )
        hidden_chrome: list[QWidget] = []
        try:
            root = self._host.window()
            if _is_qt_object_valid(root):
                root.raise_()
                root.activateWindow()
            if _is_qt_object_valid(self._host._native_surface):
                self._host._native_surface.show()
                self._host._native_surface.raise_()
            if overlay_was_visible:
                # Prefer HWND capture while the Qt overlay remains visible so
                # the smoke frame does not flash through during document setup.
                self._host._overlay_surface.raise_()
                self._process_probe_events()
                reason = self.run_probe_without_overlay(
                    presenter,
                    capture_attempts=1,
                )
                if reason is None:
                    return None
                if reason.startswith("wgpu native visual probe present failed"):
                    return reason
            if overlay_was_visible:
                self._host._overlay_surface.hide()
            hidden_chrome = self.hide_overlapping_top_level_chrome_for_probe()
            self._process_probe_events()
            return self.run_probe_without_overlay(presenter)
        finally:
            self._host._native_visual_probe_running = False
            self.restore_top_level_chrome_after_probe(hidden_chrome)
            if (
                overlay_was_visible
                and self._host.native_surface_active
                and getattr(self._host, "isVisible", lambda: True)()
                and _is_qt_object_valid(self._host._overlay_surface)
            ):
                self._host._overlay_surface.show()
                self._host._overlay_surface.raise_()
                self._host._raise_overlapping_top_level_chrome()

    def run_probe_without_overlay(
        self,
        presenter: Any,
        *,
        capture_attempts: int | None = None,
    ) -> str | None:
        capture: QImage | None = None
        attempts = self._capture_attempts if capture_attempts is None else max(1, capture_attempts)
        for attempt in range(attempts):
            self._check_cancelled()
            if attempt > 0:
                self._process_probe_events()
            try:
                presenter.present_smoke_frame()
            except self._present_error_types as exc:
                reason = f"wgpu native visual probe present failed: {exc}"
                self.record_visual_probe(
                    presenter,
                    status="failed",
                    reason=reason,
                    capture_supported=False,
                )
                return reason
            self._process_probe_events()
            flush_native_surface = getattr(self._host, "_flush_native_surface_window", None)
            if callable(flush_native_surface):
                flush_native_surface()
                self._process_probe_events()
            composition_flushed = _flush_desktop_composition()
            if attempt > 0 and not composition_flushed:
                time.sleep(self._capture_settle_seconds)
                self._process_probe_events()
            capture = self._host._capture_native_surface_image()
            window_capture = self._host._capture_native_surface_window_image()
            obscured = native_surface_obscured(getattr(self._host, "_native_surface", None))
            matching_capture = self._matching_smoke_capture(
                (window_capture,) if obscured else (capture, window_capture)
            )
            if matching_capture is None:
                matching_capture = self._matching_smoke_capture(
                    (capture_composed_native_surface(getattr(self._host, "_native_surface", None)),)
                )
            if matching_capture is not None:
                capture = matching_capture
                self._host._native_visual_probe_passed = True
                self.record_visual_probe(
                    presenter,
                    status="passed",
                    reason=None,
                    capture_supported=True,
                )
                return None
            if obscured:
                self.record_visual_probe(
                    presenter,
                    status="deferred",
                    reason=self._not_ready_reason,
                    capture_supported=capture is not None and not capture.isNull(),
                )
                return self._not_ready_reason
        if capture is None or capture.isNull():
            reason = "live native surface capture unsupported"
            self.record_visual_probe(
                presenter,
                status="unsupported",
                reason=reason,
                capture_supported=False,
            )
            return reason
        if capture.width() < 16 or capture.height() < 16:
            reason = f"live native surface capture too small: {capture.width()}x{capture.height()}"
            self.record_visual_probe(
                presenter,
                status="failed",
                reason=reason,
                capture_supported=True,
            )
            return reason
        reason = self._transient_mismatch_reason
        self.record_visual_probe(
            presenter,
            status="failed",
            reason=reason,
            capture_supported=True,
        )
        return reason

    def _matching_smoke_capture(self, captures: tuple[QImage | None, ...]) -> QImage | None:
        return next(
            (
                candidate
                for candidate in captures
                if candidate is not None
                and not candidate.isNull()
                and candidate.width() >= 16
                and candidate.height() >= 16
                and self._image_matches_native_smoke_frame(candidate)
            ),
            None,
        )

    def record_visual_probe(
        self,
        presenter: Any,
        *,
        status: str,
        reason: str | None,
        capture_supported: bool,
    ) -> None:
        self._host._last_native_visual_probe_status = status
        self._host._last_native_visual_probe_reason = reason
        self._host._native_live_surface_capture_supported = capture_supported
        presenter.record_visual_probe(
            status=status,
            reason=reason,
            capture_supported=capture_supported,
        )

    def hide_overlapping_top_level_chrome_for_probe(self) -> list[QWidget]:
        root = self._host.window()
        if root is None:
            return []
        surface_rect = QRect(
            self._host._native_surface.mapToGlobal(self._host._native_surface.rect().topLeft()),
            self._host._native_surface.size(),
        )
        hidden: list[QWidget] = []
        for widget in QApplication.topLevelWidgets():
            if widget is root or widget is self._host:
                continue
            if not widget.isVisible() or not widget.isWindow():
                continue
            if not bool(widget.windowFlags() & Qt.WindowType.Tool):
                continue
            if not self.widget_belongs_to_root(widget, root):
                continue
            if not widget.frameGeometry().intersects(surface_rect):
                continue
            widget.hide()
            hidden.append(widget)
        return hidden

    def restore_top_level_chrome_after_probe(self, widgets: list[QWidget]) -> None:
        for widget in widgets:
            if not _is_qt_object_valid(widget):
                continue
            widget.show()
            widget.raise_()

    @staticmethod
    def widget_belongs_to_root(widget: QWidget, root: QWidget) -> bool:
        current: QObject | None = widget
        while current is not None:
            if current is root:
                return True
            current = current.parent()
        return False

    def capture_native_surface_image(self) -> QImage | None:
        top_left = self._host._native_surface.mapToGlobal(
            self._host._native_surface.rect().topLeft()
        )
        screen = self._screen_for_native_surface_point(top_left)
        if screen is None:
            return None
        pixmap = screen.grabWindow(
            0,
            *self._screen_local_capture_rect(
                screen,
                top_left,
                self._host._native_surface.width(),
                self._host._native_surface.height(),
            ),
        )
        if pixmap is None or pixmap.isNull():
            pixmap = self._host._native_surface.grab()
        if pixmap is None or pixmap.isNull():
            return None
        image = pixmap.toImage()
        return image if isinstance(image, QImage) else None

    def _screen_local_capture_rect(
        self,
        screen: Any,
        top_left: Any,
        width: int,
        height: int,
    ) -> tuple[int, int, int, int]:
        x = int(top_left.x())
        y = int(top_left.y())
        geometry = getattr(screen, "geometry", None)
        if callable(geometry):
            screen_geometry = geometry()
            x -= int(screen_geometry.x())
            y -= int(screen_geometry.y())
        return (
            x,
            y,
            max(1, int(width)),
            max(1, int(height)),
        )

    def capture_native_surface_window_image(self) -> QImage | None:
        top_left = self._host._native_surface.mapToGlobal(
            self._host._native_surface.rect().topLeft()
        )
        screen = self._screen_for_native_surface_point(top_left)
        if screen is None:
            return None
        pixmap = screen.grabWindow(int(self._host._native_surface.winId()))
        if pixmap is None or pixmap.isNull():
            return None
        image = pixmap.toImage()
        return image if isinstance(image, QImage) else None

    def _screen_for_native_surface_point(self, top_left: Any) -> Any | None:
        screen_at = getattr(QApplication, "screenAt", None)
        if callable(screen_at):
            screen = screen_at(top_left)
            if self._screen_can_capture(screen):
                return screen
        handle = self._host._native_surface.windowHandle()
        if handle is not None:
            screen = handle.screen()
            if self._screen_can_capture(screen):
                return screen
        screen = QApplication.primaryScreen()
        return screen if self._screen_can_capture(screen) else None

    @staticmethod
    def _screen_can_capture(screen: Any) -> bool:
        # PySide can return a cached QObject wrapper for a QScreen pointer.
        # It is non-null but has no screen API. Reacquire through the other
        # existing providers instead of aborting the startup visual probe.
        return _is_qt_object_valid(screen) and callable(getattr(screen, "grabWindow", None))

    def _image_matches_native_smoke_frame(self, image: QImage) -> bool:
        return self.image_matches_native_smoke_frame(image, self._smoke_clear_rgba)

    @staticmethod
    def image_matches_native_smoke_frame(
        image: QImage,
        smoke_clear_rgba: tuple[float, ...],
    ) -> bool:
        expected = tuple(
            round(NativeVisualProbeCoordinator.linear_to_srgb(channel) * 255.0)
            for channel in smoke_clear_rgba[:3]
        )
        points = (
            (image.width() // 2, image.height() // 2),
            (max(0, image.width() // 4), max(0, image.height() // 4)),
            (max(0, (image.width() * 3) // 4), max(0, (image.height() * 3) // 4)),
        )
        for x, y in points:
            color = image.pixelColor(int(x), int(y))
            if color.alpha() <= 0:
                continue
            distance = (
                abs(color.red() - expected[0])
                + abs(color.green() - expected[1])
                + abs(color.blue() - expected[2])
            )
            if distance <= 96:
                return True
        return False

    @staticmethod
    def linear_to_srgb(value: float) -> float:
        clamped = max(0.0, min(1.0, float(value)))
        if clamped <= 0.0031308:
            return 12.92 * clamped
        return float(1.055 * (clamped ** (1.0 / 2.4)) - 0.055)

    def _retry_or_fallback(self, reason: str, *, retry_limit: int) -> bool:
        if self._host._native_visual_probe_retry_count < retry_limit:
            self._host._native_visual_probe_retry_count += 1
            self._host._native_visual_probe_pending = True
            self._timer_single_shot(16, self._host._run_deferred_native_visual_probe)
            return True
        self._host._activate_legacy_fallback(reason)
        return False


class CanvasViewportNativeLifecycleCoordinator:
    """Coordinate native presenter lifetime and visual probe scheduling."""

    def __init__(
        self,
        host: Any,
        *,
        presenter_factory: Callable[[], Any],
        timer_single_shot: Callable[[int, Callable[[], None]], None],
        unavailable_error_type: type[BaseException],
        backend_error_type: type[BaseException],
        not_ready_reason: str,
        transient_mismatch_reason: str,
        deferred_retries: int,
        capture_attempts: int,
        capture_settle_seconds: float,
        smoke_clear_rgba: tuple[float, ...],
    ) -> None:
        self._host = host
        self._presenter_factory = presenter_factory
        self._timer_single_shot = timer_single_shot
        self._unavailable_error_type = unavailable_error_type
        self._backend_error_type = backend_error_type
        self._not_ready_reason = not_ready_reason
        self._transient_mismatch_reason = transient_mismatch_reason
        self._probe_depth = 0
        self._release_pending = False
        self._visual_probe = NativeVisualProbeCoordinator(
            host,
            timer_single_shot=timer_single_shot,
            not_ready_reason=not_ready_reason,
            transient_mismatch_reason=transient_mismatch_reason,
            deferred_retries=deferred_retries,
            present_error_types=(backend_error_type, unavailable_error_type),
            capture_attempts=capture_attempts,
            capture_settle_seconds=capture_settle_seconds,
            smoke_clear_rgba=smoke_clear_rgba,
        )

    def ensure_presenter(self) -> Any | None:
        if (
            self._host._native_shutting_down
            or not self._host._native_requested
            or self._host._native_disabled_reason is not None
        ):
            return None
        if self._host._native_presenter is not None:
            return self._host._native_presenter
        try:
            self._host._native_presenter = self._presenter_factory()
        except self._unavailable_error_type as exc:
            self._host._native_disabled_reason = f"wgpu unavailable: {exc}"
            self._host._native_fallback_count += 1
            return None
        except self._backend_error_type as exc:
            self._host._native_disabled_reason = f"wgpu native init failed: {exc}"
            self._host._native_fallback_count += 1
            return None
        logger.info("wgpu native surface presenter initialized")
        return self._host._native_presenter

    def release_presenter(self) -> None:
        if self._probe_depth:
            # Qt events dispatched by the visual probe may hide, close, or
            # change the surface. Its stack still owns the old presenter.
            self._release_pending = True
            self._visual_probe.cancel()
            return
        self._release_pending = False
        self._visual_probe.reset_state()
        self._host._native_overlay_flat_layers = ()
        self._host._native_present_pending = False
        self._host._native_force_next_present = False
        self._host._native_flush_after_present = False
        if self._host._native_presenter is None:
            return
        try:
            self._host._native_presenter.release()
        except Exception:
            logger.debug("Failed to release wgpu native presenter", exc_info=True)
        self._host._native_presenter = None

    def present_for_readback(self) -> str | None:
        reason = cast(
            "str | None",
            self._host._present_native_surface(force_full_recompose=True),
        )
        retryable_reasons = {
            self._not_ready_reason,
            self._transient_mismatch_reason,
        }
        if reason not in retryable_reasons or not self._host.isVisible():
            return reason
        for _ in range(4):
            QApplication.processEvents()
            if not self._host.native_surface_active:
                return cast("str | None", self._host._native_disabled_reason)
            if not self._host._native_surface_ready_for_visual_probe():
                continue
            reason = cast(
                "str | None",
                self._host._present_native_surface(force_full_recompose=True),
            )
            if reason not in retryable_reasons:
                return reason
        return reason

    def schedule_visual_probe(self) -> None:
        self._visual_probe.schedule()

    def run_deferred_visual_probe(self) -> None:
        self._visual_probe.run_deferred()

    def run_visual_probe(self, presenter: Any) -> str | None:
        return self._run_probe(self._visual_probe.run_probe_body, presenter)

    def run_visual_probe_without_overlay(self, presenter: Any) -> str | None:
        return self._run_probe(self._visual_probe.run_probe_without_overlay, presenter)

    def _run_probe(self, probe: Callable[[Any], str | None], presenter: Any) -> str | None:
        self._probe_depth += 1
        try:
            reason = probe(presenter)
        except _NativeProbeCancelled:
            reason = self._not_ready_reason
        finally:
            self._probe_depth -= 1
            invalidated = self._release_pending
            if not self._probe_depth and invalidated:
                self.release_presenter()
        # A lifecycle event in chrome restoration also invalidates an otherwise
        # successful probe. Never let callers render with that old presenter.
        return self._not_ready_reason if invalidated else reason

    def record_visual_probe(
        self,
        presenter: Any,
        *,
        status: str,
        reason: str | None,
        capture_supported: bool,
    ) -> None:
        self._visual_probe.record_visual_probe(
            presenter,
            status=status,
            reason=reason,
            capture_supported=capture_supported,
        )

    def hide_overlapping_top_level_chrome_for_probe(self) -> list[QWidget]:
        return self._visual_probe.hide_overlapping_top_level_chrome_for_probe()

    def restore_top_level_chrome_after_probe(self, widgets: list[QWidget]) -> None:
        self._visual_probe.restore_top_level_chrome_after_probe(widgets)

    def capture_native_surface_image(self) -> QImage | None:
        return self._visual_probe.capture_native_surface_image()

    def capture_native_surface_window_image(self) -> QImage | None:
        return self._visual_probe.capture_native_surface_window_image()
