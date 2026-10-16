"""A single worker owns the isolated GPU filter processor."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QObject, Signal

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names

from .snapshot import FilterPenSnapshot

logger = logging.getLogger(__name__)


class FilterPenWorker(QObject):
    ready = Signal(object, object, object)

    def __init__(self, factory: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._factory = factory
        self._processor: Any = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="FilterPen")
        self._closed = False
        self._submitted = False

    def submit(
        self,
        token: int,
        snapshot: FilterPenSnapshot,
        filter_type: str | None = None,
        params: dict | None = None,
        *,
        effect: str | None = None,
    ) -> None:
        if effect is not None:
            if filter_type is not None and filter_type != effect:
                raise TypeError("Conflicting filter_type and effect")
            filter_type = effect
        if filter_type is None or params is None:
            raise TypeError("submit() requires filter_type and params")
        source = snapshot.image

        def render() -> None:
            try:
                if self._closed:
                    return
                if self._processor is None:
                    self._processor = self._factory()
                result = self._processor.filter_image(
                    source,
                    filter_type,
                    params,
                    document_dpi=snapshot.document_dpi,
                    layer_origin=snapshot.origin,
                    canvas_size=snapshot.canvas_size,
                )
            except Exception as exc:
                self._emit_result(token, None, exc)
            else:
                self._emit_result(token, result, None)

        self._submitted = True
        self._executor.submit(render)

    def _emit_result(self, token: int, result: Any, error: Any) -> None:
        if self._closed:
            return
        try:
            self.ready.emit(token, result, error)
        except RuntimeError:
            # A worker may finish while Qt is destroying its parent canvas.
            logger.debug("Filter Pen result discarded after QObject destruction")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._submitted:
            # An unused service may be destroyed after interpreter shutdown
            # has stopped executors; it has no GPU resources to release.
            self._executor.shutdown(wait=False, cancel_futures=False)
            return

        def release() -> None:
            if self._processor is not None:
                try:
                    self._processor.close()
                except Exception:
                    logger.exception("Filter Pen processor cleanup failed")
                finally:
                    self._processor = None

        self._executor.submit(release)
        self._executor.shutdown(wait=False, cancel_futures=False)


# Compatibility aliases for existing imports.
EffectPenWorker = FilterPenWorker


_bind_legacy_names(
    __name__, {"EffectPenSnapshot": "FilterPenSnapshot", "EffectPenWorker": "FilterPenWorker"}
)
