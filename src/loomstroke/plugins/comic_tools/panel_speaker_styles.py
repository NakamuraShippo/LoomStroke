"""Manage speaker styles using the selected text's existing Font formatting UI."""

import logging
from copy import deepcopy

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from loomstroke.core.events import Events
from loomstroke.core.japanese_text.speaker_styles import has_style_overrides

from .panel_formatting import _ts
from .speaker_style_actions import perform_style_action
from .speaker_tag_widget import SpeakerTagWidget, tag_icon

logger = logging.getLogger(__name__)


def speaker_styles_title():
    return _ts("Speaker styles")


class SpeakerStylesWidget(QWidget):
    def __init__(self, tool, parent=None):
        super().__init__(parent)
        self.tool = tool
        self._registry = None
        self._counts = {}
        self.labels, self.buttons = [], {}
        form = QFormLayout(self)
        self.styles = QComboBox()
        form.addRow(self.styles)
        self.usage = QLabel()
        form.addRow(self.usage)
        self.name, self.speaker_id, self.speaker_name = QLineEdit(), QLineEdit(), QLineEdit()
        self.color_tag = SpeakerTagWidget()
        for widget, source in (
            (self.name, "Style name"),
            (self.speaker_id, "Speaker ID"),
            (self.speaker_name, "Speaker name"),
            (self.color_tag, "Color tag"),
        ):
            label = QLabel()
            self.labels.append((label, source))
            form.addRow(label, widget)
        self.default = QCheckBox()
        form.addRow(self.default)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        form.addRow(self.hint)
        for action, source in (
            ("register", "Register from selected text"),
            ("update", "Update from selected text"),
            ("details", "Update style details"),
            ("duplicate", "Duplicate style"),
            ("delete", "Delete style"),
            ("apply", "Apply to selected balloon"),
            ("clear", "Clear style overrides"),
            ("export", "Save user defaults"),
            ("import", "Load user defaults"),
        ):
            button = QPushButton()
            button.clicked.connect(lambda checked=False, action=action: self._run(action))
            self.buttons[action] = (button, source)
            form.addRow(button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addRow(self.status)
        self.styles.currentIndexChanged.connect(self._selected)
        self.retranslate()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        self._document_changed = self._queue_refresh
        events = getattr(tool.host_api, "events", None)
        if events is not None:
            events.on(Events.DOC_CHANGED, self._document_changed)

    def _queue_refresh(self, *_):
        if self.isVisible() and not self._refresh_timer.isActive():
            self._refresh_timer.start(0)

    def refresh(self):
        service = getattr(self.tool.host_api.pages, "speaker_styles", None)
        self.setEnabled(service is not None)
        if service is None:
            return
        registry = service.read()
        self._counts = getattr(service, "usage_counts", lambda: {})()
        self._show_usage()
        state = self.tool.read_state()
        obj = state.text_objects.get(self.tool.selected_balloon_id) if state else None
        if obj is not None and has_style_overrides(obj.meta["text_tool"]):
            self.status.setText(_ts("+ Selected text has style overrides"))
        elif self.status.text() == _ts("+ Selected text has style overrides"):
            self.status.clear()
        if registry == self._registry:
            return
        self._registry = deepcopy(registry)
        previous = self.styles.currentData()
        blocker = QSignalBlocker(self.styles)
        self.styles.clear()
        for style in registry.get("styles", []):
            self.styles.addItem(
                tag_icon(style.get("color_tag", "")),
                style.get("display_name") or style["style_id"],
                style["style_id"],
            )
        index = self.styles.findData(previous)
        if index >= 0:
            self.styles.setCurrentIndex(index)
        del blocker
        self._selected()

    def _selected(self):
        service = getattr(self.tool.host_api.pages, "speaker_styles", None)
        if service is None:
            return
        registry = service.read()
        selected = next(
            (s for s in registry.get("styles", []) if s["style_id"] == self.styles.currentData()),
            {},
        )
        self.name.setText(selected.get("display_name", ""))
        self.speaker_id.setText(selected.get("speaker", {}).get("speaker_id", ""))
        self.speaker_name.setText(selected.get("speaker", {}).get("speaker_name", ""))
        self.color_tag.value.setText(selected.get("color_tag", ""))
        self._show_usage()
        self.default.setChecked(
            bool(selected) and selected.get("style_id") == registry.get("default_style_id")
        )

    def _run(self, action):
        try:
            perform_style_action(
                self.tool,
                action,
                self.styles.currentData(),
                {
                    "display_name": self.name.text().strip(),
                    "speaker_id": self.speaker_id.text().strip(),
                    "speaker_name": self.speaker_name.text().strip(),
                    "default": self.default.isChecked(),
                    "color_tag": self.color_tag.color(),
                },
            )
            self.status.clear()
            self.refresh()
        except (ValueError, OSError, KeyError):
            logger.exception("Speaker style operation failed")
            self.status.setText(
                _ts("Could not update speaker styles. Check the selection and locked layers.")
            )

    def retranslate(self):
        for label, source in self.labels:
            label.setText(_ts(source))
        for button, source in self.buttons.values():
            button.setText(_ts(source))
        self.default.setText(_ts("Default for unregistered speakers"))
        self.color_tag.retranslate()
        self._show_usage()
        self.hint.setText(
            _ts("Edit the selected text in Font, then register or update its speaker style.")
        )

    def _show_usage(self):
        self.usage.setText(
            _ts("Used by {count} text frames").format(
                count=self._counts.get(self.styles.currentData(), 0)
            )
        )
        for index in range(self.styles.count()):
            count = self._counts.get(self.styles.itemData(index), 0)
            self.styles.setItemData(
                index,
                _ts("Used by {count} text frames").format(count=count),
                Qt.ItemDataRole.ToolTipRole,
            )

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
