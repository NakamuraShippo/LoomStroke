"""Handle recovery selection without equating dismissal with data deletion."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..io.autosave import RecoveryInfo
from .dialogs.recovery_dialog import RecoveryDialog

logger = logging.getLogger(__name__)


def handle_recovery_dialog(
    recoveries: list[Any],
    *,
    manager: Any,
    window: Any = None,
    dialog_factory: Callable[..., Any] | None = None,
) -> None:
    valid = [entry for entry in recoveries if isinstance(entry, RecoveryInfo)]
    if not valid or manager is None:
        return
    dialog = (dialog_factory or RecoveryDialog)(valid, parent=window)
    if dialog.exec() == dialog.DialogCode.Accepted:
        selected = dialog.selected_recovery
        if selected is None:
            return
        if not manager.recover(selected):
            logger.warning("Recovery failed; candidates retained")
            return
        marker = getattr(window, "mark_document_recovered_from_autosave", None)
        if callable(marker):
            marker(original_path=selected.original_path, recovery_path=selected.file_path)
        logger.info("Document recovered; recovery sources retained")
    elif dialog.discard_requested is True:
        manager.cleanup_all_old_sessions()
