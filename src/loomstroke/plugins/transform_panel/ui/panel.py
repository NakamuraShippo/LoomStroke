"""Numeric transform panel for X/Y/W/H/Angle of selected objects."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from loomstroke.host_api import HostAPI
from loomstroke.plugins.shape_manager.ops.bbox import compute_object_bbox
from loomstroke.plugins.shape_manager.ops.selection_ops import (
    rotate_object,
    scale_object,
    translate_object,
)
from loomstroke.plugins.vector_tools.common import VectorObject, VectorReplaceCommand

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("TransformPanel", text, use_fallback=True)


class TransformPanel(QWidget):
    """Panel providing numeric X/Y/W/H/Angle inputs for selected objects."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host: HostAPI | None = None
        self._event_tokens: list[tuple[str, Any]] = []
        self._canvas_changed_handler: Any | None = None
        self._updating = False  # guard against recursive updates
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(50)
        self._refresh_timer.timeout.connect(self._refresh)

        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._group = group = QGroupBox(_ts("Transform"))
        form = QFormLayout(group)
        form.setContentsMargins(4, 8, 4, 4)
        form.setSpacing(4)

        # Position row
        pos_row = QHBoxLayout()
        self._spin_x = self._make_spin(-99999.0, 99999.0, "X")
        self._spin_y = self._make_spin(-99999.0, 99999.0, "Y")
        pos_row.addWidget(QLabel("X"))
        pos_row.addWidget(self._spin_x)
        pos_row.addWidget(QLabel("Y"))
        pos_row.addWidget(self._spin_y)
        form.addRow(pos_row)

        # Size row
        size_row = QHBoxLayout()
        self._spin_w = self._make_spin(0.1, 99999.0, "W")
        self._spin_h = self._make_spin(0.1, 99999.0, "H")
        size_row.addWidget(QLabel("W"))
        size_row.addWidget(self._spin_w)
        size_row.addWidget(QLabel("H"))
        size_row.addWidget(self._spin_h)
        form.addRow(size_row)

        # Angle row
        angle_row = QHBoxLayout()
        self._spin_angle = self._make_spin(-360.0, 360.0, "Angle")
        self._spin_angle.setSuffix("\u00b0")
        self._angle_label = QLabel(_ts("Angle"))
        angle_row.addWidget(self._angle_label)
        angle_row.addWidget(self._spin_angle)
        angle_row.addStretch()
        form.addRow(angle_row)

        layout.addWidget(group)

        # Status label
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self._status_label)
        layout.addStretch()

        # Connect editing signals
        self._spin_x.editingFinished.connect(self._on_position_changed)
        self._spin_y.editingFinished.connect(self._on_position_changed)
        self._spin_w.editingFinished.connect(self._on_size_changed)
        self._spin_h.editingFinished.connect(self._on_size_changed)
        self._spin_angle.editingFinished.connect(self._on_angle_changed)

    def _make_spin(self, min_val: float, max_val: float, name: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(min_val, max_val)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setEnabled(False)
        spin.setAccessibleName(name)
        spin.setFixedWidth(80)
        spin.setKeyboardTracking(False)
        return spin

    # ------------------------------------------------------------------
    # Host lifecycle
    # ------------------------------------------------------------------

    def set_host(self, host: HostAPI) -> None:
        if self._host is not None:
            self.detach_host()
        self._host = host
        bus = host.events
        handler = self._on_canvas_changed
        self._canvas_changed_handler = handler
        self._event_tokens = [
            ("layer.changed", bus.on("layer.changed", handler)),
            ("layer.selected", bus.on("layer.selected", handler)),
            ("tool.changed", bus.on("tool.changed", handler)),
        ]
        # composited_changed fires after every visual update including
        # vector selection changes which have no dedicated event.
        canvas = host.canvas
        if hasattr(canvas, "composited_changed"):
            canvas.composited_changed.connect(handler)
        self._refresh()

    def detach_host(self) -> None:
        if self._host is not None:
            bus = self._host.events
            for event_name, token in self._event_tokens:
                bus.off(event_name, token)
            self._event_tokens.clear()
            canvas = self._host.canvas
            if hasattr(canvas, "composited_changed"):
                try:  # noqa: SIM105
                    handler = self._canvas_changed_handler or self._on_canvas_changed
                    canvas.composited_changed.disconnect(handler)
                except (RuntimeError, TypeError):
                    logger.debug("composited_changed disconnect failed", exc_info=True)
        self._event_tokens.clear()
        self._canvas_changed_handler = None
        self._host = None

    # ------------------------------------------------------------------
    # Selection reading
    # ------------------------------------------------------------------

    def _on_canvas_changed(self, _payload: Any = None) -> None:
        # If a spinbox is being edited, defer to avoid overwriting user input.
        if self._any_spin_has_focus():
            self._refresh_timer.start()
            return
        self._refresh()

    def _any_spin_has_focus(self) -> bool:
        return any(
            s.hasFocus()
            for s in (self._spin_x, self._spin_y, self._spin_w, self._spin_h, self._spin_angle)
        )

    def _refresh(self) -> None:
        """Read current selection and populate spinboxes."""
        if self._updating:
            return
        self._updating = True
        try:
            self._do_refresh()
        finally:
            self._updating = False

    def _do_refresh(self) -> None:
        info = self._get_selected_info()
        if info is None:
            self._set_all_enabled(False)
            self._status_label.setText(_ts("No selection"))
            return

        kind, bbox, angle = info["kind"], info["bbox"], info["angle"]
        x, y, x2, y2 = bbox
        w, h = x2 - x, y2 - y

        is_vector = kind in ("vector", "text", "vector_multi")

        self._spin_x.setEnabled(True)
        self._spin_y.setEnabled(True)
        self._spin_w.setEnabled(is_vector)
        self._spin_h.setEnabled(is_vector)

        self._spin_x.setValue(x)
        self._spin_y.setValue(y)
        self._spin_w.setValue(w)
        self._spin_h.setValue(h)
        self._spin_angle.setValue(angle)

        if kind == "layer":
            self._status_label.setText(_ts("Raster Layer"))
        elif kind == "text":
            self._status_label.setText(_ts("Text Object"))
        elif kind == "vector_multi":
            self._status_label.setText(_ts("Vector Objects"))
        else:
            self._status_label.setText(_ts("Vector Object"))

    def _get_selected_info(self) -> dict[str, Any] | None:
        """Return selection info dict or None."""
        if not self._host:
            return None
        canvas = self._host.canvas

        # Check vector selection first
        vs = getattr(canvas, "_vector_selection", None)
        if isinstance(vs, dict):
            selected_objects = self._selected_vector_objects(vs)
            if len(selected_objects) > 1:
                bbox = self._objects_bbox([obj for _vec_id, _layer_id, obj in selected_objects])
                if bbox is not None:
                    return {
                        "kind": "vector_multi",
                        "bbox": bbox,
                        "angle": 0.0,
                        "items": selected_objects,
                    }
            if len(selected_objects) == 1:
                vec_id, layer_id, obj = selected_objects[0]
                bbox = self._object_bbox(obj)
                if bbox is not None:
                    angle = self._extract_angle(obj)
                    kind = "text" if obj.meta.get("text_tool") else "vector"
                    return {
                        "kind": kind,
                        "bbox": bbox,
                        "angle": angle,
                        "vec_id": vec_id,
                        "layer_id": layer_id,
                        "obj": obj,
                    }

        if isinstance(vs, dict) and vs.get("id"):
            vec_id = vs["id"]
            layer_id = vs.get("layer_id")
            obj = self._find_vector_object(vec_id, layer_id)
            if obj is not None:
                bbox = self._object_bbox(obj)
                if bbox is not None:
                    angle = self._extract_angle(obj)
                    kind = "text" if obj.meta.get("text_tool") else "vector"
                    return {
                        "kind": kind,
                        "bbox": bbox,
                        "angle": angle,
                        "vec_id": vec_id,
                        "layer_id": layer_id,
                        "obj": obj,
                    }

        # Fallback: raster layer
        layer = getattr(canvas, "_get_active_layer", lambda: None)()
        if layer is not None and getattr(layer, "layer_type", None) == "raster":
            ox = int(getattr(layer, "offset_x", 0))
            oy = int(getattr(layer, "offset_y", 0))
            img = getattr(layer, "image", None)
            lw = int(img.width()) if img is not None else 0
            lh = int(img.height()) if img is not None else 0
            if lw > 0 and lh > 0:
                return {
                    "kind": "layer",
                    "bbox": (float(ox), float(oy), float(ox + lw), float(oy + lh)),
                    "angle": 0.0,
                    "layer": layer,
                }

        return None

    def _find_vector_object(self, vec_id: str, layer_id: str | None) -> VectorObject | None:
        if not self._host:
            return None
        canvas = self._host.canvas
        getter = getattr(canvas, "get_vector_objects", None)
        if not callable(getter):
            return None
        for lid, obj in getter():
            if obj.id == vec_id and (layer_id is None or lid == layer_id):
                return obj
        return None

    def _selected_vector_objects(
        self, selection: dict[str, Any]
    ) -> list[tuple[str, str | None, VectorObject]]:
        raw_items = selection.get("items")
        if not isinstance(raw_items, list):
            return []
        objects: list[tuple[str, str | None, VectorObject]] = []
        seen: set[tuple[str, str | None]] = set()
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            vec_id = item.get("id")
            if not isinstance(vec_id, str) or not vec_id:
                continue
            raw_layer_id = item.get("layer_id")
            layer_id = raw_layer_id if isinstance(raw_layer_id, str) else None
            key = (vec_id, layer_id)
            if key in seen:
                continue
            obj = self._find_vector_object(vec_id, layer_id)
            if obj is None:
                continue
            seen.add(key)
            objects.append((vec_id, layer_id, obj))
        return objects

    @staticmethod
    def _object_bbox(
        obj: VectorObject,
    ) -> tuple[float, float, float, float] | None:
        # Text objects: use frame metadata
        text_meta = obj.meta.get("text_tool")
        if isinstance(text_meta, dict):
            frame = text_meta.get("frame")
            if isinstance(frame, dict):
                try:
                    fx = float(frame.get("x", 0))
                    fy = float(frame.get("y", 0))
                    fw = float(frame.get("width", 0))
                    fh = float(frame.get("height", 0))
                    if fw > 0 and fh > 0:
                        return (fx, fy, fx + fw, fy + fh)
                except (TypeError, ValueError):
                    logger.debug("frame bbox parse failed", exc_info=True)
        return compute_object_bbox(obj)

    @staticmethod
    def _extract_angle(obj: VectorObject) -> float:
        text_meta = obj.meta.get("text_tool")
        if isinstance(text_meta, dict):
            transform = text_meta.get("transform")
            if isinstance(transform, dict):
                return float(transform.get("rotation_deg", 0.0))
        return 0.0

    @classmethod
    def _objects_bbox(
        cls,
        objects: list[VectorObject],
    ) -> tuple[float, float, float, float] | None:
        bounds: tuple[float, float, float, float] | None = None
        for obj in objects:
            bbox = cls._object_bbox(obj)
            if bbox is None:
                continue
            if bounds is None:
                bounds = bbox
            else:
                bounds = (
                    min(bounds[0], bbox[0]),
                    min(bounds[1], bbox[1]),
                    max(bounds[2], bbox[2]),
                    max(bounds[3], bbox[3]),
                )
        return bounds

    # ------------------------------------------------------------------
    # Apply changes
    # ------------------------------------------------------------------

    def _on_position_changed(self) -> None:
        if self._updating:
            return
        info = self._get_selected_info()
        if info is None:
            return

        new_x = self._spin_x.value()
        new_y = self._spin_y.value()
        bbox = info["bbox"]
        dx = new_x - bbox[0]
        dy = new_y - bbox[1]
        if abs(dx) < 0.01 and abs(dy) < 0.01:
            return

        kind = info["kind"]
        if kind == "layer":
            self._apply_layer_move(info, dx, dy)
        else:
            self._apply_vector_translate(info, dx, dy)

    def _on_size_changed(self) -> None:
        if self._updating:
            return
        info = self._get_selected_info()
        if info is None or info["kind"] == "layer":
            return

        bbox = info["bbox"]
        old_w = bbox[2] - bbox[0]
        old_h = bbox[3] - bbox[1]
        new_w = self._spin_w.value()
        new_h = self._spin_h.value()
        if old_w < 0.01 or old_h < 0.01:
            return
        sx = new_w / old_w
        sy = new_h / old_h
        if abs(sx - 1.0) < 0.001 and abs(sy - 1.0) < 0.001:
            return

        self._apply_vector_scale(info, sx, sy)

    def _on_angle_changed(self) -> None:
        if self._updating:
            return
        info = self._get_selected_info()
        if info is None:
            return
        angle_deg = self._spin_angle.value()
        if abs(angle_deg) < 0.01:
            return
        kind = info["kind"]
        if kind == "layer":
            self._apply_layer_rotate(info, angle_deg)
        else:
            self._apply_vector_rotate(info, angle_deg)

    def _apply_layer_rotate(self, info: dict[str, Any], angle_deg: float) -> None:
        if not self._host:
            return
        canvas = self._host.canvas
        import math  # noqa: PLC0415

        from loomstroke.core.commands import TransformCommand  # noqa: PLC0415

        layer = info.get("layer")
        layer_id = getattr(layer, "id", None) if layer else None
        cmd = TransformCommand("rotate", {"angle": math.radians(angle_deg)}, layer_id=layer_id)
        submit = getattr(canvas, "submit", None)
        if callable(submit):
            submit(cmd, merge_key=None)
        self._updating = True
        self._spin_angle.setValue(0.0)
        self._updating = False
        self._refresh()

    def _apply_vector_rotate(self, info: dict[str, Any], angle_deg: float) -> None:
        if not self._host:
            return
        import math  # noqa: PLC0415

        canvas = self._host.canvas
        selected = info.get("items")
        if isinstance(selected, list):
            bbox = info["bbox"]
            cx = (bbox[0] + bbox[2]) / 2.0
            cy = (bbox[1] + bbox[3]) / 2.0
            replacements = [
                (vec_id, layer_id, rotate_object(obj, (cx, cy), math.radians(angle_deg)))
                for vec_id, layer_id, obj in selected
            ]
            self._submit_vector_replacements(canvas, "Rotate Vector Selection", replacements)
            self._set_vector_selection_bounds(canvas, replacements)
            self._updating = True
            self._spin_angle.setValue(0.0)
            self._updating = False
            self._refresh()
            return

        obj: VectorObject = info["obj"]
        vec_id: str = info["vec_id"]
        layer_id: str | None = info.get("layer_id")
        bbox = info["bbox"]
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        rotated = rotate_object(obj, (cx, cy), math.radians(angle_deg))
        cmd = VectorReplaceCommand(vec_id, rotated, layer_id=layer_id)
        submit = getattr(canvas, "submit", None)
        if callable(submit):
            submit(cmd, merge_key=None)
        self._updating = True
        self._spin_angle.setValue(0.0)
        self._updating = False
        self._refresh()

    def _apply_layer_move(self, info: dict[str, Any], dx: float, dy: float) -> None:
        if not self._host:
            return
        canvas = self._host.canvas
        from loomstroke.core.commands import MoveLayerCommand  # noqa: PLC0415

        layer = info.get("layer")
        layer_id = getattr(layer, "id", None) if layer else None
        cmd = MoveLayerCommand(round(dx), round(dy), layer_id=layer_id)
        submit = getattr(canvas, "submit", None)
        if callable(submit):
            submit(cmd, merge_key=None)
        self._refresh()

    def _apply_vector_translate(self, info: dict[str, Any], dx: float, dy: float) -> None:
        if not self._host:
            return
        canvas = self._host.canvas
        selected = info.get("items")
        if isinstance(selected, list):
            replacements = [
                (vec_id, layer_id, translate_object(obj, dx, dy))
                for vec_id, layer_id, obj in selected
            ]
            self._submit_vector_replacements(canvas, "Move Vector Selection", replacements)
            self._set_vector_selection_bounds(canvas, replacements)
            self._refresh()
            return

        obj: VectorObject = info["obj"]
        vec_id: str = info["vec_id"]
        layer_id: str | None = info.get("layer_id")
        moved = translate_object(obj, dx, dy)
        cmd = VectorReplaceCommand(vec_id, moved, layer_id=layer_id)
        submit = getattr(canvas, "submit", None)
        if callable(submit):
            submit(cmd, merge_key=None)
        self._refresh()

    def _apply_vector_scale(self, info: dict[str, Any], sx: float, sy: float) -> None:
        if not self._host:
            return
        canvas = self._host.canvas
        selected = info.get("items")
        bbox = info["bbox"]
        pivot = (bbox[0], bbox[1])  # top-left pivot
        if isinstance(selected, list):
            replacements = [
                (
                    vec_id,
                    layer_id,
                    scale_object(obj, pivot, sx, sy, keep_stroke_width=True),
                )
                for vec_id, layer_id, obj in selected
            ]
            self._submit_vector_replacements(canvas, "Scale Vector Selection", replacements)
            self._set_vector_selection_bounds(canvas, replacements)
            self._refresh()
            return

        obj: VectorObject = info["obj"]
        vec_id: str = info["vec_id"]
        layer_id: str | None = info.get("layer_id")
        scaled = scale_object(obj, pivot, sx, sy, keep_stroke_width=True)
        cmd = VectorReplaceCommand(vec_id, scaled, layer_id=layer_id)
        submit = getattr(canvas, "submit", None)
        if callable(submit):
            submit(cmd, merge_key=None)
        self._refresh()

    def _submit_vector_replacements(
        self,
        canvas: object,
        label: str,
        replacements: list[tuple[str, str | None, VectorObject]],
    ) -> None:
        submit = getattr(canvas, "submit", None)
        if not callable(submit):
            return
        begin_tx = getattr(canvas, "begin_transaction", None)
        end_tx = getattr(canvas, "end_transaction", None)
        transaction_open = False
        if len(replacements) > 1 and callable(begin_tx) and callable(end_tx):
            begin_tx(label)
            transaction_open = True
        try:
            for vec_id, layer_id, obj in replacements:
                cmd = VectorReplaceCommand(vec_id, obj, layer_id=layer_id)
                submit(cmd, merge_key=None)
        finally:
            if transaction_open:
                end_tx()

    def _set_vector_selection_bounds(
        self,
        canvas: object,
        replacements: list[tuple[str, str | None, VectorObject]],
    ) -> None:
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, dict) or not selection.get("items"):
            return
        bounds = self._objects_bbox([obj for _vec_id, _layer_id, obj in replacements])
        if bounds is not None:
            selection["transform_bounds"] = bounds

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # i18n
    # ------------------------------------------------------------------

    def changeEvent(self, event: QEvent) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        if self._group is not None:
            self._group.setTitle(_ts("Transform"))
        self._angle_label.setText(_ts("Angle"))
        # Refresh status label text
        self._do_refresh()

    def _set_all_enabled(self, enabled: bool) -> None:
        for spin in (
            self._spin_x,
            self._spin_y,
            self._spin_w,
            self._spin_h,
        ):
            spin.setEnabled(enabled)
            if not enabled:
                spin.setValue(0.0)
        self._spin_angle.setValue(0.0)
