"""Select a dialogue's balloon without navigating away from the dialogue tab."""

import logging

from PySide6.QtCore import Qt

from .dialogue_editing import begin_edit
from .panel_formatting import _ts

logger = logging.getLogger(__name__)


def select_dialogue_row(panel, current, previous):
    if current is None or panel._updating:
        return
    if len(panel.dialogue_table.selectionModel().selectedRows()) > 1:
        return
    data = current.data(Qt.ItemDataRole.UserRole)
    if not data or (previous is not None and previous.data(Qt.ItemDataRole.UserRole) == data):
        return
    page_id, balloon_id = data
    try:
        begin_edit(panel._tool, str(page_id), str(balloon_id))
    except Exception:
        logger.exception("Could not select dialogue for formatting")
        panel.formatting.target = None
        panel.formatting.scroll.widget().setEnabled(False)
        panel.formatting.status.setText(
            _ts("Could not apply formatting. Reselect the balloon and try again.")
        )
        return
    panel._tool.select_balloon(str(balloon_id))
    panel.formatting.sync()
