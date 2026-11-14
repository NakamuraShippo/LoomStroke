"""SAM 3.1 Preferences section: root folder, one-click Setup, worker lifecycle.

GUI-thin by design: all real work lives in ``Sam31SetupEngine`` and
``Sam31ServerManager``; this widget renders progress/status/errors while
the blocking operations run in Qt's worker pool.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from loomstroke.sam31 import worker_protocol as protocol
from loomstroke.sam31.readiness import Sam31Readiness
from loomstroke.sam31.runtime_config import (
    SETTING_AUTO_START,
    SETTING_ROOT_DIR,
    Sam31RuntimeConfig,
    build_sam31_runtime_config,
)
from loomstroke.sam31.server_manager import (
    WORKER_RUNNING,
    Sam31ServerError,
    get_sam31_server_manager,
)
from loomstroke.sam31.setup import Sam31SetupEngine
from loomstroke.sam31.setup.engine import SetupProgress, SetupReport

_PRIVACY_EXTERNAL_SERVICES_KEY = "privacy/allow_external_services"


class _TaskSignals(QObject):
    progress_line = Signal(str)
    task_finished = Signal(object)


class _Sam31Task(QRunnable):
    """The pool owns the runnable until it finishes, even if the dialog closes."""

    def __init__(self, task: Callable[[Callable[[str], None]], Any]) -> None:
        super().__init__()
        self.signals = _TaskSignals()
        self._task = task

    def run(self) -> None:  # noqa: D102
        try:
            result = self._task(self.signals.progress_line.emit)
        except Sam31ServerError as exc:
            result = {"error": str(exc), "errorCode": exc.code}
        except Exception as exc:  # noqa: BLE001
            result = {"error": f"{type(exc).__name__}: {exc}", "errorCode": "internal"}
        self.signals.task_finished.emit(result)


@dataclass(frozen=True, slots=True)
class _Sam31Status:
    readiness: Sam31Readiness
    worker: str
    model: str
    gpu_name: str
    point_mode: str
    error: str


def _read_status(
    manager: Any, config: Sam31RuntimeConfig, cancelled: threading.Event
) -> _Sam31Status | None:
    if cancelled.is_set():
        return None
    readiness = manager.readiness(config)
    if cancelled.is_set():
        return None
    worker = manager.worker_state()
    model = manager.last_model_state()
    gpu_name = point_mode = error = ""
    if worker == WORKER_RUNNING and not cancelled.is_set():
        try:
            gpu_name = str(manager.runtime_info().get("gpuName") or "")
            if not cancelled.is_set():
                features = manager.capabilities().get("features", {})
                point_mode = str(features.get(protocol.FEATURE_POINT_NEW_OBJECT) or "")
        except Sam31ServerError as exc:
            error = str(exc)
    return _Sam31Status(readiness, worker, model, gpu_name, point_mode, error)


class Sam31PreferencesSection(QGroupBox):
    """The "SAM 3.1" group shown at the top of the SAM settings page."""

    #: emitted after any background task fully settles (tests wait on it)
    setup_finished_for_test = Signal()

    def __init__(  # noqa: PLR0915 - linear widget construction
        self,
        settings: Any,
        *,
        translate: Callable[[str], str],
        parent: QWidget | None = None,
        manager: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._ts = translate
        self._manager = manager or get_sam31_server_manager()
        self._task: _Sam31Task | None = None
        self._cancel_event: threading.Event | None = None
        self._status_generation = 0
        self._status_pending: tuple[int, Sam31RuntimeConfig] | None = None
        self._status_running: tuple[_Sam31Task, int, Sam31RuntimeConfig, threading.Event] | None = (
            None
        )
        self.setTitle("SAM 3.1")

        form = QFormLayout(self)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.root_edit = QLineEdit()
        self.root_browse_btn = QPushButton(self._ts("Browse..."))
        self.root_browse_btn.clicked.connect(self._browse_root)
        root_row = QWidget()
        root_layout = QHBoxLayout(root_row)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(self.root_edit, 1)
        root_layout.addWidget(self.root_browse_btn)
        form.addRow(self._ts("SAM 3.1 root folder"), root_row)

        self.auto_start_check = QCheckBox(self._ts("Start SAM 3.1 server automatically"))
        form.addRow("", self.auto_start_check)

        self.status_label = QLabel("-")
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setWordWrap(True)
        form.addRow(self._ts("Status"), self.status_label)

        buttons_row = QWidget()
        buttons_layout = QHBoxLayout(buttons_row)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.setup_btn = QPushButton(self._ts("Setup"))
        self.cancel_btn = QPushButton(self._ts("Cancel"))
        self.start_btn = QPushButton(self._ts("Start"))
        self.stop_btn = QPushButton(self._ts("Stop"))
        self.restart_btn = QPushButton(self._ts("Restart"))
        self.load_model_btn = QPushButton(self._ts("Load Model"))
        self.unload_model_btn = QPushButton(self._ts("Unload Model"))
        for button in (
            self.setup_btn,
            self.cancel_btn,
            self.start_btn,
            self.stop_btn,
            self.restart_btn,
            self.load_model_btn,
            self.unload_model_btn,
        ):
            buttons_layout.addWidget(button)
        self.cancel_btn.setEnabled(False)
        form.addRow("", buttons_row)

        self.operation_label = QLabel("")
        form.addRow("", self.operation_label)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(120)
        self.log_output.setMaximumBlockCount(2000)
        form.addRow(self.log_output)

        self.setup_btn.clicked.connect(self._on_setup)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.start_btn.clicked.connect(lambda: self._run_manager_task("start"))
        self.stop_btn.clicked.connect(lambda: self._run_manager_task("stop"))
        self.restart_btn.clicked.connect(lambda: self._run_manager_task("restart"))
        self.load_model_btn.clicked.connect(lambda: self._run_manager_task("load"))
        self.unload_model_btn.clicked.connect(lambda: self._run_manager_task("unload"))
        self.root_edit.textChanged.connect(self._discard_status)
        self.root_edit.editingFinished.connect(self.refresh_status)
        self.auto_start_check.toggled.connect(self.refresh_status)

    # ── settings binding ───────────────────────────────────────────────

    def load(self) -> None:
        config = build_sam31_runtime_config(self._settings)
        with QSignalBlocker(self.root_edit), QSignalBlocker(self.auto_start_check):
            self.root_edit.setText(config.root_dir)
            self.auto_start_check.setChecked(config.auto_start)
        self.refresh_status()

    def collect_values(self) -> dict[str, Any]:
        return {
            SETTING_ROOT_DIR: self.root_edit.text().strip(),
            SETTING_AUTO_START: self.auto_start_check.isChecked(),
        }

    def _current_config(self) -> Sam31RuntimeConfig:
        base = build_sam31_runtime_config(self._settings)
        return Sam31RuntimeConfig(
            root_dir=self.root_edit.text().strip(),
            device=base.device,
            auto_start=self.auto_start_check.isChecked(),
            request_timeout_s=base.request_timeout_s,
        )

    # ── status ─────────────────────────────────────────────────────────

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.refresh_status()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt override
        self._discard_status()
        super().hideEvent(event)

    @Slot()
    def _discard_status(self) -> None:
        self._status_generation += 1
        self._status_pending = None
        if self._status_running is not None:
            _, _, _, cancelled = self._status_running
            cancelled.set()
        self.status_label.setText("-")

    @Slot()
    def refresh_status(self) -> None:
        self._discard_status()
        if self.isVisible():
            self._status_pending = self._status_generation, self._current_config()
            self._start_status_if_idle()

    def _start_status_if_idle(self) -> None:
        pending = self._status_pending
        if pending is None or self._status_running is not None or self._busy():
            return
        self._status_pending = None
        generation, config = pending
        manager = self._manager
        cancelled = threading.Event()
        task = _Sam31Task(lambda _emit: _read_status(manager, config, cancelled))
        task.signals.task_finished.connect(self._on_status_finished)
        self._status_running = task, generation, config, cancelled
        QThreadPool.globalInstance().start(task)

    @Slot(object)
    def _on_status_finished(self, result: object) -> None:
        running = self._status_running
        self._status_running = None
        if running is not None:
            _, generation, config, _ = running
            if (
                generation == self._status_generation
                and self.isVisible()
                and config == self._current_config()
            ):
                if isinstance(result, _Sam31Status):
                    self._display_status(result)
                elif isinstance(result, dict) and result.get("error"):
                    self.status_label.setText(self._ts("Status error") + f": {result['error']}")
        self._start_status_if_idle()

    def _display_status(self, status: _Sam31Status) -> None:
        parts: list[str] = []
        readiness = status.readiness
        parts.append(
            self._ts("Runtime")
            + ": "
            + (self._ts("Ready") if readiness.ready else ", ".join(readiness.blocked_reasons))
        )
        checkpoint = readiness.diagnostics.get("checkpoint")
        parts.append(
            self._ts("Checkpoint")
            + ": "
            + (str(checkpoint).rsplit("\\", 1)[-1] if checkpoint else self._ts("missing"))
        )
        parts.append(self._ts("Worker") + f": {status.worker}")
        parts.append(self._ts("Model") + f": {status.model}")
        if status.gpu_name:
            parts.append("CUDA: " + status.gpu_name)
        if status.point_mode:
            parts.append(self._ts("Point mode") + f": {status.point_mode}")
        if status.error:
            parts.append(self._ts("Status error") + f": {status.error}")
        self.status_label.setText("\n".join(parts))

    # ── task plumbing ──────────────────────────────────────────────────

    def _busy(self) -> bool:
        return self._task is not None

    def _set_busy(self, busy: bool) -> None:
        for button in (
            self.setup_btn,
            self.start_btn,
            self.stop_btn,
            self.restart_btn,
            self.load_model_btn,
            self.unload_model_btn,
            self.root_edit,
            self.root_browse_btn,
        ):
            button.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy and self._cancel_event is not None)

    def _launch(self, task: Callable[[Callable[[str], None]], Any]) -> None:
        if self._busy():
            return
        self._discard_status()
        self._set_busy(True)
        worker = _Sam31Task(task)
        worker.signals.progress_line.connect(self._on_progress_line)
        worker.signals.task_finished.connect(self._on_task_finished)
        self._task = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(str)
    def _on_progress_line(self, line: str) -> None:
        if line.startswith("@stage "):
            self.operation_label.setText(line[len("@stage ") :])
        else:
            self.log_output.appendPlainText(line)

    @Slot(object)
    def _on_task_finished(self, result: object) -> None:
        self._task = None
        self._cancel_event = None
        self._set_busy(False)
        self.operation_label.setText("")
        if isinstance(result, SetupReport):
            if result.ok:
                self.log_output.appendPlainText(self._ts("SAM 3.1 Ready"))
            else:
                message = self._ts(result.error_message)
                self.log_output.appendPlainText(f"[{result.error_code}] {message}")
                if result.error_action:
                    self.log_output.appendPlainText(self._ts(result.error_action))
        elif isinstance(result, dict) and result.get("error"):
            self.log_output.appendPlainText(f"[{result.get('errorCode')}] {result['error']}")
        self.refresh_status()
        self.setup_finished_for_test.emit()

    # ── actions ────────────────────────────────────────────────────────

    def _browse_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, self._ts("Select SAM 3.1 root folder"), self.root_edit.text()
        )
        if chosen:
            self.root_edit.setText(chosen)
            self.refresh_status()

    def _on_cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    def _on_setup(self) -> None:
        config = self._current_config()
        network_allowed = bool(self._settings.get(_PRIVACY_EXTERNAL_SERVICES_KEY, False))
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        manager = self._manager

        def task(emit: Callable[[str], None]) -> SetupReport:
            engine = Sam31SetupEngine.with_server_manager(
                config, network_allowed=network_allowed, server_manager=manager
            )

            def on_progress(progress: SetupProgress) -> None:
                emit(
                    "@stage "
                    + f"[{progress.stage_index + 1}/{progress.stage_count}] "
                    + f"{progress.label}..."
                )
                emit(f"[{progress.stage_name}] {progress.message}")

            return engine.run(on_progress, cancel_event)

        self.log_output.appendPlainText(self._ts("Starting setup..."))
        self._launch(task)

    def _run_manager_task(self, kind: str) -> None:
        config = self._current_config()
        manager = self._manager

        def task(emit: Callable[[str], None]) -> dict[str, Any]:
            emit(f"{kind}...")
            if kind == "start":
                return manager.start(config)
            if kind == "stop":
                manager.stop()
                return {"stopped": True}
            if kind == "restart":
                return manager.restart(config)
            if kind == "load":
                return manager.load_model()
            if kind == "unload":
                return manager.unload_model()
            return {}

        self._launch(task)


__all__ = ["Sam31PreferencesSection"]
