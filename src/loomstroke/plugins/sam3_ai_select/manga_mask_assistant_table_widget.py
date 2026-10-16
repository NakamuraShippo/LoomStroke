"""Table widget for SAM3 Manga Mask Assistant table models."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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

_TRANSLATION_DOMAIN = "SAM3MangaMaskAssistant"
_ROW_ID_ROLE = int(Qt.ItemDataRole.UserRole)
_ACTION_COLUMN = 7


class Sam3MangaMaskAssistantTableWidget(QWidget):
    """Render Manga Mask Assistant table rows and emit action ids."""

    action_requested = Signal(str)
    acceptance_requested = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._table_model: dict[str, Any] = {}
        self._title_label = QLabel(self)
        self._summary_label = QLabel(self)
        self._empty_label = QLabel(self)
        self._tree = QTreeWidget(self)
        self._setup_ui()
        self.set_table_model(None)

    def set_table_model(self, table_model: Mapping[str, Any] | None) -> None:
        self._table_model = dict(table_model) if isinstance(table_model, Mapping) else {}
        self._render_table_model()

    def row_count(self) -> int:
        return self._tree.topLevelItemCount()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override name.
        if event.type() == QEvent.Type.LanguageChange:
            self._render_table_model()
        super().changeEvent(event)

    def _setup_ui(self) -> None:
        self.setObjectName("Sam3MangaMaskAssistantTableWidget")
        self._summary_label.setObjectName("sam3MangaMaskAssistantSummary")
        self._empty_label.setObjectName("sam3MangaMaskAssistantEmpty")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tree.setObjectName("sam3MangaMaskAssistantTree")
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(
            _ACTION_COLUMN,
            QHeaderView.ResizeMode.ResizeToContents,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._title_label)
        layout.addWidget(self._summary_label)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._tree)

    def _render_table_model(self) -> None:
        rows = _row_mappings(self._table_model.get("rows"))
        self._title_label.setText(_ts("Manga Mask Assistant"))
        self._summary_label.setText(_summary_text(self._table_model, len(rows)))
        self._empty_label.setText(_ts("No mask candidates"))
        self._empty_label.setVisible(not rows)
        self._tree.setHeaderLabels(
            [
                _ts("Scope"),
                _ts("Panel"),
                _ts("Category"),
                _ts("Score"),
                _ts("Area"),
                _ts("Accepted"),
                _ts("Warnings"),
                _ts("Actions"),
            ]
        )
        self._clear_item_widgets()
        self._tree.clear()
        for row in rows:
            self._add_row(row)
        for column in range(_ACTION_COLUMN):
            self._tree.resizeColumnToContents(column)

    def _clear_item_widgets(self) -> None:
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            widget = self._tree.itemWidget(item, _ACTION_COLUMN)
            if widget is None:
                continue
            self._tree.removeItemWidget(item, _ACTION_COLUMN)
            widget.setParent(None)
            widget.deleteLater()

    def _add_row(self, row: Mapping[str, Any]) -> None:
        row_id = _optional_text(row.get("rowId"))
        if row_id is None:
            return
        item = QTreeWidgetItem(
            [
                _optional_text(row.get("scope")) or "",
                _optional_text(row.get("panelId")) or "",
                _optional_text(row.get("category")) or "",
                _score_text(row.get("score")),
                _area_text(row.get("areaPx")),
                _accepted_text(row.get("accepted")),
                _warning_text(row.get("warningCodes")),
                "",
            ]
        )
        tooltip = _row_tooltip(row, row_id)
        for column in range(_ACTION_COLUMN + 1):
            item.setToolTip(column, tooltip)
        item.setData(0, _ROW_ID_ROLE, row_id)
        self._tree.addTopLevelItem(item)
        self._tree.setItemWidget(item, _ACTION_COLUMN, self._action_buttons(row, row_id))

    def _action_buttons(self, row: Mapping[str, Any], row_id: str) -> QWidget:
        container = QWidget(self._tree)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._make_acceptance_button(row, row_id))
        for action in _action_mappings(row.get("actionButtons")):
            button = self._make_action_button(row_id, action)
            layout.addWidget(button)
        layout.addStretch(1)
        return container

    def _make_acceptance_button(self, row: Mapping[str, Any], row_id: str) -> QToolButton:
        candidate_id = _optional_text(row.get("candidateId"))
        scope = _optional_text(row.get("scope"))
        panel_id = _optional_text(row.get("panelId"))
        current_accepted = bool(row.get("accepted"))
        next_accepted = not current_accepted
        label = _ts("Unaccept") if current_accepted else _ts("Accept")
        button = QToolButton(self._tree)
        button.setObjectName("sam3MangaMaskAssistantAcceptanceButton")
        button.setText(label)
        button.setToolTip(label if candidate_id is not None else _ts("Candidate unavailable"))
        button.setEnabled(candidate_id is not None)
        button.setProperty("rowId", row_id)
        button.setProperty("candidateId", candidate_id)
        button.setProperty("scope", scope)
        button.setProperty("panelId", panel_id)
        button.setProperty("accepted", next_accepted)
        if candidate_id is not None:
            request = {
                "rowId": row_id,
                "candidateId": candidate_id,
                "accepted": next_accepted,
                "scope": scope,
                "panelId": panel_id,
            }
            button.clicked.connect(
                lambda _checked=False, current_request=request: self._emit_acceptance(
                    current_request
                )
            )
        return button

    def _make_action_button(self, row_id: str, action: Mapping[str, Any]) -> QToolButton:
        action_key = _optional_text(action.get("action"))
        action_id = _optional_text(action.get("actionId"))
        enabled = bool(action.get("enabled", False)) and action_id is not None
        label = _action_label(action_key)
        button = QToolButton(self._tree)
        button.setObjectName("sam3MangaMaskAssistantActionButton")
        button.setText(label)
        button.setToolTip(_action_tooltip(label, action_id, action.get("blockedReasonCodes")))
        button.setEnabled(enabled)
        button.setProperty("rowId", row_id)
        button.setProperty("action", action_key)
        button.setProperty("actionId", action_id)
        if enabled:
            button.clicked.connect(
                lambda _checked=False, current_action_id=action_id: self._emit_action(
                    current_action_id
                )
            )
        return button

    def _emit_action(self, action_id: str | None) -> None:
        if action_id:
            self.action_requested.emit(action_id)

    def _emit_acceptance(self, request: Mapping[str, Any]) -> None:
        self.acceptance_requested.emit(dict(request))


def _row_mappings(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _action_mappings(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [action for action in value if isinstance(action, Mapping)]
    return []


def _summary_text(table_model: Mapping[str, Any], row_count: int) -> str:
    if row_count <= 0:
        return _ts("No mask candidates")
    visible = _optional_int(table_model.get("visibleRowCount"))
    total = _optional_int(table_model.get("totalRowCount"))
    if visible is None:
        visible = row_count
    if total is None:
        total = row_count
    return _ts("{visible} visible / {total} total").format(visible=visible, total=total)


def _row_tooltip(row: Mapping[str, Any], row_id: str) -> str:
    parts = [_ts("Row: {row_id}").format(row_id=row_id)]
    scope = _optional_text(row.get("scope"))
    if scope:
        parts.append(_ts("Scope: {scope}").format(scope=scope))
    panel_id = _optional_text(row.get("panelId"))
    if panel_id:
        parts.append(_ts("Panel: {panel_id}").format(panel_id=panel_id))
    candidate_id = _optional_text(row.get("candidateId"))
    if candidate_id:
        parts.append(_ts("Candidate: {candidate_id}").format(candidate_id=candidate_id))
    category = _optional_text(row.get("category"))
    if category:
        parts.append(_ts("Category: {category}").format(category=category))
    score = _score_text(row.get("score"))
    if score:
        parts.append(_ts("Score: {score}").format(score=score))
    area = _area_text(row.get("areaPx"))
    if area:
        parts.append(_ts("Area: {area}").format(area=area))
    warnings = _warning_text(row.get("warningCodes"))
    if warnings:
        parts.append(_ts("Warnings: {warnings}").format(warnings=warnings))
    bbox = _box_text(row.get("bbox"))
    if bbox:
        parts.append(_ts("Box: {box}").format(box=bbox))
    panel_bbox = _box_text(row.get("panelLocalBbox"))
    if panel_bbox:
        parts.append(_ts("Panel box: {box}").format(box=panel_bbox))
    return "\n".join(parts)


def _action_tooltip(label: str, action_id: str | None, blocked_codes_value: object) -> str:
    blocked = _warning_text(blocked_codes_value)
    if blocked:
        return _ts("{label}: {blocked}").format(label=label, blocked=blocked)
    if action_id is None:
        return _ts("{label}: Unavailable").format(label=label)
    return label


def _action_label(action_key: str | None) -> str:
    if action_key == "apply_as_selection":
        return _ts("Select")
    if action_key == "protect_text":
        return _ts("Protect")
    if action_key == "send_to_inpaint":
        return _ts("Inpaint")
    if action_key == "export_package":
        return _ts("Export")
    if action_key == "create_changeset":
        return _ts("Changeset")
    return action_key or _ts("Action")


def _accepted_text(value: object) -> str:
    return _ts("Yes") if bool(value) else _ts("No")


def _warning_text(value: object) -> str:
    return ", ".join(_string_list(value))


def _score_text(value: object) -> str:
    numeric = _optional_float(value)
    if numeric is None:
        return ""
    return f"{round(max(0.0, min(1.0, numeric)) * 100)}%"


def _area_text(value: object) -> str:
    number = _optional_number(value)
    if number is None:
        return ""
    return str(int(max(0.0, number)))


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


def _optional_int(value: object) -> int | None:
    number = _optional_number(value)
    if number is None:
        return None
    return int(number)


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


__all__ = ["Sam3MangaMaskAssistantTableWidget"]
