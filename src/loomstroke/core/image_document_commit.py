"""Publish one completely prepared image document with reversible assignments."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .canvas_service_simple import SimpleCanvasService
from .image_document_state import ImageDocumentStateBundle, image_document_state_bundles

if TYPE_CHECKING:
    from .document_service import DocumentServiceImpl
    from .image_document_preparation import PreparedImageDocument

logger = logging.getLogger(__name__)


def _install_bundle(bundle: ImageDocumentStateBundle) -> None:
    """Assignment-only critical section; kept separate for failure injection."""
    for name, value in bundle.values.items():
        _install_value(bundle.target, name, value)


def _install_value(target: Any, name: str, value: Any) -> None:
    setattr(target, name, value)


def commit_image_document(
    owner: DocumentServiceImpl,
    prepared: PreparedImageDocument,
    *,
    on_committed: Callable[[], None] | None = None,
) -> bool:
    """Transfer staged state once; any installer failure restores exact old owners."""
    canvas = owner._canvas
    if prepared.committed:
        raise ValueError("The prepared image document has already been committed")
    if (
        not isinstance(canvas, SimpleCanvasService)
        or not isinstance(prepared.canvas, SimpleCanvasService)
        or prepared.canvas is canvas
    ):
        raise ValueError("Image document commit requires a separate supported staging canvas")
    if (
        owner._save_in_progress
        or canvas._in_transaction
        or canvas._active_history_group is not None
    ):
        raise ValueError("Finish the current save or editing transaction before opening an image")
    if canvas._mask_paint_dirty or canvas._stroke_painting_active or canvas._defer_recompose_depth:
        raise ValueError("Finish the current painting operation before opening an image")
    pages = owner._page_service
    if pages is not None and pages._history_state_notifier._depth:
        raise ValueError("Finish the current page operation before opening an image")
    if not prepared.canvas._layers or prepared.canvas._undo_stack or prepared.canvas._redo_stack:
        raise ValueError("The staging document is incomplete or contains editing history")
    bundles = image_document_state_bundles(owner, prepared)
    try:
        for bundle in bundles:
            _install_bundle(bundle)
    except BaseException:
        # No callbacks, render, file I/O, container.clear or layer mutation has
        # occurred. Old images, stacks and lazy loaders remain valid by identity.
        for bundle in reversed(bundles):
            bundle.restore()
        raise
    prepared.committed = True
    _finish_old_document_edit(owner)
    if on_committed is not None:
        try:
            # The caller settles document identity/path before page/history
            # subscribers can observe the newly committed document data.
            on_committed()
        except Exception:
            logger.exception("Image document committed; document-boundary callback failed")
    _publish_committed_document(owner)
    return True


def _finish_old_document_edit(owner: DocumentServiceImpl) -> None:
    """Invalidate the old edit before boundary listeners can begin a new one."""
    canvas = owner._canvas
    filter_pen = getattr(canvas, "_filter_pen_service", None)
    callbacks = [canvas._mask_commit_timer.stop]
    if filter_pen is not None and callable(getattr(filter_pen, "cancel", None)):
        callbacks.append(filter_pen.cancel)
    for callback in callbacks:
        try:
            callback()
        except Exception:
            logger.exception("Image document committed; old edit cleanup failed")


def _publish_committed_document(owner: DocumentServiceImpl) -> None:
    """After the commit point, notification trouble cannot mean document failure."""
    canvas = owner._canvas
    callbacks = [canvas._emit_selection_changed]
    pages = owner._page_service
    if pages is not None:
        callbacks.extend(
            (
                pages._emit_pages_changed,
                lambda: pages._emit_active_page_changed(pages.get_active_page_id()),
                pages._history_state_notifier.mark_changed,
            )
        )
    else:
        callbacks.append(canvas._emit_history_changed)
    callbacks.extend((lambda: owner._emit_history_reset("load"), canvas._recompose))
    for callback in callbacks:
        try:
            callback()
        except Exception:
            logger.exception("Image document committed; post-commit notification failed")
