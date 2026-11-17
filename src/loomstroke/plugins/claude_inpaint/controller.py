"""Run the inpaint kernel off the UI thread and commit only fresh results."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot

from loomstroke.core.telea_inpaint import (
    TeleaInpaintCommand,
    TeleaJob,
    capture_telea_job,
    prepared_telea_command,
    telea_job_current,
)

from ..common.page_context import page_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepairMessages:
    busy: str
    failed: str
    discarded: str
    commit_failed: str


class _Signals(QObject):
    finished = Signal(object, object)


class _Worker(QRunnable):
    def __init__(
        self,
        job: TeleaJob,
        processor: Callable[[np.ndarray, np.ndarray, int], np.ndarray],
        messages: RepairMessages,
        kind: str,
    ) -> None:
        super().__init__()
        self.job = job
        self.processor = processor
        self.messages = messages
        self.kind = kind
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.processor(self.job.pixels, self.job.mask, self.job.radius)
        except Exception as error:
            logger.warning("%s worker failed: %s", self.kind, type(error).__name__)
            self.signals.finished.emit(None, self.messages.failed)
        else:
            self.signals.finished.emit(result, None)


class LocalRepairPreparationController(QObject):
    def __init__(
        self,
        tool: Any,
        *,
        processor: Callable[[np.ndarray, np.ndarray, int], np.ndarray],
        command_type: type[TeleaInpaintCommand],
        kind: str,
        domain: str,
        messages: RepairMessages,
        context_padding: int | None = None,
    ) -> None:
        super().__init__()
        self._tool = tool
        self._processor = processor
        self._command_type = command_type
        self._kind = kind
        self._domain = domain
        self._messages = messages
        self._context_padding = context_padding
        self._worker: _Worker | None = None
        self._job: TeleaJob | None = None
        self._page_context: Any = None
        self._cancelled = False

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def cancel(self) -> None:
        self._cancelled = True

    def prepare(self, points: tuple[tuple[float, float], ...], size: int, radius: int) -> None:
        host = self._tool.host_api
        if host is None:
            return
        if self.busy:
            self._notify(self._messages.busy)
            return
        try:
            job = capture_telea_job(
                host.canvas, points, size, radius, context_padding=self._context_padding
            )
        except (ValueError, RuntimeError) as error:
            self._notify(str(error))
            return
        self._job = job
        self._page_context = page_context(host)
        self._cancelled = False
        worker = _Worker(job, self._processor, self._messages, self._kind)
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object, object)
    def _finished(self, output: Any, error: Any) -> None:
        job, page = self._job, self._page_context
        self._worker, self._job, self._page_context = None, None, None
        host = self._tool.host_api
        if self._cancelled or host is None or job is None:
            return
        if error is not None:
            self._notify(error)
            return
        if page_context(host) != page:
            self._notify(self._messages.discarded)
            return
        if not telea_job_current(host.canvas, job):
            self._notify(self._messages.discarded)
            return
        command = prepared_telea_command(
            host.canvas,
            job,
            output,
            command_type=self._command_type,
            kind=self._kind,
        )
        if command is not None:
            try:
                applied = host.canvas.submit(command)
            except Exception as commit_error:
                logger.warning("%s commit failed: %s", self._kind, type(commit_error).__name__)
                applied = False
            if not applied:
                self._notify(self._messages.commit_failed)

    def _notify(self, source: str) -> None:
        host = self._tool.host_api
        show = getattr(getattr(host, "ui", None), "show_message", None)
        if callable(show):
            from loomstroke.ui.translations import translate_with_fallback  # noqa: PLC0415

            show(translate_with_fallback(self._domain, source))
