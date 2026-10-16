"""Keep the Panels list in sync with document history without refreshing editors."""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer

from loomstroke.core.events import Events


class PanelOrderRefresh(QObject):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self.handler = self.schedule
        self.closed = False
        state = panel._tool.read_state()
        self.gutter = state.layout.gutter_px if state and state.layout else None
        self.bus = getattr(panel._tool.host_api, "events", None)
        self.tokens = []
        if self.bus is not None:
            for event in (
                Events.HISTORY_CHANGED,
                Events.DOC_LOADED,
                Events.DOC_CREATED,
                "pages.active.changed",
            ):
                self.tokens.append((event, self.bus.on(event, self.handler)))
        panel.installEventFilter(self)
        panel.tabs.currentChanged.connect(self.schedule)

    def schedule(self, *_):
        if not self.closed and self.panel.isVisible() and self.panel.tabs.currentIndex() == 0:
            self.timer.start(0)

    def eventFilter(self, watched, event):
        if watched is self.panel and event.type() == QEvent.Type.Show:
            self.schedule()
        return super().eventFilter(watched, event)

    def refresh(self):
        if self.closed or not self.panel.isVisible() or self.panel.tabs.currentIndex() != 0:
            return
        state = self.panel._tool.read_state()
        rows = tuple(
            self.panel.order_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.panel.order_list.count())
        )
        order = state.reading_order if state else ()
        gutter = state.layout.gutter_px if state and state.layout else None
        changed_gutter = gutter != self.gutter
        self.gutter = gutter
        if rows != order or changed_gutter:
            if self.panel._tool.selected_panel_id not in order:
                self.panel._tool.selected_panel_id = order[0] if order else None
            self.panel.refresh_from_tool()

    def dispose(self):
        self.closed = True
        self.timer.stop()
        for event, token in self.tokens:
            self.bus.off(event, token)
        self.tokens.clear()
