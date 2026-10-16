"""Effect Editor main window (Phase 7-a skeleton).

Top-level :class:`QWidget` for authoring ``.lsfx`` presets from an
arbitrary backend workflow. Phase 7-a delivers the skeleton: gateway
selection, a "Load workflow..." file action, error surfacing, and a
placeholder three-pane layout that later sub-phases fill in:

.. code-block:: text

    ┌────────────────────────────────────────────────────────────┐
    │ Gateway: [ComfyUI ▾]          (File • Help)                │
    ├──────────────┬───────────────────────────┬─────────────────┤
    │ Candidates   │ (center placeholder —     │ Inspector       │
    │              │  Phase 7-b fills this)    │ (Phase 7-c)     │
    │ • Slots      │                           │                 │
    │ • Parameters │                           │                 │
    │ • Outputs    │                           │                 │
    ├──────────────┴───────────────────────────┴─────────────────┤
    │ Status: (no workflow loaded)                               │
    └────────────────────────────────────────────────────────────┘

The window is created but NOT yet wired into ``app.py``. Wiring lands in
Phase 7-d once the export loop is end-to-end.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenuBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from loomstroke.plugins.ai_effect.editor.export import (
    CandidateKey,
    ExportValidationError,
    candidate_key,
    collect_author_choices,
    export_preset,
)
from loomstroke.plugins.ai_effect.editor.import_preset import (
    ImportedPresetState,
    ImportPresetError,
    load_preset_for_editing,
)
from loomstroke.plugins.ai_effect.editor.inspector import ChoiceInspector
from loomstroke.plugins.ai_effect.editor.metadata_form import MetadataForm
from loomstroke.plugins.ai_effect.editor.panes import CandidatesPane
from loomstroke.plugins.ai_effect.gateway_protocol import (
    AIGateway,
    CandidateSet,
    WorkflowLoadResult,
)
from loomstroke.plugins.ai_effect.lsfx_io import LsfxReadError, read_lsfx
from loomstroke.plugins.ai_effect.registry import GatewayRegistry, UnknownGatewayError
from loomstroke.ui.translations import translate_with_fallback

logger = logging.getLogger(__name__)


def _tr(text: str) -> str:
    return translate_with_fallback("EffectEditorWindow", text)


# Maps ``ExportValidationError`` reason strings to human-readable message
# templates (passed through ``self.tr`` at display time). Keys that embed a
# colon-separated detail (e.g. ``manifest_invalid:invalid_trigger``) are
# matched by prefix in ``_format_export_error``; bare keys match exactly.
_EXPORT_ERROR_MESSAGES: dict[str, str] = {
    "metadata_name_empty": "Preset name is required.",
    "no_outputs_selected": "At least one output must be enabled.",
}
_EXPORT_ERROR_MANIFEST_PREFIX = "manifest_invalid:"

# Phase 7-f: import reason → translated message. Prefix-indexed entries
# (``"<prefix>:"`` keys) match via ``_format_import_error`` so reasons
# carrying a detail tail ("gateway_not_registered:stub") use the same
# message template regardless of the tail value.
_IMPORT_ERROR_MESSAGES: dict[str, str] = {
    "no_raw_workflow": "Import failed: preset has no embedded workflow.",
}
_IMPORT_ERROR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("manifest_invalid:", "Import failed: manifest invalid (%s)"),
    ("gateway_not_registered:", "Import failed: gateway %s is not registered"),
    ("gateway_file_missing:", "Import failed: gateway file missing (%s)"),
    ("read_error:", "Import failed: %s"),
)


# Characters that Windows and POSIX filesystems both reject in a
# path component. Stripping them lets "Register to Manager" derive a
# filename from a free-form preset name without a QFileDialog round-trip.
_FS_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _sanitize_filename(name: str) -> str:
    """Turn a user-supplied preset name into a filesystem-safe stem.

    Strips Windows / POSIX illegal characters, collapses whitespace,
    and trims trailing dots / spaces (Windows rejects those in a stem).
    Falls back to ``"preset"`` when the name sanitises to empty so
    ``_on_register_requested`` never has to dispatch on a blank stem.
    """
    cleaned = _FS_UNSAFE_CHARS.sub("_", name).strip().strip(". ")
    if not cleaned:
        return "preset"
    return cleaned


class EffectEditorWindow(QWidget):
    """Preset authoring window (skeleton).

    Emits :attr:`workflow_loaded(dict)` whenever a workflow is successfully
    parsed, so Phase 7-b / 7-c can subscribe without reaching into the
    window's internals.
    """

    # Signal payload types are documented here because ``PySide6.Signal``
    # only accepts types registered with the Qt meta-type system; project
    # types like ``SlotCandidate`` / ``ParamChoice`` / ``pathlib.Path``
    # are not registered, so the declared payload is always ``object``
    # and the real type is communicated via this docstring.
    #
    # - workflow_loaded: dict (raw workflow JSON)
    # - candidate_selected: SlotCandidate | ParamCandidate | OutputCandidate | None
    # - candidate_include_toggled: (candidate, bool)
    # - choice_updated: (candidate, SlotChoice | ParamChoice | OutputChoice)
    # - preset_exported: pathlib.Path (freshly written .lsfx)
    workflow_loaded = Signal(dict)
    candidate_selected = Signal(object)
    candidate_include_toggled = Signal(object, bool)
    choice_updated = Signal(object, object)
    preset_exported = Signal(object)

    def __init__(
        self,
        gateway_registry: GatewayRegistry,
        parent: QWidget | None = None,
        *,
        register_target_dir: Path | None = None,
    ) -> None:
        """``register_target_dir``: optional directory that the
        "Register to Manager" button writes directly into, skipping
        the file-dialog round-trip. Typically the preset library's
        user_dir so the newly-authored ``.lsfx`` lands where the
        library's ``scan_and_load`` / refresh hooks will pick it up.
        ``None`` disables the button (it stays visible but greyed out
        with an explanatory tooltip so the affordance is discoverable)."""
        super().__init__(parent)
        self._registry = gateway_registry
        self._register_target_dir = register_target_dir
        self._current_workflow: dict[str, Any] | None = None
        self.native_workflow: dict[str, Any] | None = None
        self._current_workflow_path: Path | None = None
        self._current_candidates: CandidateSet | None = None
        # Maintained by on_choice_changed. Keyed by ``CandidateKey`` —
        # the ``(kind, source_hint)`` pair Gateway promises to keep unique
        # per workflow. Survives CandidatesPane rebuilds and Python GC
        # ``id()`` reuse, unlike the former ``id(candidate)`` key.
        self._choices_by_candidate: dict[CandidateKey, Any] = {}
        # Cached predicates for ``_refresh_export_action_state`` so that
        # name-field keystrokes do not re-scan the entire candidate tree.
        # Updated only on the signals that can actually change them
        # (``metadata_changed`` for name readiness, ``include_toggled`` +
        # workflow load for output presence).
        self._name_ready: bool = False
        self._has_outputs: bool = False
        # Phase 7-f: identity carried across an import → re-export cycle.
        # Remains None for fresh-workflow flow so re-export mints a new UUID.
        self._imported_preset_id: str | None = None
        self._imported_preset_path: Path | None = None
        # Async task that pre-warms ``object_info`` and re-detects
        # candidates with backend constraints. Stored on self so the
        # task isn't garbage-collected before it runs (asyncio holds
        # only weak refs to fire-and-forget futures via
        # ``ensure_future``); cancelled implicitly when a fresh
        # workflow load supersedes it.
        self._object_info_refresh_task: Any = None
        self.destroyed.connect(lambda *_args: self._cancel_object_info_refresh())

        self.setObjectName("EffectEditorWindow")
        self.setWindowTitle(_tr("AI Effect — Preset Editor"))
        self.resize(1100, 700)
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)
        self._menu_bar = QMenuBar(self)
        self._root_layout.addWidget(self._menu_bar)
        self._central_widget: QWidget | None = None
        self._status_bar = QStatusBar(self)
        self._status_bar.setSizeGripEnabled(False)
        self._root_layout.addWidget(self._status_bar)

        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        self._build_menus()

        self._refresh_gateway_combo()

        # Internal wiring: workflow load → candidate detection → populate
        # left pane. Kept inside __init__ so the window is self-contained.
        self.workflow_loaded.connect(self._on_workflow_loaded_internal)
        self._candidates_pane.selection_changed.connect(self.candidate_selected)
        self._candidates_pane.include_toggled.connect(self.candidate_include_toggled)
        self.candidate_selected.connect(self._on_candidate_selected)
        self._inspector.choice_changed.connect(self._on_choice_changed)
        self._metadata_form.metadata_changed.connect(self._on_metadata_readiness_changed)
        self._candidates_pane.include_toggled.connect(self._on_outputs_count_changed)
        self._refresh_export_action_state()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def menuBar(self) -> QMenuBar:  # noqa: N802 - QMainWindow compatibility
        return self._menu_bar

    def statusBar(self) -> QStatusBar:  # noqa: N802 - QMainWindow compatibility
        return self._status_bar

    def centralWidget(self) -> QWidget | None:  # noqa: N802 - QMainWindow compatibility
        return self._central_widget

    def setCentralWidget(self, widget: QWidget) -> None:  # noqa: N802 - QMainWindow compatibility
        if self._central_widget is not None:
            self._root_layout.removeWidget(self._central_widget)
            self._central_widget.hide()
            self._central_widget.deleteLater()
        self._central_widget = widget
        self._root_layout.insertWidget(1, widget, 1)

    def _build_toolbar(self) -> None:
        toolbar_host = QWidget()
        h = QVBoxLayout(toolbar_host)
        h.setContentsMargins(8, 6, 8, 6)

        row = QFrame()
        row.setFrameShape(QFrame.Shape.NoFrame)
        # Horizontal row: Gateway combo on the left, Export button on
        # the right so the "register / save preset" action is always
        # discoverable (previously only reachable via File > Export
        # preset... which was easy to miss, especially while disabled).
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        label = QLabel(_tr("Gateway"))
        label.setStyleSheet("font-weight: bold;")
        self._gateway_combo = QComboBox()
        self._gateway_combo.setMinimumWidth(200)
        # Re-run candidate detection on every Gateway switch so the
        # left pane reflects the new backend's parameter surface.
        # Gateways with no workflow file (NanoBanana) return their
        # canonical surface from ``detect_candidates({})``; gateways
        # that need a workflow (ComfyUI) return an empty CandidateSet
        # — same as the original "no workflow loaded" state. The
        # ``_refresh_gateway_combo`` populator blocks signals so this
        # does not fire spuriously at startup.
        self._gateway_combo.currentIndexChanged.connect(self._on_gateway_changed)

        row_layout.addWidget(label)
        row_layout.addWidget(self._gateway_combo)

        # Per-preset target server (Phase 10c-ui). Preferences is the
        # only URL editor; this combo selects a configured server id.
        self._endpoint_url_label = QLabel(_tr("Server"))
        self._server_combo = QComboBox()
        self._server_combo.setMinimumWidth(160)
        self._server_combo.setToolTip(
            _tr("Choose which Preferences-configured ComfyUI server runs this preset.")
        )
        self._endpoint_url_label.setVisible(False)
        self._server_combo.setVisible(False)
        row_layout.addWidget(self._endpoint_url_label)
        row_layout.addWidget(self._server_combo)
        row_layout.addStretch(1)

        from ..native_comfy.editor_button import WorkflowEditButton  # noqa: PLC0415

        self._native_workflow_button = WorkflowEditButton(self)
        row_layout.addWidget(self._native_workflow_button)

        # "Register to Manager" writes straight into the library's
        # user_dir (no file dialog) so the preset surfaces in the AI
        # Effects Manager immediately. Greyed out when the host did not
        # pass a ``register_target_dir`` (e.g. tests without a library).
        self._register_button = QPushButton(_tr("Register to Manager"))
        self._register_button.clicked.connect(self._on_register_requested)
        self._register_button.setEnabled(False)
        if self._register_target_dir is None:
            self._register_button.setToolTip(
                _tr("Preset library unavailable — use Export preset... instead")
            )
        row_layout.addWidget(self._register_button)

        # Prominent Export button — mirrors File > Export preset...
        # and shares the same enabled/disabled state via
        # _refresh_export_action_state.
        self._export_button = QPushButton(_tr("Export preset..."))
        self._export_button.clicked.connect(self._on_export_requested)
        self._export_button.setEnabled(False)
        row_layout.addWidget(self._export_button)

        h.addWidget(row)

        self._toolbar_host = toolbar_host

    def _build_central(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._toolbar_host)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left pane — candidate lists (Phase 7-b).
        self._candidates_pane = CandidatesPane()
        self._left_pane = self._candidates_pane
        splitter.addWidget(self._left_pane)

        # Centre pane — workflow info + metadata editor (Phase 7-d). The
        # header label doubles as a compact "what's loaded" cue so the
        # author doesn't need to glance at the status bar while editing
        # metadata.
        self._center_pane = QWidget()
        center_layout = QVBoxLayout(self._center_pane)
        center_layout.setContentsMargins(8, 8, 8, 8)
        center_layout.setSpacing(4)

        self._workflow_label = QLabel(_tr("Workflow: (no workflow loaded)"))
        self._workflow_label.setStyleSheet("font-weight: bold;")
        center_layout.addWidget(self._workflow_label)

        self._metadata_form = MetadataForm()
        center_layout.addWidget(self._metadata_form, stretch=1)

        splitter.addWidget(self._center_pane)

        # Right pane — inspector (Phase 7-c).
        self._inspector = ChoiceInspector()
        self._right_pane = self._inspector
        splitter.addWidget(self._right_pane)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)

        layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(central)

    def _build_statusbar(self) -> None:
        self._status_label = QLabel(_tr("(no workflow loaded)"))
        self.statusBar().addWidget(self._status_label)

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu(_tr("&File"))
        load_action = file_menu.addAction(_tr("Load workflow..."))
        load_action.triggered.connect(self._on_load_workflow_requested)
        import_action = file_menu.addAction(_tr("Import preset..."))
        import_action.triggered.connect(self._on_import_preset_requested)
        self._export_action: QAction = file_menu.addAction(_tr("Export preset..."))
        self._export_action.triggered.connect(self._on_export_requested)
        self._export_action.setEnabled(False)
        file_menu.addSeparator()
        close_action = file_menu.addAction(_tr("Close"))
        close_action.triggered.connect(self.close)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def current_gateway(self) -> AIGateway | None:
        """Return the currently selected gateway, or None when the
        registry is empty / selection is invalid."""
        gateway_id = self._gateway_combo.currentData()
        if not isinstance(gateway_id, str) or not gateway_id:
            return None
        try:
            return self._registry.get(gateway_id)
        except UnknownGatewayError:
            return None

    def select_gateway(self, gateway_id: str) -> bool:
        """Select a gateway by id when it is available in the combo."""
        combo_idx = self._gateway_combo.findData(gateway_id)
        if combo_idx < 0:
            return False
        if self._gateway_combo.currentIndex() != combo_idx:
            self._gateway_combo.setCurrentIndex(combo_idx)
        return True

    def current_workflow(self) -> dict[str, Any] | None:
        """Return the last successfully loaded workflow, or None."""
        return self._current_workflow

    def current_candidates(self) -> CandidateSet | None:
        """Return the candidate set detected for the current workflow, or
        None if detection has not run / has failed."""
        return self._current_candidates

    def current_choices(self) -> list[Any]:
        """Return every Choice object the inspector has produced so far.

        Includes choices for candidates the author has since unchecked;
        Phase 7-d filter by the include state at export time (the
        ``CandidatesPane.included()`` return value) rather than trusting
        this list alone.
        """
        return list(self._choices_by_candidate.values())

    def _current_endpoint_url(self) -> str | None:
        """Direct URL entry is no longer available in the Editor."""
        return None

    def _current_target_server_id(self) -> str | None:
        """Return the selected Preferences-managed server id."""
        data = self._server_combo.currentData()
        if isinstance(data, str) and data:
            return data
        return None

    def _apply_imported_server_choice(self, imported: ImportedPresetState) -> None:
        """Reflect ``imported.target_server_id`` / ``endpoint_url`` in
        the server picker. Direct endpoint URLs from older presets map
        to a configured server with the same URL when possible, then
        fall back to ``Local``.
        """
        self._refresh_server_combo()
        target_id = imported.target_server_id
        if isinstance(target_id, str) and target_id:
            sid_index = self._server_combo.findData(target_id)
            if sid_index >= 0:
                self._server_combo.setCurrentIndex(sid_index)
                return
        elif imported.endpoint_url:
            try:
                from loomstroke.plugins.comfyui.server_registry import get_registry  # noqa: PLC0415

                server = get_registry().get_by_url(imported.endpoint_url)
            except Exception as exc:
                logger.debug("Could not map imported endpoint_url to registry: %s", exc)
                server = None
            if server is not None:
                sid_index = self._server_combo.findData(server.id)
                if sid_index >= 0:
                    self._server_combo.setCurrentIndex(sid_index)
                    return
        self._server_combo.setCurrentIndex(max(0, self._server_combo.findData("local")))

    # ------------------------------------------------------------------
    # Semantic accessors (Phase 7-d-fix-G / review #14)
    # ------------------------------------------------------------------
    # Thin wrappers over MetadataForm / export action / status label so
    # callers (tests, future host code) do not reach through three layers
    # of private widget chains. Behaviour is strict delegation — no new
    # logic lives here.

    def preset_name(self) -> str:
        return self._metadata_form.preset_name()

    def set_preset_name(self, value: str) -> None:
        self._metadata_form.set_preset_name(value)

    def preset_description(self) -> str:
        return self._metadata_form.preset_description()

    def set_preset_description(self, value: str) -> None:
        self._metadata_form.set_preset_description(value)

    def preset_tags_text(self) -> str:
        return self._metadata_form.preset_tags_text()

    def set_preset_tags_text(self, value: str) -> None:
        self._metadata_form.set_preset_tags_text(value)

    def collect_metadata(self) -> Any:
        """Return an :class:`ExportMetadata` snapshot of the centre pane."""
        return self._metadata_form.collect()

    def status_text(self) -> str:
        return self._status_label.text()

    def is_export_enabled(self) -> bool:
        return self._export_action.isEnabled()

    def request_export(self) -> None:
        """Public entry point for the File → Export preset... action.

        Used by tests that want to drive the export path without going
        through the menu / QFileDialog (dialog is still triggered; tests
        monkey-patch it upstream).
        """
        self._on_export_requested()

    def format_export_error(self, exc: Exception) -> str:
        """Public wrapper over the internal reason→message translator."""
        return self._format_export_error(exc)

    # ------------------------------------------------------------------
    # Public API (Phase 7-f — import)
    # ------------------------------------------------------------------

    def imported_preset_id(self) -> str | None:
        """Preset UUID preserved from the last import, or ``None`` when
        the current state comes from a fresh workflow."""
        return self._imported_preset_id

    def imported_preset_path(self) -> Path | None:
        """Filesystem path of the last imported ``.lsfx``, or ``None``."""
        return self._imported_preset_path

    def format_import_error(self, exc: Exception) -> str:
        """Public wrapper over the internal import-reason translator."""
        return self._format_import_error(exc)

    def apply_imported_preset(self, imported: ImportedPresetState) -> None:
        """Load an :class:`ImportedPresetState` into the editor.

        Steps (each must succeed for the subsequent one to run):
        1. Switch the Gateway combo to ``imported.gateway_id`` if needed.
           Defensive guard only — ``load_preset_for_editing`` has already
           verified the gateway is registered.
        2. Store the raw workflow (bypassing the ``workflow_loaded``
           signal — external subscribers expect a fresh .json, not a
           preset restore).
        3. Run ``detect_candidates`` on the raw workflow.
        4. Populate the left pane with every row **unchecked** by
           default, then tick the ones whose ``gateway_ref`` matches one
           of the imported choices. Matched choices are stored in
           ``_choices_by_candidate`` keyed by the freshly-detected
           candidate.
        5. Replace metadata form contents with ``imported.metadata``.
        6. Stash ``preset_id`` / ``source_path`` for a later Export to
           preserve identity.
        """
        combo_idx = self._gateway_combo.findData(imported.gateway_id)
        if combo_idx < 0:
            self._status_label.setText(
                _tr("Import failed: gateway %s is not registered") % imported.gateway_id
            )
            return
        gateway_switched = self._gateway_combo.currentIndex() != combo_idx
        if gateway_switched:
            self._gateway_combo.setCurrentIndex(combo_idx)

        gateway = self.current_gateway()
        if gateway is None:
            # Equivalent to combo index < 0 path, but only reachable if a
            # concurrent unregister raced the import. Fail closed.
            self._status_label.setText(
                _tr("Import failed: gateway %s is not registered") % imported.gateway_id
            )
            return

        cached_info = self._cached_object_info_for(gateway)
        try:
            candidates = gateway.detect_candidates(imported.raw_workflow, object_info=cached_info)
        except Exception:
            logger.exception(
                "apply_imported_preset: detect_candidates raised for gateway=%s",
                gateway.gateway_id,
            )
            self._status_label.setText(_tr("Import failed: detect candidates error"))
            return

        self._current_workflow = imported.raw_workflow
        self.native_workflow = imported.native_workflow
        # Deliberately clear _current_workflow_path — the state came from
        # a .lsfx, not a .json workflow file. Export's default dir will
        # fall back to _imported_preset_path.
        self._current_workflow_path = None
        self._current_candidates = candidates
        self._choices_by_candidate.clear()
        self._inspector.set_candidate(None)
        self._candidates_pane.clear()
        self._candidates_pane.populate(candidates, default_included=False)

        self._restore_choices_from_import(candidates, imported.choices)

        self._metadata_form.apply_metadata(imported.metadata)
        self._imported_preset_id = imported.preset_id
        self._imported_preset_path = imported.source_path
        self._workflow_label.setText(_tr("Preset: %s") % imported.source_path.name)
        self._apply_imported_server_choice(imported)
        if gateway_switched:
            self._status_label.setText(
                _tr("Imported: %s (gateway: %s)") % (imported.source_path.name, imported.gateway_id)
            )
        else:
            self._status_label.setText(_tr("Imported: %s") % imported.source_path.name)

        # Refresh readiness cache — apply_metadata + tick operations do
        # not fire their per-signal handlers (blockSignals / suppression
        # above), so poke the derived state manually.
        self._name_ready = self._metadata_form.is_ready_for_export()
        self._has_outputs = bool(self._candidates_pane.outputs.included_candidates())
        self._refresh_export_action_state()
        # Schedule the same object_info refresh that ``_on_workflow_loaded_internal``
        # does for fresh JSON loads. Without this, the "Edit..." path
        # from the Preset Manager would surface candidates with the
        # cached / no-info shape only — defeating the whole point of
        # being able to re-edit a preset and pick up the latest backend
        # constraints.
        self._schedule_object_info_refresh(imported.raw_workflow, cached_info)
        logger.info(
            "apply_imported_preset: loaded preset %s (gateway=%s) from %s",
            imported.preset_id,
            imported.gateway_id,
            imported.source_path,
        )

    def load_workflow_file(self, path: str | Path) -> WorkflowLoadResult:
        """Programmatic entry point used by the File menu and tests.

        Routes through the current gateway's ``load_workflow_file`` and
        updates status / emits signals accordingly.
        """
        gateway = self.current_gateway()
        path_str = str(path)
        if gateway is None:
            result = WorkflowLoadResult(ok=False, error="no_gateway_selected")
            self._apply_load_result(path_str, result)
            return result

        try:
            result = gateway.load_workflow_file(path_str)
        except Exception as exc:  # pragma: no cover — gateway contract violation
            logger.exception("Gateway.load_workflow_file raised for %s", path_str)
            result = WorkflowLoadResult(ok=False, error=f"gateway_error:{exc}")
        self._apply_load_result(path_str, result)
        return result

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_load_workflow_requested(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            _tr("Load workflow"),
            "",
            _tr("Workflow files (*.json);;All files (*)"),
        )
        if not path:
            return
        self.load_workflow_file(path)

    def _on_gateway_changed(self, _index: int) -> None:
        """React to the user picking a different Gateway in the combo.

        - Workflow-less gateways (NanoBanana) return their canonical
          parameter surface from ``detect_candidates({})``, so the
          left pane populates immediately on switch.
        - Workflow-driven gateways (ComfyUI) return an empty
          ``CandidateSet`` from ``detect_candidates({})`` until the
          user loads a workflow file — matching the editor's
          original "no workflow loaded" UX.

        Re-running detection on every switch also clears stale
        candidates from the previous gateway. The previous
        ``_current_workflow`` is reused only when it is the right
        shape for the new gateway (i.e. NanoBanana ignores it; for
        ComfyUI it is fine to retain because the workflow originally
        came from a ComfyUI file).
        """
        gateway = self.current_gateway()
        # Toggle the per-preset endpoint URL input. Only ComfyUI uses
        # it today; new gateways that want author-configurable
        # endpoints can opt in by claiming ``gateway_id == "comfyui"``
        # or by extending this list. The check is independent of the
        # candidate refresh below so the field is shown / hidden
        # before the pane repopulates.
        supports_endpoint_url = gateway is not None and gateway.gateway_id == "comfyui"
        self._endpoint_url_label.setVisible(supports_endpoint_url)
        self._server_combo.setVisible(supports_endpoint_url)
        if not supports_endpoint_url:
            self._server_combo.setCurrentIndex(0)
        else:
            self._refresh_server_combo()
        if gateway is None:
            self._current_candidates = None
            self._candidates_pane.clear()
            self._inspector.set_candidate(None)
            self._choices_by_candidate.clear()
            return
        # Reuse the previously-loaded workflow when present so a
        # ComfyUI → ComfyUI swap (rare but possible if more
        # ComfyUI-shaped gateways are added) keeps the existing
        # parameter selection. NanoBanana ignores the workflow arg.
        workflow = self._current_workflow or {}
        self._on_workflow_loaded_internal(workflow)

    def _on_import_preset_requested(self) -> None:
        """File menu entry point for the Phase 7-f import flow.

        Opens a file picker, runs :func:`load_preset_for_editing`, and
        hands the resulting state to :meth:`apply_imported_preset`.
        Any :class:`ImportPresetError` is surfaced in the status bar;
        the existing editor state is preserved on failure.
        """
        path, _filter = QFileDialog.getOpenFileName(
            self,
            _tr("Import preset"),
            "",
            _tr("LoomStroke effect (*.lsfx);;All files (*)"),
        )
        if not path:
            return
        try:
            imported = load_preset_for_editing(Path(path), self._registry)
        except ImportPresetError as exc:
            self._status_label.setText(self._format_import_error(exc))
            logger.warning("Import preset failed for %s: %s", path, exc)
            return
        self.apply_imported_preset(imported)

    def _on_workflow_loaded_internal(self, workflow: dict[str, Any]) -> None:
        """Run candidate detection and populate the left pane.

        Called via the workflow_loaded signal (connected in __init__) so
        external subscribers still see the signal fire first, in case they
        want to observe the raw workflow before candidates are derived.

        Two-pass behaviour for backends that expose introspection (e.g.
        ComfyUI's ``/object_info``):

        1. **Synchronous first pass** — detect candidates with whatever
           ``object_info`` the gateway already has cached. Lets the
           Editor's left pane populate immediately on workflow load
           even if the backend connection is slow / unavailable.
        2. **Async refresh** — if the gateway's underlying service
           exposes ``get_object_info`` (ComfyUIGateway does, via
           ``ExtendedComfyUIService``), schedule a fetch and re-run
           detect once the cache is warm. Param widgets that started
           with the constraint-less defaults get repopulated with
           proper min / max / choices the moment the fetch resolves.

        Pre-warm failures are best-effort: a missing service / disabled
        privacy consent / network drop logs at WARNING and leaves the
        first-pass candidates in place rather than crashing the load.
        """
        gateway = self.current_gateway()
        if gateway is None:
            return
        cached_info = self._cached_object_info_for(gateway)
        try:
            candidates = gateway.detect_candidates(workflow, object_info=cached_info)
        except Exception:
            logger.exception(
                "Gateway.detect_candidates raised for gateway=%s",
                gateway.gateway_id,
            )
            self._current_candidates = None
            self._candidates_pane.clear()
            self._inspector.set_candidate(None)
            self._choices_by_candidate.clear()
            self._status_label.setText(_tr("Detect candidates failed (see log)"))
            return
        # Fresh workflow → drop any choice state accumulated from the
        # previous one. Candidate identity is not preserved across
        # detections, so the old dict is no longer meaningful.
        self._choices_by_candidate.clear()
        self._inspector.set_candidate(None)
        self._current_candidates = candidates
        self._candidates_pane.populate(candidates)
        # ``populate`` does not fire ``include_toggled`` for the
        # auto-included candidates, so the cached readiness flag has
        # to be refreshed manually here. Without this, gateways like
        # NanoBanana whose outputs are auto-included on detection
        # leave Export / Register greyed out until the author touches
        # an output checkbox.
        self._has_outputs = bool(self._candidates_pane.outputs.included_candidates())
        self._refresh_export_action_state()

        self._schedule_object_info_refresh(workflow, cached_info)

    def _schedule_object_info_refresh(
        self,
        workflow: dict[str, Any],
        cached_info: dict[str, Any] | None,
    ) -> None:
        """Kick off the async ``object_info`` refresh.

        Skips when the gateway has no service / no ``get_object_info``
        hook, or when the cache was already warm at first-pass time
        (the first-pass candidates already carry the right constraints
        — no need to re-detect). Otherwise schedules
        :meth:`_refresh_candidates_with_object_info` and updates the
        status bar so the author sees the work in progress.
        """
        gateway = self.current_gateway()
        if gateway is None:
            return
        service = getattr(gateway, "_service", None)
        if service is None or not hasattr(service, "get_object_info"):
            return
        if cached_info:
            return
        connection = getattr(service, "_connection", None)
        is_connected_fn = getattr(connection, "is_connected", None) if connection else None
        connected = is_connected_fn() if callable(is_connected_fn) else False
        from loomstroke.plugins.comfyui.server_registry import get_registry  # noqa: PLC0415
        from loomstroke.plugins.comfyui.session_target import active_url  # noqa: PLC0415

        if connected is not True and not active_url(get_registry()):
            self._status_label.setText(
                _tr(
                    "Connect ComfyUI to apply backend metadata "
                    "(parameter constraints will load on next workflow open)."
                )
            )
            return
        try:
            import asyncio  # noqa: PLC0415

            # Cancel any in-flight refresh from a previous workflow
            # load — concurrent fetches against the shared
            # ExtendedComfyUIService can race on _object_info_cache
            # mutation, and the previous task's result is no longer
            # relevant once the workflow has changed.
            previous = self._object_info_refresh_task
            if previous is not None and not previous.done():
                previous.cancel()
            # Store on self so the task isn't garbage-collected before
            # it gets a chance to run; supersedes the previous in-flight
            # refresh when a fresh workflow is loaded mid-fetch.
            self._object_info_refresh_task = asyncio.ensure_future(
                self._refresh_candidates_with_object_info(workflow)
            )
            # Visible cue: the author can tell the Editor is fetching
            # backend metadata and that the ranges they're about to
            # see in the inspector are about to change. Mirrors the
            # ComfyUI panel's ``Connecting...`` → ``Connected``
            # transition.
            self._status_label.setText(_tr("Fetching backend metadata..."))
        except RuntimeError:
            # No running event loop (e.g. test driving this method
            # without qasync). The first pass already populated the
            # panel with constraint-less candidates — that's the best
            # we can do without a loop.
            logger.debug(
                "Editor: no running event loop for object_info refresh; "
                "first-pass detect_candidates remains in effect"
            )

    def _cancel_object_info_refresh(self) -> None:
        task = self._object_info_refresh_task
        if task is not None and not task.done():
            task.cancel()
        self._object_info_refresh_task = None

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        self._cancel_object_info_refresh()
        super().closeEvent(event)

    @staticmethod
    def _cached_object_info_for(gateway: AIGateway) -> dict[str, Any] | None:
        """Return the gateway's already-cached object_info, if any.

        Looks up ``service._object_info_cache`` on the underlying
        ExtendedComfyUIService — present after the first connect or any
        previous Effect Panel / Editor session in the same app launch.
        Returns ``None`` when no cache is available so the caller can
        decide whether to schedule a fetch.
        """
        service = getattr(gateway, "_service", None)
        if service is None:
            return None
        from loomstroke.plugins.comfyui.server_registry import get_registry  # noqa: PLC0415
        from loomstroke.plugins.comfyui.session_target import active_url  # noqa: PLC0415

        url = active_url(get_registry())
        if url and getattr(getattr(service, "_connection", None), "base_url", None) != url:
            return None
        cache = getattr(service, "_object_info_cache", None)
        if cache is None:
            return None
        nodes = getattr(cache, "nodes", None)
        models = getattr(cache, "models", None)
        if not isinstance(nodes, dict) or not nodes:
            return None
        return {"nodes": nodes, "models": models if isinstance(models, dict) else {}}

    async def _refresh_candidates_with_object_info(  # noqa: PLR0911 - guard chain over backend availability
        self, workflow: dict[str, Any]
    ) -> None:
        """Fetch ``object_info`` from the gateway's service and rebuild
        the candidate panel with the richer constraint metadata.

        **Passive mode**: this method does NOT initiate a connection.
        It only proceeds when the gateway's service already has a
        connected ``_connection`` (i.e. the user has clicked Connect
        on the ComfyUI panel). Otherwise it surfaces a status hint
        telling the author to connect first.

        Why passive? An earlier version of this method called
        ``service.initialize()`` itself, modelled after
        :meth:`ComfyUIMainPanel.auto_connect`. That worked when the
        Editor was the only client, but the moment the user opened
        the AI Effects Manager (which opens the Editor) and then
        clicked Connect on the ComfyUI panel, both parties raced on
        the shared ``_connection`` and the GUI froze (the qasync loop
        ended up waiting on a Qt round-trip that needed the same loop
        to make progress). Letting the ComfyUI panel be the sole
        owner of connection lifecycle eliminates the race. The
        constraint refresh then happens passively: as soon as the
        cache fills (post-Connect), any subsequent workflow load /
        preset reload picks it up via the synchronous first-pass
        path in ``_cached_object_info_for``.
        """
        gateway = self.current_gateway()
        if gateway is None or self._current_workflow is not workflow:
            # Workflow changed or gateway dropped while we awaited —
            # the new state has its own refresh in flight.
            return
        service = getattr(gateway, "_service", None)
        if service is None:
            return
        from loomstroke.plugins.comfyui.server_registry import get_registry  # noqa: PLC0415
        from loomstroke.plugins.comfyui.session_target import active_url  # noqa: PLC0415

        if active_url(get_registry()):
            await service.initialize()
            await service.connect()
        connection = getattr(service, "_connection", None)
        is_connected_fn = getattr(connection, "is_connected", None) if connection else None
        connected = is_connected_fn() if callable(is_connected_fn) else False
        if connected is not True:
            # No live connection — don't trigger one ourselves. Hand
            # the user the next step instead. When they click Connect
            # on the ComfyUI panel and reload the workflow, the
            # synchronous first pass will pick up the cached
            # object_info via _cached_object_info_for.
            self._status_label.setText(
                _tr(
                    "Connect ComfyUI to apply backend metadata "
                    "(parameter constraints will load on next workflow open)."
                )
            )
            return
        try:
            object_info = await service.get_object_info()
        except Exception:
            logger.warning(
                "Editor: object_info fetch failed; param widgets will use defaults",
                exc_info=True,
            )
            self._status_label.setText(_tr("Backend metadata fetch failed"))
            return
        if not isinstance(object_info, dict) or not object_info:
            self._status_label.setText(_tr("Backend returned no metadata; using defaults"))
            return
        nodes = object_info.get("nodes") if "nodes" in object_info else object_info
        if not isinstance(nodes, dict) or not nodes:
            self._status_label.setText(_tr("Backend returned no metadata; using defaults"))
            return
        # Re-detect with the freshly-fetched info. If the user has
        # already started ticking candidates, the choice dict is keyed
        # by ``(kind, source_hint)`` and survives this rebuild — but the
        # Inspector selection points at a stale candidate object, so we
        # clear it to avoid editing a candidate that's been replaced by
        # a richer-typed twin.
        if self._current_workflow is not workflow:
            return
        try:
            candidates = gateway.detect_candidates(workflow, object_info=object_info)
        except Exception:
            logger.exception("Editor: object_info-aware detect_candidates raised")
            return
        self._inspector.set_candidate(None)
        self._current_candidates = candidates
        self._candidates_pane.populate(candidates)
        # Visible cue: how many parameters now carry backend constraints
        # so the author has a quick sanity check that the fetch actually
        # changed something.
        constrained = sum(
            1
            for p in candidates.parameters
            if p.suggested_range is not None or p.suggested_choices is not None
        )
        self._status_label.setText(
            _tr("Backend metadata applied: %d parameter(s) constrained") % constrained
        )
        logger.info(
            "Editor: re-populated candidates with object_info "
            "(parameters=%d slots=%d outputs=%d constrained=%d)",
            len(candidates.parameters),
            len(candidates.input_slots),
            len(candidates.outputs),
            constrained,
        )

    def _on_choice_changed(self, candidate: Any, choice: Any) -> None:
        """Store the edited Choice and re-emit ``choice_updated``."""
        if candidate is None:
            return
        self._choices_by_candidate[candidate_key(candidate)] = choice
        self.choice_updated.emit(candidate, choice)

    def _on_candidate_selected(self, candidate: Any) -> None:
        """Load the candidate inspector, preferring imported saved values."""
        if candidate is None:
            self._inspector.set_candidate(None)
            return
        choice = self._choices_by_candidate.get(candidate_key(candidate))
        self._inspector.set_candidate(candidate, choice)

    def _on_export_requested(self) -> None:
        """File menu entry point: validate, prompt for path, write ``.lsfx``.

        Any ``ExportValidationError`` is surfaced in the status bar — the
        dialog stays open so the author can fix the issue without losing
        form state. Unexpected I/O errors are logged and shown as a
        generic "Export failed" message.
        """
        gateway = self.current_gateway()
        workflow = self._current_workflow
        # Workflow-less gateways (NanoBanana — Gemini API has no
        # author-loadable workflow file) skip the workflow-required
        # gate. ``export_preset`` and the gateway's
        # ``apply_author_choices`` already accept ``raw_workflow=None``
        # so a None passes straight through. Mirrors the same
        # ``gateway_id == "comfyui"`` hardcode the readiness gate uses.
        needs_workflow = gateway is not None and gateway.gateway_id == "comfyui"
        if gateway is None or (needs_workflow and workflow is None):
            self._status_label.setText(_tr("Export failed: no workflow loaded"))
            return

        included = self._candidates_pane.included()
        try:
            choices = collect_author_choices(included, dict(self._choices_by_candidate))
        except ValueError as exc:
            logger.exception("collect_author_choices failed")
            self._status_label.setText(self._format_export_error(exc))
            return

        metadata = self._metadata_form.collect()
        suggested_name = f"{metadata.name or 'preset'}.lsfx"
        # Prefer the imported preset's directory so a re-export targets the
        # same file location by default. Falls back to the .json workflow
        # directory, then CWD.
        if self._imported_preset_path is not None:
            default_dir = str(self._imported_preset_path.parent)
        elif self._current_workflow_path is not None:
            default_dir = str(self._current_workflow_path.parent)
        else:
            default_dir = ""
        default_path = str(Path(default_dir) / suggested_name) if default_dir else suggested_name
        path, _filter = QFileDialog.getSaveFileName(
            self,
            _tr("Export preset"),
            default_path,
            _tr("LoomStroke effect (*.lsfx);;All files (*)"),
        )
        if not path:
            return
        target_path = Path(path)
        # Append rather than swap suffix: user-entered ``foo.v2.backup``
        # should become ``foo.v2.backup.lsfx``, not ``foo.v2.lsfx``.
        if target_path.suffix.lower() != ".lsfx":
            target_path = target_path.with_name(target_path.name + ".lsfx")

        try:
            written = export_preset(
                target_path,
                gateway,
                workflow,
                choices,
                metadata,
                preset_id=self._imported_preset_id,
                icon_bytes=metadata.icon_bytes,
                preview_bytes=metadata.preview_bytes,
                endpoint_url=self._current_endpoint_url(),
                target_server_id=self._current_target_server_id(),
                native_workflow=self.native_workflow,
            )
        except ExportValidationError as exc:
            self._status_label.setText(self._format_export_error(exc))
            return
        except (OSError, ValueError) as exc:
            logger.exception("export_preset failed for %s", target_path)
            self._status_label.setText(self._format_export_error(exc))
            return

        self._status_label.setText(_tr("Exported: %s") % written.name)
        self.preset_exported.emit(written)

    def _on_register_requested(self) -> None:  # noqa: PLR0911
        """Register the current configuration with the preset library.

        Writes the ``.lsfx`` directly into ``_register_target_dir``
        (typically the preset library's user_dir) — no file dialog, no
        path prompt. Reuses :func:`export_preset` so validation,
        atomic-write, and writeback-verify all share the same code
        path as the dialog-driven Export button. Emits the same
        :attr:`preset_exported` signal so the host's library-sync hook
        runs whether the file came from Register or Export.

        Skips silently (with a status-bar nudge) when the host did not
        pass a ``register_target_dir`` — the Editor stays usable as a
        standalone export tool in that configuration.
        """
        target_dir = self._register_target_dir
        if target_dir is None:
            self._status_label.setText(_tr("Register: preset library unavailable in this session"))
            return
        gateway = self.current_gateway()
        workflow = self._current_workflow
        # Same workflow-less bypass as ``_on_export_requested`` — the
        # Register path shares its readiness contract with Export.
        needs_workflow = gateway is not None and gateway.gateway_id == "comfyui"
        if gateway is None or (needs_workflow and workflow is None):
            self._status_label.setText(_tr("Register failed: no workflow loaded"))
            return
        included = self._candidates_pane.included()
        try:
            choices = collect_author_choices(included, dict(self._choices_by_candidate))
        except ValueError as exc:
            logger.exception("collect_author_choices failed")
            self._status_label.setText(self._format_export_error(exc))
            return
        metadata = self._metadata_form.collect()

        # Filename derivation: preserve the imported preset's filename
        # on a re-edit (``apply_imported_preset`` seeds
        # ``_imported_preset_path``) so saving over an existing library
        # entry lands on the same file instead of creating a
        # "My Effect (2).lsfx" duplicate. Fresh-authoring case derives
        # from the preset name with a conservative filesystem-safe
        # sanitisation.
        if (
            self._imported_preset_path is not None
            and self._imported_preset_path.parent == target_dir
        ):
            target_filename = self._imported_preset_path.name
        else:
            safe_name = _sanitize_filename(metadata.name or "preset")
            target_filename = f"{safe_name}.lsfx"
        target_path = target_dir / target_filename

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.exception("Register failed: could not create target dir %s", target_dir)
            self._status_label.setText(_tr("Register failed: %s") % exc)
            return

        try:
            written = export_preset(
                target_path,
                gateway,
                workflow,
                choices,
                metadata,
                preset_id=self._imported_preset_id,
                icon_bytes=metadata.icon_bytes,
                preview_bytes=metadata.preview_bytes,
                endpoint_url=self._current_endpoint_url(),
                target_server_id=self._current_target_server_id(),
                native_workflow=self.native_workflow,
            )
        except ExportValidationError as exc:
            self._status_label.setText(self._format_export_error(exc))
            return
        except (OSError, ValueError) as exc:
            logger.exception("export_preset (register path) failed for %s", target_path)
            self._status_label.setText(self._format_export_error(exc))
            return

        # Defensive: confirm the file is actually on disk before
        # claiming success. ``export_preset`` already self-verifies via
        # readback, but this guards against the (rare) case where
        # something asynchronously removes the file between writeback
        # and the user hitting OK on a follow-up dialog.
        if not written.exists():
            logger.error(
                "Register reported success but file is missing on disk: %s",
                written,
            )
            self._status_label.setText(
                _tr("Register failed: file missing after write (%s)") % written.name
            )
            return
        # Track the registered preset so a subsequent Register re-uses
        # the same filename + preset_id rather than minting a new
        # entry every time the user clicks the button.
        self._imported_preset_path = written
        # Pull the freshly-minted preset_id back from the on-disk file
        # so re-Register stays on the same library entry. Without this,
        # ``export_preset(preset_id=None)`` would mint a new UUID on
        # the next click and the in-memory library would accumulate
        # orphan entries until restart.
        try:
            roundtrip = read_lsfx(written, source="user")
            self._imported_preset_id = roundtrip.preset_id
        except (LsfxReadError, OSError):
            logger.debug("Register: could not read back preset_id", exc_info=True)
        logger.info("Register wrote preset to %s", written)
        self._status_label.setText(_tr("Registered: %s") % written.name)
        # Reuse preset_exported so the host's library-sync hook fires
        # without needing a second dedicated signal. The host is
        # responsible for refreshing any open Preset Manager window.
        self.preset_exported.emit(written)

    def _format_export_error(self, exc: Exception) -> str:
        """Translate a raw export exception into a user-facing status line.

        Handles three tiers:
        - known reason key → specific translated message
        - ``manifest_invalid:<detail>`` prefix → generic manifest-validation line
        - anything else → the generic "Export failed: %s" fallback so
          unexpected failures are still surfaced instead of swallowed
        """
        reason = str(exc)
        known = _EXPORT_ERROR_MESSAGES.get(reason)
        if known is not None:
            return _tr(known)
        if reason.startswith(_EXPORT_ERROR_MANIFEST_PREFIX):
            detail = reason[len(_EXPORT_ERROR_MANIFEST_PREFIX) :]
            return _tr("Exported manifest failed validation (internal): %s") % detail
        return _tr("Export failed: %s") % reason

    def _format_import_error(self, exc: Exception) -> str:
        """Translate an :class:`ImportPresetError` into a status line.

        Same three-tier layout as :meth:`_format_export_error`: exact
        reason match → prefix match (uses the ``_IMPORT_ERROR_PREFIXES``
        table) → generic fallback. Keeps the status bar messaging
        symmetric with the export side so authors see consistent
        phrasing across failure kinds.
        """
        reason = str(exc)
        known = _IMPORT_ERROR_MESSAGES.get(reason)
        if known is not None:
            return _tr(known)
        for prefix, template in _IMPORT_ERROR_PREFIXES:
            if reason.startswith(prefix):
                detail = reason[len(prefix) :]
                return _tr(template) % detail
        return _tr("Import failed: %s") % reason

    def _restore_choices_from_import(
        self,
        candidates: CandidateSet,
        imported_choices: Any,
    ) -> None:
        """Tick each imported choice's matching candidate + store the choice.

        Matching is by ``gateway_ref`` equality. A choice whose
        ``gateway_ref`` does not match any detected candidate is dropped
        silently (debug log only) — the candidate no longer exists in
        the current workflow.
        """
        self._restore_section_choices(
            candidates.input_slots,
            imported_choices.input_slots,
            self._candidates_pane.slots,
        )
        self._restore_section_choices(
            candidates.parameters,
            imported_choices.parameters,
            self._candidates_pane.parameters,
        )
        self._restore_section_choices(
            candidates.outputs,
            imported_choices.outputs,
            self._candidates_pane.outputs,
        )

    def _restore_section_choices(
        self,
        detected: list[Any],
        imported_section: list[Any],
        list_view: Any,
    ) -> None:
        for choice in imported_section:
            match = _match_by_gateway_ref(detected, choice.gateway_ref)
            if match is None:
                logger.debug(
                    "Import dropped choice internal_id=%s: no candidate matches ref=%s",
                    getattr(choice, "internal_id", "?"),
                    choice.gateway_ref,
                )
                continue
            self._choices_by_candidate[candidate_key(match)] = choice
            list_view.set_included(match, True)

    def _refresh_export_action_state(self) -> None:  # noqa: PLR0912
        # PLR0912: readiness fanout feeds three buttons (Export / Register
        # action + button), each with its own enable + tooltip branch —
        # extracting a helper would duplicate state checks.
        """Toggle the Export action + button from cached readiness flags.

        Conditions: gateway selected AND workflow loaded AND metadata.name
        present AND at least one output ticked. The last two come from
        :attr:`_name_ready` / :attr:`_has_outputs` which are updated only
        when their contributing signal fires, so this method is O(1) and
        safe to call on every keystroke.

        When disabled, the toolbar button tooltip enumerates the missing
        prerequisites so the author does not have to guess why the button
        cannot be pressed.
        """
        action = getattr(self, "_export_action", None)
        if action is None:
            return
        gateway = self.current_gateway()
        # Workflow-driven gateways (ComfyUI) require an author-loaded
        # ``.json`` before export. Workflow-less gateways (NanoBanana
        # — the Gemini API has no author-loadable workflow file
        # concept) skip this gate so a fresh NanoBanana preset does
        # not stay greyed out forever waiting for a workflow load
        # that will never happen. Mirrors the existing
        # ``gateway_id == "comfyui"`` hardcode in
        # ``_on_gateway_changed`` (server picker visibility).
        needs_workflow = gateway is not None and gateway.gateway_id == "comfyui"
        self._native_workflow_button.refresh()
        workflow_ok = self._current_workflow is not None or not needs_workflow
        ready = gateway is not None and workflow_ok and self._name_ready and self._has_outputs
        action.setEnabled(ready)
        button = getattr(self, "_export_button", None)
        missing: list[str] = []
        if not ready:
            if gateway is None:
                missing.append(_tr("select a Gateway"))
            if needs_workflow and self._current_workflow is None:
                missing.append(_tr("load a workflow"))
            if not self._name_ready:
                missing.append(_tr("enter a preset name"))
            if not self._has_outputs:
                missing.append(_tr("tick at least one output"))
        if button is not None:
            button.setEnabled(ready)
            if ready:
                button.setToolTip(_tr("Export the configured preset as a .lsfx file."))
            else:
                button.setToolTip(_tr("Export is disabled: %s.") % ", ".join(missing))
        register_btn = getattr(self, "_register_button", None)
        if register_btn is not None:
            # Register gate: same prerequisites as Export, plus the
            # register_target_dir has to be configured by the host.
            has_target = self._register_target_dir is not None
            register_btn.setEnabled(ready and has_target)
            if not has_target:
                register_btn.setToolTip(
                    _tr("Preset library unavailable — use Export preset... instead")
                )
            elif ready:
                register_btn.setToolTip(
                    _tr("Save this preset directly into the AI Effects Manager (no file dialog).")
                )
            else:
                register_btn.setToolTip(_tr("Register is disabled: %s.") % ", ".join(missing))

    def _on_metadata_readiness_changed(self) -> None:
        """Refresh the cached name-ready flag and the Export action state.

        Wired to :attr:`MetadataForm.metadata_changed`, which fires on
        every field edit (including non-name fields). Early-out when the
        name-ready bit has not flipped so common "type another char of
        description" cases stay cheap.
        """
        new_ready = self._metadata_form.is_ready_for_export()
        if new_ready == self._name_ready:
            return
        self._name_ready = new_ready
        self._refresh_export_action_state()

    def _on_outputs_count_changed(self, _candidate: Any = None, _included: bool = True) -> None:
        """Refresh the cached has-outputs flag and the Export action state.

        Wired to :attr:`CandidatesPane.include_toggled`. Only the outputs
        list contributes to export readiness, so scanning that single
        section is sufficient — we do not build a full CandidateSet for
        every toggle.
        """
        new_has = bool(self._candidates_pane.outputs.included_candidates())
        if new_has == self._has_outputs:
            return
        self._has_outputs = new_has
        self._refresh_export_action_state()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refresh_gateway_combo(self) -> None:
        self._gateway_combo.blockSignals(True)
        try:
            self._gateway_combo.clear()
            for gateway_id in self._registry.list_registered():
                gateway = self._registry.get(gateway_id)
                display = f"{gateway.display_name} ({gateway_id})"
                self._gateway_combo.addItem(display, gateway_id)
        finally:
            self._gateway_combo.blockSignals(False)
        # ``blockSignals(True)`` above also suppresses the
        # ``currentIndexChanged`` Qt fires implicitly when ``addItem``
        # populates an empty combo — without this manual call the
        # dependent widgets (Phase 10c-ui server picker visibility,
        # the per-preset endpoint row) would stay in their __init__
        # default ``setVisible(False)`` state until the user touched
        # the gateway combo, which never happens when ComfyUI is the
        # only registered gateway.
        self._on_gateway_changed(self._gateway_combo.currentIndex())

    def _refresh_server_combo(self) -> None:
        current_id = self._server_combo.currentData()
        try:
            from loomstroke.plugins.comfyui.server_registry import (  # noqa: PLC0415
                DEFAULT_LOCAL_COMFYUI_URL,
                ComfyUIServer,
                get_registry,
            )

            registry = get_registry()
            if not registry.list_all():
                registry.add(
                    ComfyUIServer(
                        id="local",
                        name="Local",
                        url=DEFAULT_LOCAL_COMFYUI_URL,
                        auto_detect=True,
                        is_default=True,
                    )
                )
            servers = registry.list_all()
            default_id = registry.default_server_id or "local"
        except Exception as exc:
            logger.warning("Failed to refresh ComfyUI server combo: %s", exc)
            servers = []
            default_id = "local"

        self._server_combo.blockSignals(True)
        try:
            self._server_combo.clear()
            for server in servers:
                self._server_combo.addItem(f"{server.name} — {server.url}", server.id)
            if self._server_combo.count() == 0:
                self._server_combo.addItem("Local — http://127.0.0.1:8188", "local")
            index = self._server_combo.findData(current_id)
            if index < 0:
                index = self._server_combo.findData(default_id)
            self._server_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._server_combo.blockSignals(False)

    def _apply_load_result(self, path_str: str, result: WorkflowLoadResult) -> None:
        self.native_workflow = None
        if result.ok and isinstance(result.workflow, dict):
            new_path = Path(path_str)
            # Reloading the same path must not wipe metadata the author has
            # been editing — only a different workflow file triggers a
            # prefill + reset. Compare before mutating _current_workflow_path.
            is_new_workflow = self._current_workflow_path != new_path
            self._current_workflow = result.workflow
            self._current_workflow_path = new_path
            # Phase 7-f: loading a fresh .json workflow breaks the identity
            # link to any previously imported preset. Subsequent Export
            # therefore mints a new preset_id (None → uuid4() in export_preset).
            self._imported_preset_id = None
            self._imported_preset_path = None
            self._status_label.setText(_tr("Loaded: %s") % self._current_workflow_path.name)
            self._workflow_label.setText(_tr("Workflow: %s") % self._current_workflow_path.name)
            if is_new_workflow:
                self._metadata_form.set_defaults(self._current_workflow_path.stem)
            self.workflow_loaded.emit(result.workflow)
            # Resync cached readiness flags now that candidate detection
            # (via workflow_loaded) and metadata.set_defaults may have
            # changed them without firing their usual signals in order.
            self._name_ready = self._metadata_form.is_ready_for_export()
            self._has_outputs = bool(self._candidates_pane.outputs.included_candidates())
            self._refresh_export_action_state()
            logger.info("EffectEditor loaded workflow from %s", path_str)
        else:
            self._current_workflow = None
            self._current_workflow_path = None
            self._current_candidates = None
            self._candidates_pane.clear()
            self._inspector.set_candidate(None)
            self._choices_by_candidate.clear()
            self._workflow_label.setText(_tr("Workflow: (no workflow loaded)"))
            # Deliberately no set_defaults(""): a failed load usually means
            # the user mistyped a path or dropped in a corrupt file, and
            # discarding the metadata they were editing would be a surprise.
            # Candidate / inspector state is tied to the raw workflow and
            # is still reset above.
            # ``_candidates_pane.clear`` drops all outputs without firing
            # include_toggled per row, so refresh the cached flag manually.
            self._has_outputs = False
            reason = result.error or "unknown"
            self._status_label.setText(_tr("Load failed: %s") % reason)
            self._refresh_export_action_state()
            logger.warning("EffectEditor failed to load workflow %s: %s", path_str, reason)


def _match_by_gateway_ref(candidates: list[Any], target: dict[str, Any]) -> Any | None:
    """Return the first candidate with ``gateway_ref`` equal to ``target``.

    Used by the Phase 7-f import path to reattach imported Choices to
    the candidates ``detect_candidates`` rebuilt from the raw workflow.
    Dict equality is the matching key (not identity / source_hint) so
    the match survives changes in candidate ordering between detects.
    """
    for c in candidates:
        if getattr(c, "gateway_ref", None) == target:
            return c
    return None


__all__ = ["EffectEditorWindow"]
