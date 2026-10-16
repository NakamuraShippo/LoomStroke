"""Image and view action coordination for MainWindow."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from contextlib import suppress
from math import isfinite
from typing import Any, cast

from ..core.book_layout_service import BookLayoutService
from ..core.book_spread_layout import BookLayout
from .dialogs.comic_book_settings_dialog import ComicBookSettingsDialog

logger = logging.getLogger(__name__)


class MainWindowImageViewActionsCoordinator:
    """Coordinate image menu actions and view runtime state updates."""

    def __init__(
        self,
        window: Any,
        *,
        canvas_resize_dialog: Callable[[], Any],
        image_properties_dialog: Callable[[], Any],
        dialog_type: Callable[[], Any],
        message_box: Callable[[], Any],
    ) -> None:
        self._window = window
        self._canvas_resize_dialog = canvas_resize_dialog
        self._image_properties_dialog = image_properties_dialog
        self._dialog_type = dialog_type
        self._message_box = message_box

    def _canvas_service(self) -> Any | None:
        host_api = getattr(self._window, "_host_api", None)
        return getattr(host_api, "canvas", None) if host_api else None

    def on_canvas_resize(self) -> None:
        width, height = self._window._current_canvas_size()
        dialog = self._canvas_resize_dialog()(width, height, parent=self._window)
        if dialog.exec() != self._dialog_type().DialogCode.Accepted:
            return

        new_width, new_height = dialog.canvas_size()
        anchor = dialog.anchor()
        canvas = self._canvas_service()
        if not canvas or not hasattr(canvas, "update_canvas_config"):
            self._message_box().warning(
                self._window,
                self._window._ts("Canvas Resize"),
                self._window._ts("Canvas service is not available."),
            )
            return

        try:
            update_fn = cast(Callable[..., Any], canvas.update_canvas_config)
            supports_anchor = False
            try:
                supports_anchor = "anchor" in inspect.signature(update_fn).parameters
            except (TypeError, ValueError):
                supports_anchor = False
            if supports_anchor:
                update_fn(width=new_width, height=new_height, anchor=anchor)
            else:
                update_fn(width=new_width, height=new_height)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Canvas resize failed: %s", exc)
            self._message_box().warning(
                self._window,
                self._window._ts("Canvas Resize"),
                self._window._ts("Failed to resize canvas."),
            )
            return

        canvas_widget = getattr(self._window, "canvas", None)
        if canvas_widget is not None and hasattr(canvas_widget, "set_canvas_size"):
            with suppress(Exception):
                canvas_widget.set_canvas_size(new_width, new_height)

        self._window.statusBar().showMessage(f"Canvas resized to {new_width}x{new_height}", 3000)

    def on_image_flip_horizontal(self) -> None:
        self._window._apply_image_flip(horizontal=True, vertical=False)

    def on_image_flip_vertical(self) -> None:
        self._window._apply_image_flip(horizontal=False, vertical=True)

    def apply_image_flip(self, *, horizontal: bool, vertical: bool) -> None:
        canvas = self._canvas_service()
        if not canvas or not hasattr(canvas, "flip_image"):
            self._message_box().warning(
                self._window,
                self._window._ts("Flip Canvas"),
                self._window._ts("Canvas service is not available."),
            )
            return
        try:
            ok = bool(canvas.flip_image(horizontal=horizontal, vertical=vertical))
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Canvas flip failed: %s", exc)
            self._message_box().warning(
                self._window,
                self._window._ts("Flip Canvas"),
                self._window._ts("Failed to flip canvas."),
            )
            return
        if not ok:
            return
        if horizontal and not vertical:
            msg = "Canvas flipped horizontally"
        elif vertical and not horizontal:
            msg = "Canvas flipped vertically"
        else:
            msg = "Canvas flipped"
        self._window.statusBar().showMessage(self._window._ts(msg), 3000)

    def on_image_rotate_cw(self) -> None:
        self._window._apply_image_rotate(90)

    def on_image_rotate_ccw(self) -> None:
        self._window._apply_image_rotate(-90)

    def on_image_rotate_180(self) -> None:
        self._window._apply_image_rotate(180)

    def apply_image_rotate(self, angle: int) -> None:
        canvas = self._canvas_service()
        if not canvas or not hasattr(canvas, "rotate_image"):
            self._message_box().warning(
                self._window,
                self._window._ts("Rotate Canvas"),
                self._window._ts("Canvas service is not available."),
            )
            return
        try:
            ok = bool(canvas.rotate_image(angle=angle))
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Canvas rotate failed: %s", exc)
            self._message_box().warning(
                self._window,
                self._window._ts("Rotate Canvas"),
                self._window._ts("Failed to rotate canvas."),
            )
            return
        if not ok:
            return

        canvas_widget = getattr(self._window, "canvas", None)
        if canvas_widget is not None and hasattr(canvas_widget, "set_canvas_size"):
            new_w = int(getattr(canvas, "_width", 0))
            new_h = int(getattr(canvas, "_height", 0))
            if new_w > 0 and new_h > 0:
                with suppress(Exception):
                    canvas_widget.set_canvas_size(new_w, new_h)
        if angle == 180:
            msg = "Canvas rotated 180°"
        elif angle == 90:
            msg = "Canvas rotated 90° clockwise"
        else:
            msg = "Canvas rotated 90° counter-clockwise"
        self._window.statusBar().showMessage(self._window._ts(msg), 3000)

    def on_image_properties(self) -> None:
        canvas = self._canvas_service()
        if canvas is None:
            self._message_box().warning(
                self._window,
                self._window._ts("Image Properties"),
                self._window._ts("Canvas service is not available."),
            )
            return
        width, height = self._window._current_canvas_size()
        current_dpi = int(getattr(canvas, "_dpi", 96) or 96)
        current_profile = getattr(canvas, "color_profile", None)
        profile_name = (
            getattr(current_profile, "name", None)
            if current_profile is not None and getattr(current_profile, "data", None)
            else None
        )
        dialog = self._image_properties_dialog()(
            width=width,
            height=height,
            dpi=current_dpi,
            color_profile_name=profile_name,
            parent=self._window,
        )
        if dialog.exec() != self._dialog_type().DialogCode.Accepted:
            return
        new_dpi = dialog.dpi()
        if new_dpi != current_dpi:
            if not hasattr(canvas, "update_canvas_config"):
                self._message_box().warning(
                    self._window,
                    self._window._ts("Image Properties"),
                    self._window._ts("Canvas service is not available."),
                )
                return
            try:
                update_fn = cast(Callable[..., Any], canvas.update_canvas_config)
                update_fn(dpi=new_dpi)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Update DPI failed: %s", exc)
                self._message_box().warning(
                    self._window,
                    self._window._ts("Image Properties"),
                    self._window._ts("Failed to update DPI."),
                )
                return
            self._window.statusBar().showMessage(
                self._window._ts("DPI updated to {dpi}").format(dpi=new_dpi),
                3000,
            )
        self._apply_color_profile_choice(canvas, dialog)

    def _apply_color_profile_choice(self, canvas: Any, dialog: Any) -> None:
        """CT-09: assign / clear the document ICC profile chosen in the dialog."""
        choose = getattr(dialog, "color_profile_choice", None)
        setter = getattr(canvas, "set_color_profile", None)
        if not callable(choose) or not callable(setter):
            return
        choice, profile = choose()
        if choice == "keep":
            return
        try:
            changed = bool(setter(profile))
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Assign colour profile failed: %s", exc)
            self._message_box().warning(
                self._window,
                self._window._ts("Image Properties"),
                self._window._ts("Failed to assign the colour profile."),
            )
            return
        if not changed:
            return
        if profile is None:
            message = self._window._ts("Colour profile removed (treated as sRGB)")
        else:
            message = self._window._ts("Colour profile assigned: {name}").format(
                name=getattr(profile, "name", "")
            )
        self._window.statusBar().showMessage(message, 3000)

    def current_canvas_size(self) -> tuple[int, int]:
        canvas = self._canvas_service()
        if canvas and hasattr(canvas, "get_composited_pixmap"):
            try:
                pixmap = canvas.get_composited_pixmap()
                return int(pixmap.width()), int(pixmap.height())
            except Exception:
                logger.debug("Failed to read canvas pixmap size", exc_info=True)
        width = getattr(canvas, "_width", None)
        height = getattr(canvas, "_height", None)
        if isinstance(width, int) and isinstance(height, int):
            return max(1, width), max(1, height)
        try:
            width = int(cast("Any", self._window._settings.get("canvas/width", 1920)))
        except (TypeError, ValueError):
            width = 1920
        try:
            height = int(cast("Any", self._window._settings.get("canvas/height", 1080)))
        except (TypeError, ValueError):
            height = 1080
        return max(1, width), max(1, height)

    def apply_canvas_zoom(self, factor: float) -> None:
        viewport = self._canvas_viewport()
        if viewport is None:
            logger.debug("Canvas viewport not available for zoom operation")
            return

        state = getattr(viewport, "state", None)
        current_zoom = getattr(state, "zoom", None)
        if not isinstance(current_zoom, (int, float)) or not isfinite(current_zoom):
            logger.debug("Invalid canvas zoom state: %s", current_zoom)
            current_zoom = 1.0

        if hasattr(viewport, "zoom_to"):
            try:
                viewport.zoom_to(float(current_zoom) * factor)
            except Exception:
                logger.debug("Failed to apply canvas zoom", exc_info=True)

    def set_canvas_zoom(self, zoom: float) -> None:
        viewport = self._canvas_viewport()
        if viewport is None:
            logger.debug("Canvas viewport not available for zoom operation")
            return

        if not hasattr(viewport, "zoom_to"):
            logger.debug("Canvas viewport has no zoom_to API")
            return

        if not isinstance(zoom, (int, float)) or not isfinite(zoom):
            zoom = 1.0

        try:
            viewport.zoom_to(float(zoom))
        except Exception:
            logger.debug("Failed to set canvas zoom", exc_info=True)

    def _canvas_viewport(self) -> Any | None:
        canvas_widget = getattr(self._window, "canvas", None)
        return getattr(canvas_widget, "viewport", None) if canvas_widget is not None else None

    def set_canvas_grid_visibility(self, on: bool) -> None:
        canvas_widget = getattr(self._window, "canvas", None)
        if canvas_widget is None:
            logger.debug("Canvas not available for grid toggle")
            return

        state = getattr(canvas_widget, "state", None)
        current = bool(getattr(state, "grid_visible", False))
        if current == bool(on):
            return

        toggle_grid = getattr(canvas_widget, "toggle_grid", None)
        if callable(toggle_grid):
            try:
                toggle_grid()
            except Exception:
                logger.debug("Failed to toggle canvas grid", exc_info=True)
                return

    def set_canvas_grid_size(self, size: int) -> None:
        canvas_widget = getattr(self._window, "canvas", None)
        if canvas_widget is None:
            logger.debug("Canvas not available for grid-size change")
            return

        try:
            grid_size = int(size)
        except (TypeError, ValueError):
            logger.debug("Invalid grid size: %r", size)
            return

        if grid_size <= 0:
            grid_size = 1

        set_grid_size = getattr(canvas_widget, "set_grid_size", None)
        if callable(set_grid_size):
            try:
                set_grid_size(grid_size)
                return
            except Exception:
                logger.debug("Failed to set canvas grid size", exc_info=True)
                return

        state = getattr(canvas_widget, "state", None)
        if state is None:
            logger.debug("Canvas state unavailable for grid size change")
            return

        state.grid_size = int(grid_size)
        if hasattr(canvas_widget, "update"):
            canvas_widget.update()

    def sync_view_toggle_actions(self) -> None:
        action = self._window._shortcut_actions.get("view.toggle_grid")
        if action is not None:
            canvas_widget = getattr(self._window, "canvas", None)
            if canvas_widget is not None:
                state = getattr(canvas_widget, "state", None)
                checked = bool(getattr(state, "grid_visible", False))
                action.blockSignals(True)
                action.setChecked(checked)
                action.blockSignals(False)

    def restore_view_preferences(self) -> None:
        try:
            grid_visible = bool(self._window._settings.get_bool("view/grid_visible", False))
        except Exception:
            logger.debug("_restore_view_preferences: suppressed exception", exc_info=True)
            grid_visible = False

        self._window._settings.set("view/grid_visible", grid_visible)
        try:
            grid_size = int(self._window._settings.get_int("view/grid_size", 32))
        except Exception:
            logger.debug("_restore_view_preferences: suppressed exception", exc_info=True)
            grid_size = 32
        self._window._settings.set("view/grid_size", max(1, grid_size))
        grid_size = max(1, grid_size)

        canvas_widget = getattr(self._window, "canvas", None)
        if canvas_widget is not None:
            state = getattr(canvas_widget, "state", None)
            if state is not None:
                state.grid_visible = bool(grid_visible)
                state.grid_size = max(1, grid_size)
            self._window._set_canvas_grid_size(grid_size)

        self._window._sync_view_toggle_actions()

    def on_zoom_in(self) -> None:
        self._window._apply_canvas_zoom(1.1)
        self._window._event_bus.emit("view.zoom", {"delta": +1})

    def on_zoom_out(self) -> None:
        self._window._apply_canvas_zoom(1 / 1.1)
        self._window._event_bus.emit("view.zoom", {"delta": -1})

    def on_zoom_reset(self) -> None:
        self._window._set_canvas_zoom(1.0)
        self._window._event_bus.emit("view.zoom.reset", {})

    def on_comic_book_settings(self) -> None:
        """Open the book settings dialog and commit the edits as one undo step.

        The dialog reports only what changed, so accepting it untouched
        writes nothing (and adds nothing to the history).
        """

        window = self._window
        layout = self._current_book_layout()
        preview_pages, page_attributes = self._book_preview_inputs()
        dialog = ComicBookSettingsDialog(
            layout=layout,
            preview_pages=preview_pages,
            page_attributes=page_attributes,
            parent=window,
        )
        if dialog.exec() != self._dialog_type().DialogCode.Accepted:
            return
        changed = dialog.changed_values()
        if not changed:
            return
        setter = getattr(getattr(window, "host", None), "pages", None)
        set_book_layout = getattr(setter, "set_book_layout", None)
        if not callable(set_book_layout) or not set_book_layout(changed):
            # No open document (metadata port unbound): say so instead of
            # silently discarding the user's choices.
            self._message_box().warning(
                window,
                window._ts("Comic Book Settings"),
                window._ts("The book settings could not be stored in this document."),
            )

    def _book_preview_inputs(self) -> tuple[list[tuple[str, str]], dict[str, Any]]:
        """(page_id, name) pairs plus resolved page attributes for the
        dialog's spread preview; empty when no page service is available
        (the dialog then simply omits the preview)."""
        pages = getattr(getattr(self._window, "host", None), "pages", None)
        list_pages = getattr(pages, "list_pages", None)
        if not callable(list_pages):
            return [], {}
        try:
            summaries = list(list_pages())
        except Exception:
            logger.debug("page list unavailable for the book preview", exc_info=True)
            return [], {}
        preview = [(summary.id, summary.name) for summary in summaries]
        snapshot = getattr(pages, "get_page_snapshot", None)
        metadata_by_id: dict[str, Any] = {}
        if callable(snapshot):
            for summary in summaries:
                page = snapshot(summary.id)
                metadata_by_id[summary.id] = (
                    None if page is None else getattr(page, "metadata", None)
                )
        attributes = BookLayoutService().resolve_page_attributes_by_id(metadata_by_id)
        return preview, attributes

    def _current_book_layout(self) -> BookLayout:
        service = self._window._get_document_service()
        resolve = getattr(service, "resolve_book_layout", None)
        if not callable(resolve):
            return BookLayout()
        try:
            return resolve()
        except Exception:
            logger.debug("book layout unavailable; using defaults", exc_info=True)
            return BookLayout()

    def on_fit_spread(self) -> None:
        """Fit the spread, or the page when there is no spread (CT-06 §6.3)."""

        canvas = getattr(self._window, "canvas", None)
        if canvas is None:
            return
        fit_spread = getattr(canvas, "fit_spread_to_window", None)
        try:
            if callable(fit_spread) and fit_spread():
                self._window._event_bus.emit("view.fit", {"scope": "spread"})
                return
            fit_window = getattr(canvas, "fit_to_window", None)
            if callable(fit_window):
                fit_window()
                self._window._event_bus.emit("view.fit", {"scope": "page"})
        except Exception:
            logger.error("Fit spread failed", exc_info=True)

    def on_toggle_grid(self, on: bool) -> None:
        self._window._set_canvas_grid_visibility(bool(on))
        try:
            self._window._settings.set("view/grid_visible", bool(on))
        except Exception:
            logger.debug("Failed to persist grid visibility")
        self._window._event_bus.emit("view.grid", {"on": on})
