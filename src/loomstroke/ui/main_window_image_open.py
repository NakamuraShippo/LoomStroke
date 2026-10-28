"""Present a fully prepared image document through the existing UI lifecycle."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def open_image_document(workflow: Any, path: Path, *, layered: bool) -> bool:
    window = workflow._window
    service = window._get_document_service()
    prepare = getattr(service, "_prepare_image_document", None)
    commit = getattr(service, "_commit_image_document", None)
    if not callable(prepare) or not callable(commit):
        # Older/custom services may still import layers, but a destructive
        # reset+import fallback cannot promise to preserve an open document.
        workflow._message_box().critical(
            window,
            window._ts("Load Failed"),
            window._ts("Unable to load document: {path}").format(path=path),
        )
        return False
    original = workflow._replacement_guard.capture_state()
    capture_pending = workflow._replacement_guard._capture_blocked()
    try:
        prepared = prepare(path, layered=layered)
        if (
            workflow._replacement_guard.capture_state() != original
            or workflow._replacement_guard._capture_blocked() != capture_pending
        ):
            return False

        def on_committed() -> None:
            # Publish the new identity before page/history/render observers.
            # No fallible display work precedes this lifecycle boundary.
            window._document_path = None
            window._document_dirty = False
            window._has_document = True
            workflow._emit_document_created(
                {
                    "width": prepared.metadata.width,
                    "height": prepared.metadata.height,
                    "source": "psd_import" if layered else "image_import",
                    "path": str(path),
                }
            )

        if commit(prepared, on_committed=on_committed) is not True:
            raise ValueError("The prepared document was not committed")
    except Exception:
        logger.exception("Image document preparation or commit failed")
        workflow._message_box().critical(
            window,
            window._ts("Load Failed"),
            window._ts("Unable to load document: {path}").format(path=path),
        )
        return False

    for update in (
        window._update_window_title,
        lambda: window._set_document_present(True),
        lambda: window.statusBar().showMessage(f"Opened {path.name}", 3000),
    ):
        try:
            update()
        except Exception:
            logger.exception("Image document committed; display update failed")
    return True
