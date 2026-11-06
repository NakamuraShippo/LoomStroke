"""Session-only continuous-generation controls for the selected AI Effect."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.translations import translated_literal_domain

MODES = {"count": "Specified count", "cycle": "One full cycle", "infinite": "Until stopped"}
PROGRESS = "Completed {completed} / {total}"


def tr(source: str) -> str:
    return translated_literal_domain("BatchGenerationControls", source, use_fallback=True)


class BatchGenerationControls(QWidget):
    start_requested = Signal(str, int)
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False
        # Set by ``mount``; returns the LivePrompt cycle length or None.
        self.cycle_provider = None
        self._status = ""
        self._status_values: dict = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(self)
        layout.addWidget(self.title)
        options = QHBoxLayout()
        self.mode = QComboBox(self)
        self.mode.setObjectName("aiBatchMode")
        for value in MODES:
            self.mode.addItem("", value)
        self.mode.setMinimumContentsLength(0)
        self.mode.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.count = QSpinBox(self)
        self.count.setObjectName("aiBatchCount")
        self.count.setRange(1, 1000000)
        self.count.setValue(10)
        options.addWidget(self.mode, 1)
        options.addWidget(self.count)
        layout.addLayout(options)
        self.plan = QLabel(self)
        self.plan.setObjectName("aiBatchPlan")
        self.plan.setWordWrap(True)
        layout.addWidget(self.plan)
        actions = QHBoxLayout()
        self.start = QPushButton(self)
        self.start.setObjectName("aiBatchStart")
        self.stop = QPushButton(self)
        self.stop.setObjectName("aiBatchStop")
        actions.addWidget(self.start)
        actions.addWidget(self.stop)
        layout.addLayout(actions)
        self.progress = QProgressBar(self)
        self.progress.setObjectName("aiBatchProgress")
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(6)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.mode.currentIndexChanged.connect(self._update_enabled)
        self.mode.currentIndexChanged.connect(self.refresh_plan)
        self.count.valueChanged.connect(self.refresh_plan)
        self.start.clicked.connect(
            lambda: self.start_requested.emit(self.mode.currentData(), self.count.value())
        )
        self.stop.clicked.connect(self.stop_requested.emit)
        self._update_enabled()
        self.retranslate()

    def _update_enabled(self) -> None:
        self.mode.setEnabled(not self.running)
        self.count.setEnabled(not self.running and self.mode.currentData() == "count")
        self.start.setEnabled(not self.running)
        self.stop.setEnabled(self.running)

    def set_running(self, running: bool) -> None:
        self.running = running
        self._update_enabled()
        self.progress.setVisible(running)
        if not running:
            self.refresh_plan()

    def show_status(self, source: str, **values) -> None:
        self._status, self._status_values = source, values
        self.status.setText(tr(source).format(**values) if source else "")
        if source == PROGRESS:
            total = values.get("total")
            # Until stopped has no total; show a busy bar instead of a ratio.
            self.progress.setRange(0, total if isinstance(total, int) else 0)
            self.progress.setValue(values.get("completed", 0))

    def refresh_plan(self, *_args) -> None:
        """Show how many generations Start will run; frozen while running."""
        if self.running:
            return
        mode = self.mode.currentData()
        if mode == "count":
            self.plan.setText(tr("Plan: {count} generations").format(count=self.count.value()))
        elif mode == "infinite":
            self.plan.setText(tr("Plan: until stopped"))
        else:
            length = self.cycle_provider() if callable(self.cycle_provider) else None
            self.plan.setText(
                tr("Plan: one cycle of {count}").format(count=length)
                if length is not None
                else tr("A cycle requires Sequential or Combinatorial LivePrompt choices.")
            )

    def showEvent(self, event: QEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh_plan()

    def retranslate(self) -> None:
        self.title.setText(tr("Continuous generation"))
        self.start.setText(tr("Start continuous generation"))
        self.stop.setText(tr("Stop"))
        self.count.setToolTip(tr("Number of generations"))
        for index, source in enumerate(MODES.values()):
            self.mode.setItemText(index, tr(source))
        self.mode.setToolTip(
            tr("A cycle uses Sequential and Combinatorial choices; random choices are excluded.")
        )
        self.show_status(self._status, **self._status_values)
        self.refresh_plan()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


def mount(section, layout) -> None:
    controls = section._batch_controls = BatchGenerationControls(section._ai_effect_group)
    controls.cycle_provider = lambda: _cycle_length(section)
    controls.start_requested.connect(section.batch_generate_requested.emit)
    controls.stop_requested.connect(section.batch_stop_requested.emit)
    layout.addWidget(controls)


def _cycle_length(section) -> int | None:
    from loomstroke.app_ai_effect_batch import cycle_length  # noqa: PLC0415

    _layer_id, state = section._get_selected_ai_effect()
    if state is None:
        return None
    # The plan refreshes on every layer sync; only re-count when prompts change.
    key = repr(state.param_ui_state)
    cached = getattr(section, "_batch_plan_cache", None)
    if cached is None or cached[0] != key:
        cached = section._batch_plan_cache = (key, cycle_length(section, state))
    return cached[1]


def clear(section) -> None:
    controls = section._batch_controls
    if controls is not None and controls.running:
        section.batch_stop_requested.emit()
    section._batch_controls = None
