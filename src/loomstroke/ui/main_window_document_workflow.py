"""Document workflow coordination for MainWindow."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from ..core.book_layout_service import BookLayoutService
from ..core.events import Events
from ..core.folio_resolver import resolve_folios
from ..core.history_failure_reporting import history_failure_event_is_current
from ..core.save_outcome_claim import (
    UNMATCHABLE_CLAIM,
    PendingSaveOutcomeClaim,
    SaveOutcomeAnnouncement,
    claim_from_pending_payload,
    classify_save_outcome_announcement,
    evaluate_final_save_outcome,
)
from ..host_api import DocumentService, LoadOptions, SaveOptions
from ..io.atomic_export_writer import write_atomically
from ..io.raster_export import export_raster
from .dialogs.export_pages_dialog import ExportPagesContext, ExportPagesDialog
from .export_pages_runner import run_export_pages
from .main_window_document_close import close_document_session
from .main_window_document_guard import DocumentReplacementGuard, guard_document_replacement
from .main_window_image_open import open_image_document
from .main_window_save_feedback import ManualSaveFeedback
from .new_document_canvas_reset import reset_new_document_canvas
from .new_document_guides import reset_new_document_guides
from .startpage_models import StartPageCanvasSpec
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

_CANVAS_READINESS_POLL_MS = 16
_CANVAS_READINESS_TIMEOUT_MS = 2000


class MainWindowDocumentWorkflowCoordinator:
    """Coordinate document open/save/import/export UI workflows."""

    def __init__(
        self,
        window: Any,
        *,
        new_document_dialog: Callable[[], Any],
        file_dialog: Callable[[], Any],
        message_box: Callable[[], Any],
        progress_dialog: Callable[[], Any],
        dialog_type: Callable[[], Any],
    ) -> None:
        self._window = window
        self._new_document_dialog = new_document_dialog
        self._file_dialog = file_dialog
        self._message_box = message_box
        self._progress_dialog = progress_dialog
        self._dialog_type = dialog_type
        self._canvas_readiness_gate_generation = 0
        self._canvas_readiness_gate_deadline = 0.0
        # SAVE-ASYNC-REV-01: manual-save state machine (review-fix 2).
        # Three states, tracked by two fields:
        #   no_event — both None: the service emitted nothing; the legacy
        #              direct fallback and close-save stay allowed.
        #   pending  — _pending_save_claim set: a built-in DOC_SAVED
        #              announced an authoritative DOC_SAVE_OUTCOME that
        #              has not (validly) arrived yet; path/dirty/title
        #              stay unsettled, fallback and close are refused.
        #   final    — _last_manual_save_outcome set: a valid matching
        #              final (or a legacy field-less DOC_SAVED) was
        #              applied; it owns path/dirty/close permission.
        self._last_manual_save_outcome: dict[str, Any] | None = None
        self._pending_save_claim: PendingSaveOutcomeClaim | None = None
        self._active_manual_save_feedback: ManualSaveFeedback | None = None
        self._replacement_guard = DocumentReplacementGuard(self, window, message_box)

    def initialize_close_save_state(self) -> None:
        """Initialize close/save async guard state owned by document workflow."""
        window = self._window
        window._closing_in_progress = False
        window._close_progress_dialog = None
        window._save_async_in_flight = False

    def initialize_document_state(self) -> None:
        """Initialize document UI state on the MainWindow compatibility surface."""
        window = self._window
        window._document_path = None
        window._document_dirty = False
        window._has_document = False
        window._recent_files = []
        window._last_file_dir = ""

    def on_new_request(self, _: Any) -> None:
        self._window._open_new_document_dialog()

    def on_open_request(self, payload: Any) -> None:
        path = None
        if isinstance(payload, Mapping):
            candidate = payload.get("path")
            if isinstance(candidate, str) and candidate:
                path = candidate
        if path:
            if self._window._open_path(path, emit_event=False):
                self._window._add_recent_file(path)
            return
        self._window._on_open()

    def on_close_request(self, _: Any) -> None:
        self._window._close_document()

    def on_create_document_request(self, payload: Any) -> None:
        if not isinstance(payload, Mapping):
            return
        try:
            spec = StartPageCanvasSpec.from_payload(payload)
        except Exception:
            logger.debug("_on_create_document_request: suppressed exception", exc_info=True)
            return
        self._window._apply_new_document(spec)

    def on_new(self) -> None:
        self._window._event_bus.emit("file.new", {})

    def on_close(self) -> None:
        self._window._event_bus.emit("file.close", {})

    def on_open(self) -> None:
        path, _ = self._file_dialog().getOpenFileName(
            self._window,
            self._window._ts("Open Project"),
            self._window._last_file_dir,
            self._window._ts(
                "LoomStroke Project (*.lstroke);;"
                "Photoshop (*.psd);;"
                "Images (*.png *.jpg *.jpeg *.bmp *.tiff *.webp);;"
                "All Files (*.*)"
            ),
        )
        if path and self._window._open_path(path):
            self._window._last_file_dir = str(Path(path).parent)
            self._window._add_recent_file(path)

    def open_new_document_dialog(self) -> None:
        dialog = self._new_document_dialog()(self._window._settings, self._window)
        if dialog.exec() != self._dialog_type().DialogCode.Accepted:
            return
        spec = dialog.spec()
        if spec is None:
            return
        emitter = getattr(self._window._event_bus, "emit", None) or getattr(
            self._window._event_bus, "publish", None
        )
        if callable(emitter):
            emitter("ui.request.create_document", spec.to_payload())

    def load_recent_files(self) -> None:
        """Load recent file paths from the StartPage settings namespace."""
        recent: Any = self._window._settings.get("plugins/ui_startpage/recent_files", None)
        if not isinstance(recent, list):
            recent = self._window._settings.get("ui/recent_files", [])
        if not isinstance(recent, list):
            recent = []
        self._window._recent_files = [str(path) for path in recent if isinstance(path, str)]

    def add_recent_file(self, path: str) -> None:
        """Record a successfully opened file path in the recent-file list."""
        recent_files = self._window._recent_files
        if path in recent_files:
            recent_files.remove(path)
        recent_files.insert(0, path)
        max_items = self._window._settings.get("plugins/ui_startpage/recent_files_max", 10)
        if not isinstance(max_items, int) or max_items <= 0:
            max_items = 10
        self._window._recent_files = recent_files[:max_items]
        try:
            self._window._settings.set(
                "plugins/ui_startpage/recent_files",
                list(self._window._recent_files),
            )
        except Exception:
            logger.debug("Failed to persist StartPage recent files", exc_info=True)

    @guard_document_replacement(False)
    def open_path(self, path: str, *, emit_event: bool = True) -> bool:
        resolved = Path(path)
        suffix = resolved.suffix.lower().lstrip(".")
        if suffix == "lstroke":
            if self._window._load_document(resolved):
                self._window._set_document_present(True)
                return True
        elif suffix == "psd":
            return bool(self._window._open_psd_as_document(resolved))
        elif suffix in {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"}:
            return bool(self._window._open_image_as_document(resolved))
        if emit_event:
            self._window._event_bus.emit("file.open", {"path": str(resolved)})
        return False

    def on_import(self) -> None:
        """Show import dialog and add layers to the current canvas."""
        path, _ = self._file_dialog().getOpenFileName(
            self._window,
            self._window._ts("Import"),
            self._window._last_file_dir,
            self._window._ts(
                "Photoshop (*.psd);;"
                "Images (*.png *.jpg *.jpeg *.bmp *.tiff *.webp);;"
                "All Files (*.*)"
            ),
        )
        if path and self._window._import_image_file(Path(path)):
            self._window._last_file_dir = str(Path(path).parent)

    def open_psd_as_document(self, path: Path) -> bool:
        """Prepare the complete PSD before replacing the current document."""
        return open_image_document(self, path, layered=True)

    def open_image_as_document(self, path: Path) -> bool:
        """Prepare the decoded image before replacing the current document."""
        return open_image_document(self, path, layered=False)

    def import_image_file(self, path: Path) -> bool:  # noqa: PLR0911
        """Import an image as a new layer."""
        service = self._window._get_document_service()
        if service is None:
            return False

        if path.suffix.lower() == ".psd":
            psd_importer = getattr(service, "import_psd_layers", None)
            if callable(psd_importer):
                try:
                    layer_ids = psd_importer(str(path))
                except Exception as exc:
                    logger.exception("Failed to import PSD: %s", exc)
                    self._message_box().critical(
                        self._window, self._window._ts("Import Failed"), str(exc)
                    )
                    return False
                if layer_ids:
                    self._window._set_document_present(True)
                    self._window.statusBar().showMessage(
                        self._window._ts("Imported {name} ({count} layers)").format(
                            name=path.name, count=len(layer_ids)
                        ),
                        3000,
                    )
                    return True

        importer = getattr(service, "import_layer", None)
        if not callable(importer):
            return False
        try:
            layer_id = importer(str(path))
        except Exception as exc:
            logger.exception("Failed to import image: %s", exc)
            self._message_box().critical(
                self._window,
                self._window._ts("Import Failed"),
                str(exc),
            )
            return False
        if layer_id is None:
            self._message_box().warning(
                self._window,
                self._window._ts("Import Failed"),
                self._window._ts("Could not import: {path}").format(path=path.name),
            )
            return False
        self._window._set_document_present(True)
        self._window.statusBar().showMessage(
            self._window._ts("Imported {name}").format(name=path.name), 3000
        )
        return True

    def open_recent_file(self, path: str) -> None:
        self._window._open_path(path)

    @guard_document_replacement(None)
    def apply_new_document(self, spec: StartPageCanvasSpec) -> None:
        width = int(spec.width_px)
        height = int(spec.height_px)
        dpi = max(1, int(spec.dpi))

        bg = spec.background
        if bg.kind == "transparent":
            color = QColor(0, 0, 0, 0)
        else:
            rgba = bg.color_rgba or (255, 255, 255, 255)
            color = QColor(rgba[0], rgba[1], rgba[2], rgba[3])

        if self._window.host and hasattr(self._window.host, "canvas"):
            canvas = self._window.host.canvas
            try:
                reset_new_document_canvas(canvas, spec, color)
            except (ValueError, MemoryError):
                logger.warning("New-document colour initialization failed", exc_info=True)
                self._message_box().critical(
                    self._window,
                    translate_with_fallback("NewDocumentColor", "New Document Failed"),
                    translate_with_fallback(
                        "NewDocumentColor",
                        "Could not create the document. Check the CMYK profile and canvas size.",
                    ),
                )
                return

        service = self._window._get_document_service()
        if service is not None:
            reset_state = getattr(service, "reset_document_state", None)
            if callable(reset_state):
                try:
                    reset_state()
                except Exception:
                    logger.debug("Failed to reset document state", exc_info=True)

        try:
            self._window._settings.set("canvas/width", width)
            self._window._settings.set("canvas/height", height)
            self._window._settings.set("canvas/dpi", dpi)
        except Exception:
            logger.debug("Failed to persist canvas defaults", exc_info=True)

        self._window._document_path = None
        self._window._document_dirty = False
        self._window._update_window_title()
        self._window._set_document_present(True)

        payload = {
            "width": width,
            "height": height,
            "dpi": dpi,
            "background": bg.to_payload(),
        }
        self._emit_document_created(payload)

    def _emit_document_created(self, payload: dict[str, Any]) -> None:
        """Emit ``DOC_CREATED`` exactly once for a successfully built document.

        Shared by the new-document flow and the image/PSD open-as-document
        flows so document-boundary observers (revision tracker, autosave
        epoch reset) see every fresh document, not only StartPage creations.
        """
        reset_new_document_guides(self._window)
        emitter = getattr(self._window._event_bus, "emit", None) or getattr(
            self._window._event_bus, "publish", None
        )
        if callable(emitter):
            emitter(Events.DOC_CREATED, payload)

    @guard_document_replacement(None)
    def close_document(self) -> None:
        if not self._window._has_document:
            return
        close_document_session(self._window)
        self._last_manual_save_outcome = None
        self._pending_save_claim = None
        self._window._document_path = None
        self._window._document_dirty = False
        self._window._update_window_title()
        self._window._set_document_present(False)
        self._window.statusBar().showMessage("Document closed", 2000)
        emitter = getattr(self._window._event_bus, "emit", None) or getattr(
            self._window._event_bus, "publish", None
        )
        if callable(emitter):
            emitter(Events.DOC_CLOSED, {"source": "ui"})

    def set_document_present(self, present: bool) -> None:
        self._window._has_document = bool(present)
        if not present:
            self._finish_canvas_readiness_gate()
        if self._window._workspace_stack is None or self._window._startpage_panel is None:
            return
        if present:
            target = (
                self._window._canvas_frame
                if self._window._canvas_frame is not None
                else self._window.canvas
            )
            self._window._workspace_stack.setCurrentWidget(target)
            self._window._raise_visible_floating_tool_toolbar()
            self._window._request_native_canvas_refresh_deferred(force_full_recompose=True)
            canvas = getattr(self._window, "canvas", None)
            set_focus = getattr(canvas, "setFocus", None)
            if callable(set_focus):
                set_focus(Qt.FocusReason.OtherFocusReason)
            self._begin_canvas_readiness_gate()
        else:
            self._window._startpage_panel.set_recent_files(self._window._recent_files)
            self._window._workspace_stack.setCurrentWidget(self._window._startpage_panel)

    def _begin_canvas_readiness_gate(self) -> None:
        canvas = self._live_readiness_canvas()
        should_defer = getattr(canvas, "should_defer_gpu_preview_readback", None)
        set_gate = getattr(canvas, "set_canvas_readiness_gate_active", None)
        if not callable(should_defer) or not callable(set_gate) or not bool(should_defer()):
            if callable(set_gate):
                set_gate(False)
            return

        self._canvas_readiness_gate_generation += 1
        generation = self._canvas_readiness_gate_generation
        self._canvas_readiness_gate_deadline = time.monotonic() + (
            _CANVAS_READINESS_TIMEOUT_MS / 1000.0
        )
        self._request_canvas_readiness_refresh()
        self._schedule_canvas_readiness_poll(generation)

    def _canvas_readiness_message(self) -> str:
        translate = getattr(self._window, "_ts", None)
        if callable(translate):
            return str(translate("Preparing canvas..."))
        return "Preparing canvas..."

    def _schedule_canvas_readiness_poll(self, generation: int) -> None:
        if isinstance(self._window, QObject):
            if isValid(self._window):
                QTimer.singleShot(
                    _CANVAS_READINESS_POLL_MS,
                    self._window,
                    lambda: self._poll_canvas_readiness_gate(generation),
                )
            return
        QTimer.singleShot(
            _CANVAS_READINESS_POLL_MS,
            lambda: self._poll_canvas_readiness_gate(generation),
        )

    def _poll_canvas_readiness_gate(self, generation: int) -> None:
        if generation != self._canvas_readiness_gate_generation:
            return
        canvas = self._live_readiness_canvas()
        should_defer = getattr(canvas, "should_defer_gpu_preview_readback", None)
        timed_out = time.monotonic() >= self._canvas_readiness_gate_deadline
        if not callable(should_defer) or not bool(should_defer()) or timed_out:
            self._finish_canvas_readiness_gate(generation=generation)
            return
        self._request_canvas_readiness_refresh()
        self._schedule_canvas_readiness_poll(generation)

    def _request_canvas_readiness_refresh(self) -> None:
        canvas = self._live_readiness_canvas()
        request_refresh = getattr(canvas, "request_native_surface_refresh", None)
        if callable(request_refresh):
            try:
                request_refresh(force_full_recompose=True)
            except Exception:
                logger.debug("Failed to request canvas readiness refresh", exc_info=True)
        set_gate = getattr(canvas, "set_canvas_readiness_gate_active", None)
        if callable(set_gate):
            set_gate(True, self._canvas_readiness_message())

    def _finish_canvas_readiness_gate(self, *, generation: int | None = None) -> None:
        if generation is not None and generation != self._canvas_readiness_gate_generation:
            return
        self._canvas_readiness_gate_generation += 1
        canvas = self._live_readiness_canvas()
        set_gate = getattr(canvas, "set_canvas_readiness_gate_active", None)
        if callable(set_gate):
            set_gate(False)

    def _live_readiness_canvas(self) -> Any:
        canvas = getattr(self._window, "canvas", None)
        if isinstance(canvas, QObject) and not isValid(canvas):
            return None
        return canvas

    def get_document_service(self) -> DocumentService | None:
        host_api = getattr(self._window, "_host_api", None)
        if not host_api:
            return None
        service = getattr(host_api, "document", None)
        if service is None:
            return None
        required = ("save", "load", "export_layer", "import_layer")
        if all(callable(getattr(service, name, None)) for name in required):
            return cast(DocumentService, service)
        return None

    def load_document(self, path: Path) -> bool:
        service = self._window._get_document_service()
        if service is None:
            return False
        try:
            loaded = bool(service.load(str(path), LoadOptions()))
        except Exception as exc:  # pragma: no cover - dialog fallback
            logger.exception("Failed to load document: %s", exc)
            self._message_box().critical(
                self._window,
                self._window._ts("Load Failed"),
                self._window._ts("Unable to load document: {path}").format(path=path),
            )
            return False
        if not loaded:
            self._message_box().critical(
                self._window,
                self._window._ts("Load Failed"),
                self._window._ts("Unable to load document: {path}").format(path=path),
            )
            return False
        self._window._document_path = path
        self._window._document_dirty = False
        if not self._window._has_document:
            self._window._set_document_present(True)
        self._window._update_window_title()
        self._window.statusBar().showMessage(f"Loaded {path.name}", 3000)
        return True

    def mark_document_recovered_from_autosave(
        self,
        *,
        original_path: Path | None,
        recovery_path: Path,
    ) -> None:
        """Mark a loaded autosave as recovered, not as the active save target."""

        self._window._document_path = Path(original_path) if original_path else None
        self._window._document_dirty = True
        self._window._update_window_title()
        self._window._set_document_present(True)
        if self._window._document_path:
            message = self._window._ts(
                "Recovered {name} from autosave. Save to keep recovered changes."
            ).format(name=self._window._document_path.name)
        else:
            message = self._window._ts(
                "Recovered unsaved document from autosave. Use Save As to keep it."
            )
        self._window.statusBar().showMessage(message, 5000)
        logger.info(
            "Recovered autosave loaded from %s; active save target is %s",
            recovery_path,
            self._window._document_path,
        )

    def on_save(self) -> None:
        target = self._window._document_path or self._window._prompt_save_path()
        if target is None:
            return
        self._window._start_save_async(target)

    def on_save_as(self) -> None:
        target = self._window._prompt_save_path()
        if target is None:
            return
        self._window._start_save_async(target)

    def on_export(self) -> None:
        # CT-09 §6: the Export Image dialog (format / colour profile / alpha /
        # encoder options / warning preview) owns the flow; the bare path
        # prompt + ``export_document`` stay for callers that bring a path.
        from .main_window_export_image_action import (  # noqa: PLC0415
            MainWindowExportImageAction,
        )

        MainWindowExportImageAction(self._window).run()

    def prompt_save_path(self) -> Path | None:
        default = (
            str(self._window._document_path)
            if self._window._document_path
            else self._window._last_file_dir
        )
        path, _ = self._file_dialog().getSaveFileName(
            self._window,
            self._window._ts("Save Project"),
            default,
            self._window._ts("LoomStroke Project (*.lstroke);;All Files (*.*)"),
        )
        if not path:
            return None
        self._window._last_file_dir = str(Path(path).parent)
        return Path(path)

    def perform_save(self, target: Path) -> bool:
        service = self._window._get_document_service()
        if service is None:
            self._message_box().warning(
                self._window,
                self._window._ts("Save Unavailable"),
                self._window._ts("Document service is not ready."),
            )
            return False
        started_state = self._replacement_guard.capture_state()
        self._last_manual_save_outcome = None
        self._pending_save_claim = None
        feedback = ManualSaveFeedback(target)
        previous_feedback = self._active_manual_save_feedback
        self._active_manual_save_feedback = feedback
        try:
            saved = bool(service.save(str(target), SaveOptions()))
        except Exception as exc:  # pragma: no cover - dialog fallback
            logger.exception("Failed to save document: %s", exc)
            self._message_box().critical(
                self._window,
                self._window._ts("Save Failed"),
                self._window._ts("Unable to save document: {path}\n{error}").format(
                    path=target,
                    error=exc,
                ),
            )
            return False
        finally:
            self._active_manual_save_feedback = previous_feedback
        if not saved:
            if not feedback.deferred_notice_shown:
                self._message_box().critical(
                    self._window,
                    self._window._ts("Save Failed"),
                    self._window._ts("Failed to write {path}").format(path=target),
                )
            return False
        # SAVE-ASYNC-REV-01: the event handlers are the single owner of
        # path/dirty updates.  The legacy direct update runs only in the
        # no_event state (custom services that emit nothing); a pending
        # claim without a valid final stays fail-closed and keeps dirty.
        if self._last_manual_save_outcome is None:
            if self._pending_save_claim is not None:
                logger.warning(
                    "Manual save finished with a pending outcome claim but no "
                    "valid DOC_SAVE_OUTCOME; keeping dirty state (fail-closed)"
                )
            elif self._replacement_guard.capture_state() == started_state:
                self._window._document_path = target
                self._window._document_dirty = False
                self._window._update_window_title()
                self._window.statusBar().showMessage(f"Saved {target.name}", 3000)
        return True

    def start_save_async(self, target: Path) -> None:
        service = self._window._get_document_service()
        if service is None:
            self._message_box().warning(
                self._window,
                self._window._ts("Save Unavailable"),
                self._window._ts("Document service is not ready."),
            )
            return
        if not callable(getattr(service, "save_async", None)):
            self._window._perform_save(target)
            return
        manager = getattr(self._window, "_async_manager", None)
        if manager is None or not hasattr(manager, "create_background_task"):
            self._window._perform_save(target)
            return
        if getattr(self._window, "_save_async_in_flight", False):
            self._window.statusBar().showMessage(
                self._window._ts("Save already in progress — skipping duplicate request"),
                3000,
            )
            return
        self._window._save_async_in_flight = True
        self._last_manual_save_outcome = None
        self._pending_save_claim = None
        self._window.statusBar().showMessage(
            self._window._ts("Saving {name}…").format(name=target.name)
        )
        manager.create_background_task(self._window._run_save_async(service, target))

    async def run_save_async(
        self,
        service: DocumentService,
        target: Path,
    ) -> bool:
        feedback = ManualSaveFeedback(target)
        previous_feedback = self._active_manual_save_feedback
        self._active_manual_save_feedback = feedback
        try:
            try:
                saved = bool(await service.save_async(str(target), SaveOptions()))
            except Exception as exc:  # pragma: no cover - dialog fallback
                logger.exception("Failed to save document (async): %s", exc)
                self._message_box().critical(
                    self._window,
                    self._window._ts("Save Failed"),
                    self._window._ts("Unable to save document: {path}\n{error}").format(
                        path=target,
                        error=exc,
                    ),
                )
                self._window.statusBar().clearMessage()
                return False
            if not saved:
                if not feedback.deferred_notice_shown:
                    self._message_box().critical(
                        self._window,
                        self._window._ts("Save Failed"),
                        self._window._ts("Failed to write {path}").format(path=target),
                    )
                self._window.statusBar().clearMessage()
                return False
            # SAVE-ASYNC-REV-01: a True result only means the archive was
            # written.  Path/dirty/title follow the applied final outcome;
            # a stale save is NOT a write failure and shows no dialog.
            # The legacy direct update runs only in the no_event state
            # (custom services that emit nothing); a pending claim without
            # a valid final stays fail-closed and keeps dirty.
            if self._last_manual_save_outcome is None:
                if self._pending_save_claim is not None:
                    logger.warning(
                        "Async save finished with a pending outcome claim but "
                        "no valid DOC_SAVE_OUTCOME; keeping dirty state "
                        "(fail-closed)"
                    )
                else:
                    self._window._document_path = target
                    self._window._document_dirty = False
                    self._window._update_window_title()
                    self._window.statusBar().showMessage(f"Saved {target.name}", 3000)
            return True
        finally:
            self._active_manual_save_feedback = previous_feedback
            self._window._save_async_in_flight = False

    def try_start_close_save_async(self, target: Path) -> bool:
        service = self._window._get_document_service()
        if service is None:
            return False
        if not callable(getattr(service, "save_async", None)):
            return False
        manager = getattr(self._window, "_async_manager", None)
        if manager is None or not hasattr(manager, "create_background_task"):
            return False
        if getattr(self._window, "_save_async_in_flight", False):
            return False

        progress = self._progress_dialog()(
            self._window._ts("Saving {name}…").format(name=target.name),
            "",
            0,
            0,
            self._window,
        )
        progress.setWindowTitle(self._window._ts("Saving"))
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        self._window._closing_in_progress = True
        self._window._close_progress_dialog = progress
        self._window._save_async_in_flight = True
        self._last_manual_save_outcome = None
        self._pending_save_claim = None
        manager.create_background_task(
            self._window._run_close_with_save_async(service, target, progress)
        )
        return True

    def _close_save_outcome_allows_close(self) -> bool:
        """True when the just-finished close-save covered the live document.

        SAVE-ASYNC-REV-01: even under the ApplicationModal progress dialog
        a plugin/background event could modify the document during the
        worker encode.  Closing is allowed only when the save covered the
        latest state; a custom service that emitted no DOC_SAVED keeps the
        legacy behaviour (treated as current).
        """
        # Review-fix 3: pending is checked FIRST, defensively — even if a
        # future regression ever left a stale final next to a live claim,
        # an announced-but-unsettled save must never authorize closing.
        if self._pending_save_claim is not None:
            logger.warning(
                "Close-save outcome claim is still pending without a valid "
                "DOC_SAVE_OUTCOME; refusing to close (fail-closed)"
            )
            return False
        outcome = self._last_manual_save_outcome
        if outcome is not None:
            return bool(outcome.get("same_document")) and bool(outcome.get("covers_latest"))
        # no_event: custom service that emits nothing keeps the legacy
        # close behaviour.
        return True

    async def run_close_with_save_async(
        self,
        service: DocumentService,
        target: Path,
        progress: Any,
    ) -> None:
        saved = False
        may_close = False
        try:
            saved = await self._window._run_save_async(service, target)
            may_close = saved and self._close_save_outcome_allows_close()
        finally:
            try:
                progress.close()
            except Exception:  # pragma: no cover - defensive UI cleanup
                logger.debug("Close-save progress dialog already closed")
            self._window._close_progress_dialog = None
            if not may_close:
                # A stale close-save is a successful archive write, not a
                # write failure: abort the close, keep the dirty state, and
                # let the normal unsaved-changes confirmation run again.
                self._window._closing_in_progress = False

        if may_close:
            self._window.close()
        elif saved:
            self._window.statusBar().showMessage(
                self._window._ts(
                    "Saved {name} — newer unsaved changes remain, close cancelled"
                ).format(name=target.name),
                5000,
            )

    def prompt_export_path(self) -> tuple[Path | None, str | None]:
        default = (
            self._window._document_path.with_suffix(".png")
            if self._window._document_path
            else Path("untitled.png")
        )
        path, selected = self._file_dialog().getSaveFileName(
            self._window,
            self._window._ts("Export Image"),
            str(default),
            self._window._ts(
                "PNG Image (*.png);;JPEG Image (*.jpg *.jpeg)"
                ";;TIFF Image (*.tif *.tiff);;WebP Image (*.webp)"
                ";;SVG Image (*.svg);;PSD Image (*.psd);;All Files (*.*)"
            ),
        )
        if not path:
            return None, None
        return Path(path), selected

    def export_document(self, path: Path, fmt_hint: str | None) -> bool:
        fmt = self.resolve_export_format(path, fmt_hint)
        if fmt == "SVG":
            return bool(self._window._export_svg(path))
        if fmt == "PSD":
            return bool(self._window._export_psd(path))
        pixmap = self._window._get_composited_pixmap()
        if pixmap is None:
            self._message_box().warning(
                self._window,
                self._window._ts("Export Unavailable"),
                self._window._ts("Canvas composite is not available."),
            )
            return False
        # CT-09 E2: PNG/JPEG go through the shared export contract + FileIO
        # metadata path (DPI actually lands in the file) with an atomic
        # write, instead of QPixmap.save.  Encoder change: same pixels,
        # but file size/bytes differ from the old Qt encoder.
        result = export_raster(
            self._pixmap_to_rgba(pixmap),
            Path(path),
            fmt,
            canvas_dpi=self._export_canvas_dpi(),
            background_rgba=self._export_background_rgba(),
        )
        if result.status == "failed":
            self._message_box().critical(
                self._window,
                self._window._ts("Export Failed"),
                self._window._ts("Unable to export image: {path}").format(path=path),
            )
            return False
        message = f"Exported {path.name}"
        if result.status == "warning":
            for issue in result.issues:
                logger.info("export warning: %s (%s)", issue.code, issue.message)
            message += " (!)"
        self._window.statusBar().showMessage(message, 3000)
        return True

    @staticmethod
    def _pixmap_to_rgba(pixmap: QPixmap) -> NDArray[np.uint8]:
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = image.width(), image.height()
        raw = np.frombuffer(bytes(image.constBits()), dtype=np.uint8)
        rows = raw.reshape(height, image.bytesPerLine())
        return np.ascontiguousarray(rows[:, : width * 4].reshape(height, width, 4))

    def _export_canvas_dpi(self) -> tuple[float, float] | None:
        """Best-effort document DPI, the same way PageService reads it."""
        host_api = getattr(self._window, "_host_api", None)
        canvas = getattr(host_api, "canvas", None) if host_api else None
        try:
            return (float(canvas._dpi), float(canvas._dpi))  # noqa: SLF001
        except Exception:
            pass
        try:
            dpi = tuple(canvas.config.dpi)
            return (float(dpi[0]), float(dpi[1]))
        except Exception:
            return None

    def _export_background_rgba(self) -> tuple[int, int, int, int]:
        host_api = getattr(self._window, "_host_api", None)
        canvas = getattr(host_api, "canvas", None) if host_api else None
        for source in (canvas, getattr(canvas, "config", None)):
            color = getattr(source, "background_color", None)
            if color is not None:
                try:
                    r, g, b, a = (int(part) for part in tuple(color))
                    return (r, g, b, a)
                except Exception:
                    continue
        return (255, 255, 255, 255)

    def on_export_pages(self) -> None:
        """CT-10 X2: File > Export Pages... — scope/naming via the shared
        X1 core, rendering via the shared page-scene conversion."""
        window = self._window
        pages = getattr(getattr(window, "_host_api", None), "pages", None)
        list_pages = getattr(pages, "list_pages", None)
        snapshot = getattr(pages, "get_page_snapshot", None)
        if not callable(list_pages) or not callable(snapshot):
            self._message_box().warning(
                window,
                window._ts("Export Pages"),
                window._ts("No pages are available to export."),
            )
            return
        summaries = list(list_pages() or [])
        if not summaries:
            self._message_box().warning(
                window,
                window._ts("Export Pages"),
                window._ts("No pages are available to export."),
            )
            return

        page_ids = tuple(summary.id for summary in summaries)
        metadata_by_id = self._page_metadata_by_id(pages, snapshot, page_ids)
        layout = self._resolve_book_layout()
        attributes = BookLayoutService().resolve_page_attributes_by_id(metadata_by_id)
        context = ExportPagesContext(
            page_ids=page_ids,
            names_by_id={summary.id: summary.name for summary in summaries},
            metadata_by_id=metadata_by_id,
            roles_by_id={page_id: attributes[page_id].role for page_id in page_ids},
            folios_by_id=resolve_folios(page_ids, layout, attributes),
            current_page_id=getattr(pages, "get_active_page_id", lambda: None)(),
            document_name=(window._document_path.stem if window._document_path else "untitled"),
        )
        initial_directory = window._document_path.parent if window._document_path else Path.home()
        dialog = ExportPagesDialog(
            context=context, initial_directory=initial_directory, parent=window
        )
        if dialog.exec() != self._dialog_accepted_code():
            return
        plan = dialog.export_plan()
        if plan is None:
            return
        from .preflight_export_gate import PreflightExportGate  # noqa: PLC0415

        if not PreflightExportGate(window).allow("Export Pages"):
            return
        run_export_pages(window, pages, self._document_metadata_for_export(), plan)

    def _dialog_accepted_code(self) -> Any:
        from PySide6.QtWidgets import QDialog  # noqa: PLC0415

        return QDialog.DialogCode.Accepted

    @staticmethod
    def _page_metadata_by_id(pages: Any, snapshot: Any, page_ids: Any) -> dict[str, Any]:
        # CT-08 V2: metadata-only reads must not hydrate page content.
        reader = getattr(pages, "get_page_metadata", None)
        if callable(reader):
            return {page_id: reader(page_id) for page_id in page_ids}
        return {page_id: getattr(snapshot(page_id), "metadata", None) for page_id in page_ids}

    def _resolve_book_layout(self) -> Any:
        service = self._window._get_document_service()
        resolve = getattr(service, "resolve_book_layout", None)
        if callable(resolve):
            try:
                return resolve()
            except Exception:
                logger.debug("book layout unavailable for export", exc_info=True)
        from ..core.book_spread_layout import BookLayout  # noqa: PLC0415

        return BookLayout()

    def _document_metadata_for_export(self) -> Any:
        from ..io.layer_io import DocumentMetadata  # noqa: PLC0415

        host_api = getattr(self._window, "_host_api", None)
        canvas = getattr(host_api, "canvas", None) if host_api else None
        return DocumentMetadata(
            width=int(getattr(canvas, "_width", 1920) or 1920),
            height=int(getattr(canvas, "_height", 1080) or 1080),
            background_color=self._export_background_rgba(),
            dpi=self._export_canvas_dpi() or (72.0, 72.0),
        )

    def export_svg(self, path: Path) -> bool:
        """Export visible vector layers as SVG."""
        from ..plugins.vector_tools.parametric_shapes import (  # noqa: PLC0415
            vector_object_groups_to_svg,
        )

        host_api = getattr(self._window, "_host_api", None)
        canvas = getattr(host_api, "canvas", None) if host_api else None
        if canvas is None:
            self._message_box().warning(
                self._window,
                self._window._ts("Export Unavailable"),
                self._window._ts("Canvas composite is not available."),
            )
            return False

        layers = getattr(canvas, "_layers", None) or []
        layer_groups = []
        object_count = 0
        omitted_unsupported = 0
        for layer in layers:
            if getattr(layer, "layer_type", "raster") != "vector":
                continue
            if not getattr(layer, "visible", True):
                continue
            if self.svg_vector_layer_export_issue(layer) is not None:
                omitted_unsupported += 1
                continue
            layer_objects = list(getattr(layer, "vector_objects", []) or [])
            if not layer_objects:
                continue
            layer_groups.append((layer_objects, getattr(layer, "opacity", 1.0)))
            object_count += len(layer_objects)

        if object_count == 0:
            if omitted_unsupported:
                self._message_box().warning(
                    self._window,
                    self._window._ts("Export SVG"),
                    self._window._ts(
                        "No SVG-compatible vector objects to export. Unsupported blend "
                        "modes or live filters were omitted."
                    ),
                )
                return False
            self._message_box().information(
                self._window,
                self._window._ts("Export SVG"),
                self._window._ts("No visible vector objects to export."),
            )
            return False

        w = getattr(canvas, "_width", 1920)
        h = getattr(canvas, "_height", 1080)
        svg_str = vector_object_groups_to_svg(layer_groups, w, h)
        try:
            # CT-09 E4: same bytes as before, but through the atomic
            # writer so a failed write keeps any existing file intact.
            write_atomically(path, lambda tmp: tmp.write_text(svg_str, encoding="utf-8"))
        except OSError as exc:
            self._message_box().critical(
                self._window,
                self._window._ts("Export Failed"),
                self._window._ts("Unable to export image: {path}").format(path=path) + f"\n{exc}",
            )
            return False
        if omitted_unsupported:
            self._window._show_svg_export_omission_warning(path.name, omitted_unsupported)
            self._window.statusBar().showMessage(
                self._window._ts(
                    "Exported {name}; omitted {count} unsupported vector layer(s)."
                ).format(name=path.name, count=omitted_unsupported),
                5000,
            )
        else:
            self._window.statusBar().showMessage(f"Exported {path.name}", 3000)
        return True

    def show_svg_export_omission_warning(self, name: str, count: int) -> None:
        if not isinstance(self._window, QWidget):
            return
        window = cast(Any, self._window)
        self._message_box().warning(
            window,
            window._ts("Export SVG"),
            window._ts(
                "Exported {name}, but omitted {count} unsupported vector layer(s). "
                "Layers using non-normal blend modes or visible live filters cannot be "
                "represented in the current SVG subset."
            ).format(name=name, count=count),
        )

    @staticmethod
    def svg_vector_layer_export_issue(layer: Any) -> str | None:
        blend_mode = getattr(layer, "blend_mode", "normal")
        blend_value = str(getattr(blend_mode, "value", blend_mode)).strip().lower()
        if blend_value not in {"normal", "pass_through", "pass-through", "pass through"}:
            return "blend"

        for child in getattr(layer, "children", []) or []:
            if (
                getattr(child, "layer_type", "") == "effect"
                and getattr(child, "visible", True)
                and getattr(child, "filter_type", getattr(child, "effect_type", None))
            ):
                return "effect"
        return None

    def export_psd(self, path: Path) -> bool:
        """Export document as a layered PSD file."""
        from ..core.commands import BlendMode  # noqa: PLC0415
        from ..io.file_io import FileIO  # noqa: PLC0415

        host_api = getattr(self._window, "_host_api", None)
        canvas = getattr(host_api, "canvas", None) if host_api else None
        if canvas is None:
            self._message_box().warning(
                self._window,
                self._window._ts("Export Unavailable"),
                self._window._ts("Canvas composite is not available."),
            )
            return False

        try:
            return bool(self._window._do_export_psd_impl(path, canvas, BlendMode, FileIO))
        except Exception:
            import traceback  # noqa: PLC0415

            tb = traceback.format_exc()
            logger.error("PSD export failed:\n%s", tb)
            self._message_box().critical(
                self._window,
                self._window._ts("Export Failed"),
                self._window._ts("Unable to export image: {path}").format(path=path)
                + f"\n\n{tb[-500:]}",
            )
            return False

    def do_export_psd_impl(
        self, path: Path, canvas: Any, BlendMode: type, FileIO: type  # noqa: N803
    ) -> bool:
        from loomstroke.core.psd_export_service import PsdExportService  # noqa: PLC0415

        del BlendMode, FileIO
        try:
            PsdExportService.export(canvas, path)
        except (ImportError, OSError, ValueError) as error:
            logger.warning("PSD hierarchy export failed", exc_info=True)
            self._message_box().critical(
                self._window, self._window._ts("Export Failed"), self._window._ts(str(error))
            )
            return False
        self._window.statusBar().showMessage(f"Exported {path.name}", 3000)
        return True

    @staticmethod
    def _write_psd_atomically(
        path: Path,
        file_io: Any,
        cw: int,
        ch: int,
        layer_tuples: list[Any],
        group_defs: list[Any],
    ) -> bool:
        """CT-09 E4: atomic destination write; the layered PSD content
        contract itself is unchanged."""

        def _encode_psd(tmp: Path) -> None:
            if not file_io.write_psd(
                tmp, cw, ch, layer_tuples, groups=group_defs if group_defs else None
            ):
                raise OSError(f"PSD encoder failed for {path.name}")

        try:
            write_atomically(path, _encode_psd)
            return True
        except OSError:
            logger.warning("PSD export failed", exc_info=True)
            return False

    @staticmethod
    def resolve_export_format(path: Path, fmt_hint: str | None) -> str:
        suffix = path.suffix.lower()
        suffix_formats = {
            ".svg": "SVG",
            ".psd": "PSD",
            ".jpg": "JPG",
            ".jpeg": "JPG",
            ".png": "PNG",
            # CT-09 E3: raster formats FileIO already encodes.
            ".tif": "TIFF",
            ".tiff": "TIFF",
            ".webp": "WEBP",
            ".bmp": "BMP",
        }
        if fmt := suffix_formats.get(suffix):
            return fmt
        hint = fmt_hint.lower() if fmt_hint else ""
        for keyword, fmt in (
            ("svg", "SVG"),
            ("psd", "PSD"),
            ("jpeg", "JPG"),
            ("tiff", "TIFF"),
            ("webp", "WEBP"),
            ("bmp", "BMP"),
        ):
            if keyword in hint:
                return fmt
        return "PNG"

    def get_composited_pixmap(self) -> QPixmap | None:
        canvas_widget = self._window.canvas
        flush = getattr(canvas_widget, "flush_and_readback", None)
        readback = getattr(canvas_widget, "get_gpu_composited_pixmap", None)
        if not callable(flush) or not callable(readback):
            return None
        pixmap = flush()
        if pixmap is None or pixmap.isNull():
            pixmap = readback()
        if pixmap is not None and not pixmap.isNull():
            return cast(QPixmap, pixmap)
        return None

    def on_document_changed_event(self, event: Any) -> None:
        if not history_failure_event_is_current(event):
            return
        self._replacement_guard.document_changed()
        self._window._document_dirty = True
        self._window._update_window_title()
        self._window.statusBar().showMessage("Document modified", 2000)

        change_type = getattr(event, "change_type", None)
        if change_type == "resize":
            area = getattr(event, "affected_area", None)
            if area and len(area) >= 4:
                w, h = area[2], area[3]
                if hasattr(self._window, "canvas") and hasattr(
                    self._window.canvas, "set_canvas_size"
                ):
                    with suppress(Exception):
                        self._window.canvas.set_canvas_size(w, h)

    @staticmethod
    def _payload_field(payload: Any, key: str, default: Any) -> Any:
        if isinstance(payload, Mapping):
            return payload.get(key, default)
        return getattr(payload, key, default)

    def on_document_saved_event(self, payload: Any) -> None:
        path = self._window._extract_document_path(payload)
        is_autosave = bool(self._payload_field(payload, "autosave", False))
        if is_autosave:
            if path:
                self._window.statusBar().showMessage(f"Autosaved {path.name}", 2000)
            return
        announcement = classify_save_outcome_announcement(payload)
        if announcement is not SaveOutcomeAnnouncement.LEGACY:
            # SAVE-ASYNC-REV-01 review-fix: DOC_SAVED is only the
            # archive-write notification and its classification fields are
            # preliminary — another DOC_SAVED handler may still edit or
            # switch the document synchronously.  The authoritative
            # outcome arrives via DOC_SAVE_OUTCOME after the broadcast;
            # applying the preliminary fields here would race it.
            # Review-fix 3: an announcement always supersedes a previously
            # applied final (saves can start without MainWindow's
            # save-start reset, e.g. HostAPI-initiated), so an old
            # covering outcome must never authorize fallback/close for
            # this new, unsettled save.  While the claim is set, direct
            # fallbacks and close-save are refused fail-closed and only a
            # matching final may settle path/dirty/title.
            self._last_manual_save_outcome = None
            if announcement is SaveOutcomeAnnouncement.PENDING:
                self._pending_save_claim = claim_from_pending_payload(payload)
            else:
                # Present-but-not-True marker: a malformed announcement is
                # NOT the legacy custom-service path.  Enter the
                # fail-closed unsettled state with a claim no final can
                # ever match.
                logger.warning(
                    "Malformed save_outcome_pending marker on DOC_SAVED; "
                    "entering fail-closed unsettled save state"
                )
                self._pending_save_claim = UNMATCHABLE_CLAIM
            return
        # Custom DocumentService implementations emit neither the pending
        # flag nor a DOC_SAVE_OUTCOME stage: payloads without the additive
        # fields keep the historical behaviour (a current/covering save).
        self._apply_manual_save_outcome(
            path,
            same_document=bool(self._payload_field(payload, "save_same_document", True)),
            covers_latest=bool(self._payload_field(payload, "save_covers_latest", True)),
        )

    def on_document_save_outcome_event(self, payload: Any) -> None:
        """Apply the authoritative final save outcome (SAVE-ASYNC-REV-01).

        Emitted by the persistence layer after the DOC_SAVED broadcast;
        the same outcome instance gates the core baseline confirmation, so
        UI state and core state can never disagree.  Handlers of this
        event must not mutate or switch the document (undefined
        behaviour).

        Review-fix 2: the payload is validated strictly and matched
        against the pending claim (normalized path + captured epoch +
        captured revision).  Unsolicited, malformed, mismatched, or
        duplicated finals are ignored fail-closed — path/dirty/title stay
        unsettled and close-save stays refused.
        """
        decision = evaluate_final_save_outcome(payload, self._pending_save_claim)
        if not decision.accepted:
            logger.warning(
                "Ignoring DOC_SAVE_OUTCOME (%s); manual-save state stays %s",
                decision.reason,
                "pending" if self._pending_save_claim is not None else "unsettled",
            )
            return
        self._pending_save_claim = None
        self._apply_manual_save_outcome(
            Path(decision.path) if decision.path else None,
            same_document=decision.same_document,
            covers_latest=decision.covers_latest,
        )

    def _apply_manual_save_outcome(
        self,
        path: Path | None,
        *,
        same_document: bool,
        covers_latest: bool,
    ) -> None:
        # Exclusive state machine (review-fix 3): applying an outcome —
        # a validated final OR a legacy DOC_SAVED — always transitions to
        # the ``final`` state with no pending claim left behind.  A stale
        # claim here would let an old DOC_SAVE_OUTCOME settle this save a
        # second time.
        self._pending_save_claim = None
        self._last_manual_save_outcome = {
            "path": path,
            "same_document": same_document,
            "covers_latest": covers_latest,
        }
        self._replacement_guard.note_save_outcome()
        if not same_document:
            # A completion for a PREVIOUS document: never retarget the
            # current document's path/dirty/title.
            if path:
                self._window.statusBar().showMessage(
                    self._window._ts("Saved previous document {name}").format(name=path.name),
                    3000,
                )
            return
        if path:
            self._window._document_path = path
        if covers_latest:
            self._window._document_dirty = False
            self._window._update_window_title()
            if path:
                self._window.statusBar().showMessage(f"Saved {path.name}", 2000)
            return
        # Same document, but edits arrived while the worker was encoding:
        # the archive is a valid save of an OLDER state.  Keep the dirty
        # marker and history untouched; the next Ctrl+S saves the latest
        # state to the same path.
        self._window._update_window_title()
        if path:
            self._window.statusBar().showMessage(
                self._window._ts("Saved {name} — newer unsaved changes remain").format(
                    name=path.name
                ),
                4000,
            )

    def on_document_loaded_event(self, payload: Any) -> None:
        self._replacement_guard.document_replaced()
        path = self._window._extract_document_path(payload)
        if path:
            self._window._document_path = path
        self._window._document_dirty = False
        self._window._update_window_title()
        if path:
            self._window.statusBar().showMessage(f"Loaded {path.name}", 2000)
        self._window._set_document_present(True)

    def on_document_created_event(self, payload: Any) -> None:
        self._replacement_guard.document_replaced()
        self._window._document_path = None
        self._window._document_dirty = False
        self._window._update_window_title()
        self._window._set_document_present(True)
        if isinstance(payload, Mapping):
            width = payload.get("width")
            height = payload.get("height")
            if width and height:
                self._window.statusBar().showMessage(f"New document {width}x{height}", 2000)

    def on_document_closed_event(self, _: Any) -> None:
        self._replacement_guard.document_replaced()
        if self._window._has_document:
            close_document_session(self._window)
        self._last_manual_save_outcome = None
        self._pending_save_claim = None
        self._window._document_path = None
        self._window._document_dirty = False
        self._window._update_window_title()
        self._window._set_document_present(False)

    def on_document_save_failed_event(self, payload: Any) -> None:
        if getattr(self._window, "_mcp_document_operation_in_flight", False):
            return
        path = self._window._extract_document_path(payload)
        if isinstance(payload, Mapping) and payload.get("reason_code") == "document_edit_pending":
            if payload.get("autosave") is True:
                return
            feedback = self._active_manual_save_feedback
            if feedback is not None and feedback.matches_path(path):
                if feedback.deferred_notice_shown:
                    return
                feedback.deferred_notice_shown = True
            self._message_box().warning(
                self._window,
                self._window._ts("Save Failed"),
                self._window._ts("Finish or cancel the current edit before saving."),
            )
            return
        error = getattr(payload, "error", None)
        if isinstance(payload, Mapping):
            error = payload.get("error", error)
        message = self._window._ts("Failed to save document")
        if path:
            message = self._window._ts("Failed to save {path}").format(path=path)
        self._message_box().critical(
            self._window,
            self._window._ts("Save Failed"),
            f"{message}\n{error or ''}".strip(),
        )

    @staticmethod
    def extract_document_path(payload: Any) -> Path | None:
        candidate = getattr(payload, "path", None)
        if candidate is None and isinstance(payload, Mapping):
            candidate = payload.get("path")
        if isinstance(candidate, str) and candidate:
            return Path(candidate)
        return None
