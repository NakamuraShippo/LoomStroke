"""Refresh document-driven list changes without interrupting cell edits."""

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QAbstractItemView

from loomstroke.core.events import Events


class DialogueListRefresh(QObject):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._refresh)
        self.handler = self._changed
        events = getattr(panel._tool.host_api, "events", None)
        if events is not None:
            events.on(Events.DOC_CHANGED, self.handler)

    def _changed(self, *_):
        if self.panel.tabs.currentIndex() == 2 and self.panel.isVisible():
            self.timer.start(0)

    def _refresh(self):
        if self.panel.tabs.currentIndex() != 2 or not self.panel.isVisible():
            return
        if self.panel.dialogue_table.state() == QAbstractItemView.State.EditingState:
            self.timer.start(200)
            return
        self.panel.refresh_dialogue()
