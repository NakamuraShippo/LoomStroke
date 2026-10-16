"""Candidate list widget for SAM3 AI Select view models."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...ui.translations import translated_literal_domain

_TRANSLATION_DOMAIN = "SAM3AISelect"
_CANDIDATE_ID_ROLE = int(Qt.ItemDataRole.UserRole)
_SELECT_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 1
_SELECT_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 2
_REJECT_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 3
_REJECT_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 4
_LAYER_MASK_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 5
_LAYER_MASK_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 6
_CLIPPING_MASK_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 7
_CLIPPING_MASK_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 8
_CUT_TO_LAYER_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 9
_CUT_TO_LAYER_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 10
_EXTRACT_FOREGROUND_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 11
_EXTRACT_FOREGROUND_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 12
_EXTRACT_BACKGROUND_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 13
_EXTRACT_BACKGROUND_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 14
_SAVE_MASK_ASSET_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 15
_SAVE_MASK_ASSET_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 16
_REFINE_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 17
_REFINE_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 18
_INPAINT_MASK_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 19
_INPAINT_MASK_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 20
_PREVIEW_INPAINT_ACTION_ID_ROLE = _CANDIDATE_ID_ROLE + 21
_PREVIEW_INPAINT_ENABLED_ROLE = _CANDIDATE_ID_ROLE + 22
_REJECTED_FOREGROUND = QColor("#7a7f87")


class Sam3AISelectCandidateListWidget(QWidget):
    """Render SAM3 candidate rows and emit row action ids."""

    action_requested = Signal(str)
    selection_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_candidate_id: str | None = None
        self._title_label = QLabel(_ts("Candidate List"), self)
        self._summary_label = QLabel(_ts("No candidates"), self)
        self._empty_label = QLabel(_ts("No candidates"), self)
        self._tree = QTreeWidget(self)
        self._setup_ui()

    def set_view_model(self, view_model: Mapping[str, Any] | None) -> None:
        model = view_model if isinstance(view_model, Mapping) else {}
        rows = _row_mappings(model.get("rows"))
        previous_selected_ids = self.selected_candidate_ids()
        previous_current_id = self._current_candidate_id
        self._current_candidate_id = _optional_text(model.get("selectedCandidateId"))
        if self._current_candidate_id != previous_current_id and previous_selected_ids == [
            previous_current_id
        ]:
            previous_selected_ids = []
        self._tree.clear()
        self._empty_label.setVisible(not rows)
        self._summary_label.setText(_summary_text(len(rows), self._current_candidate_id))

        for row in rows:
            self._add_row(row)
        self._restore_selection(previous_selected_ids, self._current_candidate_id)
        self._tree.resizeColumnToContents(0)
        self._tree.resizeColumnToContents(1)
        self._tree.resizeColumnToContents(2)
        self._tree.resizeColumnToContents(3)

    def current_candidate_id(self) -> str | None:
        current_item = self._tree.currentItem()
        if current_item is not None:
            return _optional_text(current_item.data(0, _CANDIDATE_ID_ROLE))
        return self._current_candidate_id

    def row_count(self) -> int:
        return self._tree.topLevelItemCount()

    def selected_candidate_ids(self) -> list[str]:
        selected_ids: list[str] = []
        seen: set[str] = set()
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item is None or not item.isSelected():
                continue
            candidate_id = _optional_text(item.data(0, _CANDIDATE_ID_ROLE))
            if candidate_id is None or candidate_id in seen:
                continue
            selected_ids.append(candidate_id)
            seen.add(candidate_id)
        return selected_ids

    def _setup_ui(self) -> None:
        self.setObjectName("Sam3AISelectCandidateListWidget")
        self._summary_label.setObjectName("sam3CandidateListSummary")
        self._empty_label.setObjectName("sam3CandidateListEmpty")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tree.setObjectName("sam3CandidateListTree")
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setHeaderLabels(
            [_ts("Candidate"), _ts("State"), _ts("Score"), _ts("Box"), _ts("Actions")]
        )
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.itemSelectionChanged.connect(self._emit_selection_changed)
        self._tree.itemDoubleClicked.connect(self._on_item_activated)
        self._tree.itemActivated.connect(self._on_item_activated)
        # A plain single click must move the actual tool selection too —
        # otherwise the row highlight and the refine/apply target silently
        # diverge (2026-07-13: Refine ran on #1 while row #6 looked selected).
        self._tree.itemClicked.connect(self._on_item_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._title_label)
        layout.addWidget(self._summary_label)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._tree)

    def _add_row(self, row: Mapping[str, Any]) -> None:
        candidate_id = _optional_text(row.get("candidateId"))
        if candidate_id is None:
            return
        select_action_id = _optional_text(row.get("selectActionId"))
        reject_action_id = _optional_text(row.get("rejectActionId"))
        refine_action_id = _optional_text(row.get("refineActionId"))
        layer_mask_action_id = _optional_text(row.get("layerMaskActionId"))
        inpaint_mask_action_id = _optional_text(row.get("inpaintMaskActionId"))
        preview_inpaint_action_id = _optional_text(row.get("previewInpaintActionId"))
        clipping_mask_action_id = _optional_text(row.get("clippingMaskActionId"))
        cut_to_layer_action_id = _optional_text(row.get("cutToLayerActionId"))
        extract_foreground_action_id = _optional_text(row.get("extractForegroundActionId"))
        extract_background_action_id = _optional_text(row.get("extractBackgroundActionId"))
        save_mask_asset_action_id = _optional_text(row.get("saveMaskAssetActionId"))
        item = QTreeWidgetItem(
            [
                _candidate_label(row, candidate_id),
                _optional_text(row.get("state")) or "",
                _score_text(row.get("score")),
                _box_text(row.get("bbox")),
                "",
            ]
        )
        tooltip = _row_tooltip(row, candidate_id)
        for column in range(5):
            item.setToolTip(column, tooltip)
        item.setData(0, _CANDIDATE_ID_ROLE, candidate_id)
        item.setData(0, _SELECT_ACTION_ID_ROLE, select_action_id)
        item.setData(0, _SELECT_ENABLED_ROLE, bool(row.get("selectEnabled", False)))
        item.setData(0, _REJECT_ACTION_ID_ROLE, reject_action_id)
        item.setData(0, _REJECT_ENABLED_ROLE, bool(row.get("rejectEnabled", False)))
        item.setData(0, _REFINE_ACTION_ID_ROLE, refine_action_id)
        item.setData(0, _REFINE_ENABLED_ROLE, bool(row.get("refineEnabled", False)))
        item.setData(0, _LAYER_MASK_ACTION_ID_ROLE, layer_mask_action_id)
        item.setData(0, _LAYER_MASK_ENABLED_ROLE, bool(row.get("layerMaskEnabled", False)))
        item.setData(0, _INPAINT_MASK_ACTION_ID_ROLE, inpaint_mask_action_id)
        item.setData(
            0,
            _INPAINT_MASK_ENABLED_ROLE,
            bool(row.get("inpaintMaskEnabled", False)),
        )
        item.setData(0, _PREVIEW_INPAINT_ACTION_ID_ROLE, preview_inpaint_action_id)
        item.setData(
            0,
            _PREVIEW_INPAINT_ENABLED_ROLE,
            bool(row.get("previewInpaintEnabled", False)),
        )
        item.setData(0, _CLIPPING_MASK_ACTION_ID_ROLE, clipping_mask_action_id)
        item.setData(
            0,
            _CLIPPING_MASK_ENABLED_ROLE,
            bool(row.get("clippingMaskEnabled", False)),
        )
        item.setData(0, _CUT_TO_LAYER_ACTION_ID_ROLE, cut_to_layer_action_id)
        item.setData(
            0,
            _CUT_TO_LAYER_ENABLED_ROLE,
            bool(row.get("cutToLayerEnabled", False)),
        )
        item.setData(0, _EXTRACT_FOREGROUND_ACTION_ID_ROLE, extract_foreground_action_id)
        item.setData(
            0,
            _EXTRACT_FOREGROUND_ENABLED_ROLE,
            bool(row.get("extractForegroundEnabled", False)),
        )
        item.setData(0, _EXTRACT_BACKGROUND_ACTION_ID_ROLE, extract_background_action_id)
        item.setData(
            0,
            _EXTRACT_BACKGROUND_ENABLED_ROLE,
            bool(row.get("extractBackgroundEnabled", False)),
        )
        item.setData(0, _SAVE_MASK_ASSET_ACTION_ID_ROLE, save_mask_asset_action_id)
        item.setData(
            0,
            _SAVE_MASK_ASSET_ENABLED_ROLE,
            bool(row.get("saveMaskAssetEnabled", False)),
        )
        if _optional_text(row.get("state")) == "rejected":
            for column in range(5):
                item.setForeground(column, _REJECTED_FOREGROUND)
        self._tree.addTopLevelItem(item)
        if row.get("selected", False):
            item.setSelected(True)
            self._tree.setCurrentItem(item)
        self._tree.setItemWidget(item, 4, self._action_buttons(row, candidate_id))

    def _restore_selection(
        self,
        previous_candidate_ids: list[str],
        active_candidate_id: str | None,
    ) -> None:
        if self._tree.topLevelItemCount() <= 0:
            return

        items_by_id: dict[str, QTreeWidgetItem] = {}
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item is None:
                continue
            candidate_id = _optional_text(item.data(0, _CANDIDATE_ID_ROLE))
            if candidate_id is not None:
                items_by_id[candidate_id] = item

        restored_items = [
            items_by_id[candidate_id]
            for candidate_id in previous_candidate_ids
            if candidate_id in items_by_id
        ]
        active_item = items_by_id.get(active_candidate_id or "")
        selected_items = restored_items or ([active_item] if active_item is not None else [])

        self._tree.clearSelection()
        for item in selected_items:
            item.setSelected(True)

        current_item = active_item or (selected_items[0] if selected_items else None)
        if current_item is None:
            return
        selection_model = self._tree.selectionModel()
        if selection_model is None:
            self._tree.setCurrentItem(current_item)
            return
        selection_model.setCurrentIndex(
            self._tree.indexFromItem(current_item, 0),
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )

    def _action_buttons(self, row: Mapping[str, Any], candidate_id: str) -> QWidget:
        container = QWidget(self._tree)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "select",
                _ts("Select"),
                row.get("selectActionId"),
                row.get("selectEnabled"),
                row.get("selectChecked"),
                row.get("selectBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "reject",
                _ts("Reject"),
                row.get("rejectActionId"),
                row.get("rejectEnabled"),
                row.get("rejectChecked"),
                row.get("rejectBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "refine",
                _ts("Refine"),
                row.get("refineActionId"),
                row.get("refineEnabled"),
                row.get("refineChecked"),
                row.get("refineBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "layer_mask",
                _ts("Mask"),
                row.get("layerMaskActionId"),
                row.get("layerMaskEnabled"),
                row.get("layerMaskChecked"),
                row.get("layerMaskBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "inpaint_mask",
                _ts("Inpaint"),
                row.get("inpaintMaskActionId"),
                row.get("inpaintMaskEnabled"),
                row.get("inpaintMaskChecked"),
                row.get("inpaintMaskBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "protect_subject",
                _ts("Protect"),
                row.get("protectSubjectActionId"),
                row.get("protectSubjectEnabled"),
                row.get("protectSubjectChecked"),
                row.get("protectSubjectBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "preview_inpaint",
                _ts("Preview"),
                row.get("previewInpaintActionId"),
                row.get("previewInpaintEnabled"),
                row.get("previewInpaintChecked"),
                row.get("previewInpaintBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "clipping_mask",
                _ts("Clip"),
                row.get("clippingMaskActionId"),
                row.get("clippingMaskEnabled"),
                row.get("clippingMaskChecked"),
                row.get("clippingMaskBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "cut_to_layer",
                _ts("Cut"),
                row.get("cutToLayerActionId"),
                row.get("cutToLayerEnabled"),
                row.get("cutToLayerChecked"),
                row.get("cutToLayerBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "extract_foreground",
                _ts("FG"),
                row.get("extractForegroundActionId"),
                row.get("extractForegroundEnabled"),
                row.get("extractForegroundChecked"),
                row.get("extractForegroundBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "extract_background",
                _ts("BG"),
                row.get("extractBackgroundActionId"),
                row.get("extractBackgroundEnabled"),
                row.get("extractBackgroundChecked"),
                row.get("extractBackgroundBlockedReasonCodes"),
            )
        )
        layout.addWidget(
            self._make_action_button(
                candidate_id,
                "save_mask_asset",
                _ts("Save"),
                row.get("saveMaskAssetActionId"),
                row.get("saveMaskAssetEnabled"),
                row.get("saveMaskAssetChecked"),
                row.get("saveMaskAssetBlockedReasonCodes"),
            )
        )
        layout.addStretch(1)
        return container

    def _make_action_button(
        self,
        candidate_id: str,
        action_kind: str,
        label: str,
        action_id_value: object,
        enabled_value: object,
        checked_value: object,
        blocked_codes_value: object,
    ) -> QToolButton:
        action_id = _optional_text(action_id_value)
        enabled = bool(enabled_value) and action_id is not None
        button = QToolButton(self._tree)
        button.setText(label)
        button.setToolTip(_action_tooltip(label, action_kind, action_id, blocked_codes_value))
        button.setEnabled(enabled)
        button.setCheckable(action_kind == "select")
        button.setChecked(bool(checked_value))
        button.setProperty("candidateId", candidate_id)
        button.setProperty("actionKind", action_kind)
        if enabled:
            button.clicked.connect(
                lambda _checked=False, current_action_id=action_id: self._emit_action(
                    current_action_id
                )
            )
        return button

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        enabled = bool(item.data(0, _SELECT_ENABLED_ROLE))
        action_id = _optional_text(item.data(0, _SELECT_ACTION_ID_ROLE))
        if enabled and action_id is not None:
            self.action_requested.emit(action_id)

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        # Ctrl / Shift clicks build the multi-selection (Combine Selected /
        # Layer Group); only a plain click moves the tool's selected candidate.
        modifiers = QApplication.keyboardModifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            return
        self._on_item_activated(item, column)

    def _emit_action(self, action_id: str | None) -> None:
        if action_id:
            self.action_requested.emit(action_id)

    def _emit_selection_changed(self) -> None:
        self.selection_changed.emit(self.selected_candidate_ids())


def _row_mappings(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _candidate_label(row: Mapping[str, Any], candidate_id: str) -> str:
    base = (
        _optional_text(row.get("label")) or _optional_text(row.get("summaryText")) or candidate_id
    )
    rank = row.get("rank")
    if isinstance(rank, int) and rank > 0:
        # Matches the rank badge painted on the canvas overlay so users can
        # map canvas shapes to list rows (2026-07-13 selection clarity).
        return f"#{rank} {base}"
    return base


def _summary_text(row_count: int, selected_candidate_id: str | None) -> str:
    if row_count <= 0:
        return _ts("No candidates")
    count_text = _ts("{count} candidates").format(count=row_count)
    if selected_candidate_id is None:
        return count_text
    selected_text = _ts("Selected: {candidate_id}").format(candidate_id=selected_candidate_id)
    return f"{count_text} | {selected_text}"


def _row_tooltip(row: Mapping[str, Any], candidate_id: str) -> str:
    parts = [candidate_id]
    summary = _optional_text(row.get("summaryText"))
    if summary and summary != candidate_id:
        parts.append(summary)
    box = _box_text(row.get("bbox"))
    if box:
        parts.append(f"{_ts('Box')}: {box}")
    blocked = (
        _blocked_text(row.get("selectBlockedReasonCodes"))
        or _blocked_text(row.get("rejectBlockedReasonCodes"))
        or _blocked_text(row.get("refineBlockedReasonCodes"))
        or _blocked_text(row.get("layerMaskBlockedReasonCodes"))
        or _blocked_text(row.get("inpaintMaskBlockedReasonCodes"))
        or _blocked_text(row.get("previewInpaintBlockedReasonCodes"))
        or _blocked_text(row.get("clippingMaskBlockedReasonCodes"))
        or _blocked_text(row.get("cutToLayerBlockedReasonCodes"))
        or _blocked_text(row.get("extractForegroundBlockedReasonCodes"))
        or _blocked_text(row.get("extractBackgroundBlockedReasonCodes"))
        or _blocked_text(row.get("saveMaskAssetBlockedReasonCodes"))
    )
    if blocked:
        parts.append(f"{_ts('Blocked')}: {blocked}")
    return "\n".join(parts)


def _action_description(action_kind: str) -> str | None:
    """One-line explanation of what a candidate row action does.

    The abbreviated button labels (Mask / Clip / FG / BG ...) are not
    self-explanatory; every button carries this description as its tooltip.
    """

    descriptions = {
        "select": _ts("Select this candidate as the target for Apply / AI Refine"),
        "reject": _ts("Exclude this candidate (also removed from Next/Previous cycling)"),
        "refine": _ts("Re-run SAM3 on this candidate to improve the mask (AI Refine)"),
        "layer_mask": _ts("Apply this mask as the active layer's layer mask"),
        "inpaint_mask": _ts("Use this mask as the AI Effect inpaint mask"),
        "protect_subject": _ts(
            "Protect the subject: invert the mask and use it as the background generation mask"
        ),
        "preview_inpaint": _ts(
            "Generate one inpaint preview (result goes to the Gallery; the canvas is unchanged)"
        ),
        "clipping_mask": _ts("Apply this mask as a clipping mask"),
        "cut_to_layer": _ts("Cut the masked area to a new layer"),
        "extract_foreground": _ts("Copy the masked area (foreground) to a new layer"),
        "extract_background": _ts("Copy everything except the mask (background) to a new layer"),
        "save_mask_asset": _ts("Save this mask to the generation history (Gallery) for reuse"),
    }
    return descriptions.get(action_kind)


def _action_tooltip(
    label: str,
    action_kind: str,
    action_id: str | None,
    blocked_codes_value: object,
) -> str:
    description = _action_description(action_kind)
    base = f"{label} — {description}" if description else label
    blocked = _blocked_text(blocked_codes_value)
    if blocked:
        return f"{base}\n{blocked}"
    if action_id is None:
        return f"{base}\n{_ts('Unavailable')}"
    return base


def _blocked_text(value: object) -> str | None:
    codes = _string_list(value)
    if not codes:
        return None
    return ", ".join(codes)


def _score_text(value: object) -> str:
    numeric = _optional_float(value)
    if numeric is None:
        return ""
    return f"{round(max(0.0, min(1.0, numeric)) * 100)}%"


def _box_text(value: object) -> str:
    numbers = _number_list(value)
    if not numbers:
        return ""
    return ", ".join(_number_text(number) for number in numbers[:4])


def _number_text(value: int | float) -> str:
    if isinstance(value, int) or value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is not None:
            result.append(text)
    return result


def _number_list(value: object) -> list[int | float]:
    if not isinstance(value, list):
        return []
    result: list[int | float] = []
    for item in value:
        number = _optional_number(item)
        if number is not None:
            result.append(number)
    return result


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    number = _optional_number(value)
    if number is None:
        return None
    return float(number)


def _optional_number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _ts(text: str) -> str:
    return translated_literal_domain(_TRANSLATION_DOMAIN, text, use_fallback=True)
