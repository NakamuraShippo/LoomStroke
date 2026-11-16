"""Choose a source layer and distribute its content on a vector contour."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QWidget,
)

from loomstroke.core.commands_path_object_placement import PlaceObjectsAlongPathCommand
from loomstroke.core.drawing_assist import StrokeConstraintSession
from loomstroke.core.path_object_placement import (
    assisted_placement_frames,
    contour_track,
    placement_frames,
)
from loomstroke.plugins.sdk.drawing_assist import get_drawing_assist_controller
from loomstroke.plugins.vector_tools.path_object_placement_service import (
    capture_source,
    place_source,
    target_contours,
)
from loomstroke.ui._dock_safe import is_valid
from loomstroke.ui.translations import translated_literal_domain

from .path_object_placement_overlay import PathObjectPlacementOverlay


def _ts(source: str) -> str:
    return translated_literal_domain("PathObjectPlacement", source, use_fallback=True)


def _layers(canvas: Any) -> list[Any]:
    result = []

    def visit(items):
        for layer in items:
            result.append(layer)
            visit(getattr(layer, "children", ()) or ())

    visit(getattr(canvas, "_layers", ()) or ())
    return result


class PathObjectPlacementDialog(QDialog):
    def __init__(self, host: Any, target_layer_id: str, selected_object_id: str | None, parent):
        super().__init__(parent, Qt.WindowType.Tool)
        self.host = host
        self.target_layer_id = target_layer_id
        self._canvas = getattr(parent, "canvas", None)
        self._source = None
        self._contours = []
        self._track_key = None
        self._track_value = None
        self._overlay = None
        self._labels = {}
        self._assist_session = self._capture_assist(host)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        form = QFormLayout(self)

        self.source_combo = QComboBox(self)
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        self._row(form, "Source layer", self.source_combo)

        self.contour_combo = QComboBox(self)
        self.contour_combo.currentIndexChanged.connect(self._refresh)
        self._row(form, "Contour", self.contour_combo)

        self.start_spin = self._percent_spin(form, "Start (%)")
        self.end_spin = self._percent_spin(form, "End (%)")
        self.end_spin.setValue(100)
        self.swap_button = QPushButton(self)
        self.swap_button.clicked.connect(self._swap)
        form.addRow(self.swap_button)

        self.offset_spin = QDoubleSpinBox(self)
        self.offset_spin.setRange(-10000, 10000)
        self.offset_spin.setSuffix(" px")
        self.offset_spin.valueChanged.connect(self._refresh)
        self._row(form, "Distance from path (px)", self.offset_spin)

        self.reverse_check = QCheckBox(self)
        self.reverse_check.toggled.connect(self._refresh)
        form.addRow(self.reverse_check)
        self.rotate_check = QCheckBox(self)
        self.rotate_check.setChecked(True)
        self.rotate_check.toggled.connect(self._refresh)
        form.addRow(self.rotate_check)

        self.count_slider, self.count_value = self._slider(form, "Object count", 1, 128, 5)
        self.gap_slider, self.gap_value = self._slider(form, "Object spacing (%)", -90, 300, 0)

        self.status = QLabel(self)
        self.status.setWordWrap(True)
        form.addRow(self.status)
        self._add_actions(form)

        self._populate_sources()
        self._populate_contours(selected_object_id)
        self.retranslate()
        if self._canvas is not None:
            self._overlay = PathObjectPlacementOverlay(self, self._canvas)
        self._refresh()

    @staticmethod
    def _capture_assist(host):
        controller = get_drawing_assist_controller(host)
        assist = controller.active_assist() if controller is not None else None
        if assist is not None and controller.issues(assist):
            assist = None
        return StrokeConstraintSession.compile(assist)

    def _add_actions(self, form):
        buttons = QWidget(self)
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        self.apply_button = QPushButton(self)
        self.apply_button.clicked.connect(self._apply)
        self.cancel_button = QPushButton(self)
        self.cancel_button.clicked.connect(self.reject)
        row.addWidget(self.apply_button)
        row.addWidget(self.cancel_button)
        form.addRow(buttons)

    def _row(self, form, source, widget):
        label = QLabel(self)
        label.setProperty("source", source)
        self._labels[source] = label
        form.addRow(label, widget)

    def _percent_spin(self, form, source):
        spin = QDoubleSpinBox(self)
        spin.setRange(0, 100)
        spin.setDecimals(2)
        spin.setSuffix(" %")
        spin.valueChanged.connect(self._refresh)
        self._row(form, source, spin)
        return spin

    def _slider(self, form, source, minimum, maximum, value):
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Orientation.Horizontal, row)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        label = QLabel(str(value), row)
        label.setMinimumWidth(36)
        slider.valueChanged.connect(label.setNum)
        slider.valueChanged.connect(self._refresh)
        layout.addWidget(slider)
        layout.addWidget(label)
        self._row(form, source, row)
        return slider, label

    def _populate_sources(self):
        self.source_combo.blockSignals(True)
        for layer in reversed(_layers(self.host.canvas)):
            if layer.layer_type in {"raster", "vector"}:
                self.source_combo.addItem(layer.name, layer.id)
        index = next(
            (
                i
                for i in range(self.source_combo.count())
                if self.source_combo.itemData(i) != self.target_layer_id
            ),
            0,
        )
        self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)
        self._source_changed()

    def _populate_contours(self, selected_object_id):
        target = self._layer(self.target_layer_id)
        self._contours = target_contours(target)
        self.contour_combo.blockSignals(True)
        for obj_id, index, _rect, _path in self._contours:
            self.contour_combo.addItem(f"{obj_id} / {index + 1}", (obj_id, index))
        selected = next(
            (i for i, entry in enumerate(self._contours) if entry[0] == selected_object_id), 0
        )
        self.contour_combo.setCurrentIndex(selected)
        self.contour_combo.blockSignals(False)

    def _layer(self, layer_id):
        return next((layer for layer in _layers(self.host.canvas) if layer.id == layer_id), None)

    def _source_changed(self):
        self._source = capture_source(self._layer(self.source_combo.currentData()))
        self._refresh()

    def _swap(self):
        start, end = self.start_spin.value(), self.end_spin.value()
        self.start_spin.setValue(end)
        self.end_spin.setValue(start)

    def set_endpoint(self, name: str, fraction: float):
        (self.start_spin if name == "start" else self.end_spin).setValue(fraction * 100)

    def track(self):
        index = self.contour_combo.currentIndex()
        if not 0 <= index < len(self._contours):
            return None
        key = (
            id(self._contours),
            index,
            self.start_spin.value(),
            self.end_spin.value(),
            self.offset_spin.value(),
            self.reverse_check.isChecked(),
            self.rotate_check.isChecked(),
        )
        if key == self._track_key:
            return self._track_value
        _, _, rect, path = self._contours[index]
        self._track_value = contour_track(
            rect,
            path,
            start=key[2] / 100,
            end=key[3] / 100,
            offset=key[4],
            reverse=key[5],
            rotate=key[6],
        )
        self._track_key = key
        return self._track_value

    def frames(self):
        track = self.track()
        source = self._source
        if track is None or source is None:
            return []
        gap = source.width * self.gap_slider.value() / 100
        try:
            frames = placement_frames(track, self.count_slider.value(), source.width, gap)
            return assisted_placement_frames(frames, self._assist_session)
        except ValueError:
            return []

    def source_opacity(self):
        layer = self._layer(self.source_combo.currentData())
        return layer.opacity if layer is not None else 1.0

    def _refresh(self):
        if not hasattr(self, "status"):
            return
        frames = self.frames()
        self.apply_button.setEnabled(bool(frames))
        if not self._contours:
            self.status.setText(_ts("No usable vector contour on this layer."))
        elif self._source is None:
            self.status.setText(_ts("Choose a non-empty raster or vector layer."))
        elif not frames:
            self.status.setText(
                _ts("Objects exceed the selected interval. Reduce count or spacing.")
            )
        else:
            self.status.setText(_ts("Drag the start and end handles along the contour."))
        if self._overlay is not None:
            self._overlay.refresh()

    def _apply(self):
        source_layer = self._layer(self.source_combo.currentData())
        target_layer = self._layer(self.target_layer_id)
        source = capture_source(source_layer)
        if source is None or target_layer is None:
            self._refresh()
            return
        key = self.contour_combo.currentData()
        self._contours = target_contours(target_layer)
        index = next((i for i, entry in enumerate(self._contours) if entry[:2] == key), -1)
        self.contour_combo.setCurrentIndex(index)
        self._source = source
        frames = self.frames()
        if not frames:
            self._refresh()
            return
        payload = place_source(source, frames, self.host.canvas.get_canvas_size())
        name = _ts("Objects along path: {name}").format(name=source_layer.name)
        command = (
            PlaceObjectsAlongPathCommand(name, image=payload, opacity=source_layer.opacity)
            if isinstance(payload, QImage)
            else PlaceObjectsAlongPathCommand(
                name,
                vector_objects=payload,
                vector_kind=source_layer.vector_kind,
                opacity=source_layer.opacity,
            )
        )
        if not self.host.canvas.submit(command):
            QMessageBox.warning(
                self, _ts("Objects Along Path"), _ts("Could not create the placement layer.")
            )
            return
        self.accept()

    def retranslate(self):
        self.setWindowTitle(_ts("Objects Along Path"))
        for source, label in self._labels.items():
            label.setText(_ts(source))
        self.swap_button.setText(_ts("Swap start and end"))
        self.reverse_check.setText(_ts("Reverse direction"))
        self.rotate_check.setText(_ts("Rotate objects along path"))
        self.apply_button.setText(_ts("Place objects"))
        self.cancel_button.setText(_ts("Cancel"))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "_labels"):
            self.retranslate()
            self._refresh()

    def eventFilter(self, watched, event):
        if self._overlay is not None and watched in self._overlay.event_surfaces:
            return self._overlay.event_filter(event, watched)
        return super().eventFilter(watched, event)

    def done(self, result):
        if self._overlay is not None:
            self._overlay.close()
            self._overlay = None
        super().done(result)


def open_path_object_placement(host, *, target_layer_id=None):
    """Open the shared panel from a vector tool or the layer context menu."""
    window = host.ui.get_main_window()
    canvas = host.canvas
    if target_layer_id is None:
        selection = getattr(canvas, "_vector_selection", None)
        target_layer_id = selection.get("layer_id") if isinstance(selection, dict) else None
        if target_layer_id is None and canvas.active_layer is not None:
            target_layer_id = canvas.active_layer.id
    target = next((layer for layer in _layers(canvas) if layer.id == target_layer_id), None)
    if target is None or target.layer_type != "vector" or not target_contours(target):
        QMessageBox.information(
            window, _ts("Objects Along Path"), _ts("Select a vector path first.")
        )
        return None
    old = getattr(window, "_path_object_placement_dialog", None)
    if is_valid(old):
        old.close()
    selection = getattr(canvas, "_vector_selection", None)
    selected_id = selection.get("id") if isinstance(selection, dict) else None
    dialog = PathObjectPlacementDialog(host, target_layer_id, selected_id, window)
    window._path_object_placement_dialog = dialog

    def clear_dialog(*_args):
        if getattr(window, "_path_object_placement_dialog", None) is dialog:
            window._path_object_placement_dialog = None

    dialog.finished.connect(clear_dialog)
    dialog.destroyed.connect(clear_dialog)
    dialog.show()
    return dialog
