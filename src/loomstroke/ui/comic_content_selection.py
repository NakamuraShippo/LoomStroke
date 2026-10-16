"""Route an existing layer gesture to the selected whole comic units."""

import logging

from loomstroke.core.comic_panels.content_batch_command import content_batch_command
from loomstroke.core.comic_panels.models import PanelFrameError

logger = logging.getLogger(__name__)


def submit_selected_comic_content(window, canvas, operation):
    panel = getattr(window, "_layer_panel", None)
    selected = getattr(panel, "_get_selected_layer_ids", None)
    if not callable(selected):
        return False
    try:
        command = content_batch_command(canvas, selected(), operation)
        if command is None:
            return False
        if canvas.submit(command):
            return True
        reason = command.last_error
    except PanelFrameError as exc:
        reason = str(exc)
    logger.warning("Comic content operation rejected: %s", reason)
    status = getattr(window, "statusBar", None)
    if callable(status):
        status().showMessage(reason, 5000)
    return True
