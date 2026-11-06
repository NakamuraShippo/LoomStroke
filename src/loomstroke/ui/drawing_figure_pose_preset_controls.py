"""Controls for named figure poses on the selected 3D layer."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QSignalBlocker
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from loomstroke.core.drawing_figures.model import pose_from_layer
from loomstroke.core.drawing_figures.user_pose_presets import FigurePosePresetStore

from .drawing_figure_add import figure_text as tr
from .translations import TranslationBinder


class DrawingFigurePosePresetControls(QWidget):
    def __init__(self, toolbar: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.toolbar = toolbar
        self.store = FigurePosePresetStore()
        self._model: str | None = None
        self._binder = TranslationBinder(tr)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel(self)
        self._binder.register(label.setText, "Saved poses")
        row.addWidget(label)
        self.presets = QComboBox(self)
        self.presets.setMinimumContentsLength(12)
        self.presets.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.presets.setMaximumWidth(240)
        self.presets.addItem(tr("Select saved pose"), None)
        self.presets.currentIndexChanged.connect(self._update_actions)
        row.addWidget(self.presets)
        self.save_button = self._button(row, "Save current pose", self._save_clicked)
        self.apply_button = self._button(row, "Apply saved pose", self.apply_selected)
        self.delete_button = self._button(row, "Delete saved pose", self.delete_selected)
        self.status = QLabel(self)
        self.status.hide()
        row.addWidget(self.status)
        self._update_actions()

    def _button(self, row: QHBoxLayout, source: str, callback: Any) -> QPushButton:
        button = QPushButton(self)
        self._binder.register(button.setText, source)
        button.clicked.connect(callback)
        row.addWidget(button)
        return button

    def _selected_layer(self) -> Any:
        return self.toolbar._layer()

    def refresh(self, *, force: bool = False) -> None:
        layer = self._selected_layer()
        pose = pose_from_layer(layer)
        model = pose.model if pose is not None else None
        if not force and model == self._model:
            self._update_actions()
            return
        selected = self.presets.currentData()
        self._model = model
        with QSignalBlocker(self.presets):
            self.presets.clear()
            self.presets.addItem(tr("Select saved pose"), None)
            try:
                if model is not None:
                    for item in self.store.list(model):
                        self.presets.addItem(item.name, item.id)
                self.status.hide()
                self.status.setToolTip("")
            except (OSError, ValueError, RuntimeError) as error:
                self.status.setText(tr("Could not load pose presets"))
                self.status.setToolTip(str(error))
                self.status.show()
            index = self.presets.findData(selected) if selected is not None else 0
            self.presets.setCurrentIndex(max(index, 0))
        self._update_actions()

    def _update_actions(self, *_args: Any) -> None:
        layer = self._selected_layer()
        available = pose_from_layer(layer) is not None and not bool(getattr(layer, "locked", False))
        self.save_button.setEnabled(available)
        chosen = available and self.presets.currentData() is not None
        self.apply_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen)
        self.presets.setToolTip(self.presets.currentText() if chosen else "")

    def _warn(self, source: str, error: Exception) -> None:
        duplicate = "A pose preset with this name already exists for this model"
        details = {
            duplicate: "Choose a different name for this model.",
            "Invalid pose preset name": "Enter a name between 1 and 80 characters.",
        }
        detail = details.get(str(error))
        message = tr(source) + ("\n" + tr(detail) if detail else "")
        QMessageBox.warning(self, tr("Pose presets"), message)

    def _save_clicked(self) -> None:
        name, accepted = QInputDialog.getText(self, tr("Save pose preset"), tr("Pose preset name"))
        if accepted:
            self.save_named(name)

    def save_named(self, name: str) -> bool:
        interaction = self.toolbar._figure_interaction()
        if interaction is not None:
            interaction.cancel()
        layer = self._selected_layer()
        pose = pose_from_layer(layer)
        if pose is None or getattr(layer, "locked", False):
            return False
        try:
            saved = self.store.save(name, pose)
        except (OSError, ValueError, RuntimeError) as error:
            self._warn("Could not save pose preset", error)
            return False
        self.refresh(force=True)
        self.presets.setCurrentIndex(self.presets.findData(saved.id))
        return True

    def apply_selected(self) -> bool:
        layer = self._selected_layer()
        pose = pose_from_layer(layer)
        preset_id = self.presets.currentData()
        if pose is None or getattr(layer, "locked", False) or not isinstance(preset_id, str):
            return False
        try:
            saved = self.store.get(preset_id)
        except (OSError, ValueError, RuntimeError) as error:
            self._warn("Could not apply pose preset", error)
            return False
        if saved is None or saved.pose.model != pose.model:
            self.refresh(force=True)
            return False
        return self.toolbar._submit(saved.pose)

    def delete_selected(self) -> bool:
        layer = self._selected_layer()
        pose = pose_from_layer(layer)
        preset_id = self.presets.currentData()
        if pose is None or getattr(layer, "locked", False) or not isinstance(preset_id, str):
            return False
        try:
            saved = self.store.get(preset_id)
            if saved is None or saved.pose.model != pose.model:
                self.refresh(force=True)
                return False
            answer = QMessageBox.question(
                self, tr("Pose presets"), tr("Delete selected pose preset?") + "\n" + saved.name
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
            deleted = self.store.delete(preset_id)
        except (OSError, ValueError, RuntimeError) as error:
            self._warn("Could not delete pose preset", error)
            return False
        self.refresh(force=True)
        return deleted

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._binder.retranslate()
            self.presets.setItemText(0, tr("Select saved pose"))
            if not self.status.isHidden():
                self.status.setText(tr("Could not load pose presets"))
