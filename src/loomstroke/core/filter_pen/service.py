"""Own filter stroke snapshots, worker results and the command-backed live edits."""

from __future__ import annotations

import copy
import logging
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from loomstroke.core.canvas_command_commit_guard import CanvasCommandCommitGuard
from loomstroke.core.command_types import BrushSettings
from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names
from loomstroke.core.filters import FilterRegistry

from .command import FilterPenStrokeCommand
from .coverage import FilterPenCoverage
from .snapshot import FilterPenSnapshot
from .worker import FilterPenWorker

logger = logging.getLogger(__name__)


@dataclass
class FilterPenSession:
    snapshot: FilterPenSnapshot
    coverage: FilterPenCoverage
    command: FilterPenStrokeCommand
    strength: float
    rollback_guard: CanvasCommandCommitGuard
    filtered: QImage | None = None
    pending_points: deque[Any] = field(default_factory=deque)
    pending_dab: Iterator[Any] | None = None
    pending_tiles: set[tuple[int, int]] = field(default_factory=set)
    released: bool = False
    announced: bool = False


class FilterPenService(QObject):
    failed = Signal(str)
    finished = Signal()

    def __init__(self, canvas: Any, processor_factory: Any) -> None:
        super().__init__(canvas if isinstance(canvas, QObject) else None)
        self.canvas = canvas
        self._worker = FilterPenWorker(processor_factory, self)
        self._worker.ready.connect(self._ready)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._flush)
        self._session: FilterPenSession | None = None
        self._token = 0
        self._closed = False
        self._updating = False
        self._quit_connected = False

    @property
    def updating(self) -> bool:
        return self._updating

    @property
    def active(self) -> bool:
        return self._session is not None and not self._session.released

    @property
    def editable(self) -> bool:
        session = self._session
        return bool(session and session.announced and self._valid(session))

    def begin(
        self,
        filter_type: str | None = None,
        params: dict | None = None,
        brush: BrushSettings | None = None,
        *,
        tip_path: str | None = None,
        hardness_mode: str = "tip",
        tilt_angle: bool = False,
        page_id: str | None = None,
        effect_type: str | None = None,
    ) -> bool:
        if effect_type is not None:
            if filter_type is not None and filter_type != effect_type:
                raise TypeError("Conflicting filter_type and effect_type")
            filter_type = effect_type
        if filter_type is None or params is None or brush is None:
            raise TypeError("begin() requires filter_type, params and brush")
        del page_id  # The captured layer object prevents same-ID cross-page late writes.
        if self._session is not None and (
            self._session.filtered is None
            or self._session.pending_points
            or self._session.pending_dab is not None
            or self._session.pending_tiles
        ):
            self.failed.emit("The previous filter stroke is still processing. Please wait.")
            return False
        self.finalize()
        if self._closed:
            return False
        try:
            if FilterRegistry.get(filter_type) is None:
                raise ValueError("The selected filter is not available.")
            snapshot = FilterPenSnapshot.capture(self.canvas)
            coverage = FilterPenCoverage(
                (snapshot.image.width(), snapshot.image.height()),
                snapshot.origin,
                brush,
                tip_path=tip_path,
                hardness_mode=hardness_mode,
                tilt_angle=tilt_angle,
                geometry=snapshot.geometry,
            )
            self._session = FilterPenSession(
                snapshot,
                coverage,
                FilterPenStrokeCommand(snapshot),
                brush.opacity,
                CanvasCommandCommitGuard(self.canvas),
            )
            self._token += 1
            if not self._quit_connected:
                app = QCoreApplication.instance()
                if app is not None:
                    # qasync stops Qt once after initialization. Subscribe on
                    # first use so only the editing loop's quit closes us.
                    app.aboutToQuit.connect(self.close)
                    self._quit_connected = True
            self._worker.submit(self._token, snapshot, filter_type, copy.deepcopy(params))
            return True
        except Exception as exc:
            self._session = None
            self.failed.emit(str(exc))
            return False

    def append(self, points: list[Any]) -> None:
        session = self._session
        if session is None or session.released:
            return
        if not self._valid(session):
            self._stale()
            return
        session.pending_points.extend(points)
        try:
            self._consume_points(session)
        except Exception:
            logger.exception("Filter Pen coverage failed")
            self.cancel()
            self.failed.emit("The filter could not be applied. The original pixels were preserved.")
            return
        self._schedule(session)

    def finish(self) -> None:
        session = self._session
        if session is None or session.released:
            return
        session.released = True
        self._flush()

    def finalize(self) -> None:
        self.finish()
        self._timer.stop()
        pending = self._session is not None and (
            self._session.filtered is None
            or self._session.pending_points
            or self._session.pending_dab is not None
            or self._session.pending_tiles
        )
        self._token += 1
        self._session = None
        if pending:
            self.failed.emit(
                "The pending filter stroke was cancelled because the editing target changed."
            )

    def cancel(self) -> None:
        session = self._session
        if session and session.command._executed and self._valid(session):
            self._updating = True
            try:
                session.rollback_guard.rollback(session.command)
            finally:
                self._updating = False
        self._timer.stop()
        self._token += 1
        self._session = None

    def before_command(self, command: Any) -> None:
        if (
            not getattr(
                command, "filter_pen_command", getattr(command, "effect_pen_command", False)
            )
            and not self._updating
        ):
            self.finalize()

    def replace_path(self, points: list[Any]) -> bool:
        session = self._session
        if session is None or session.filtered is None or not self._valid(session):
            return False
        session.pending_points.clear()
        session.pending_dab = None
        session.coverage.clear_path()
        session.pending_tiles.update(session.coverage.take_dirty_tiles())
        session.pending_tiles.update(session.command.tiles)
        session.pending_points.extend(points)
        self._flush()
        return self._session is session and self._valid(session)

    @Slot()
    def close(self) -> None:
        # Qt may invoke the declared slot after Python has cleared this wrapper.
        if getattr(self, "_closed", True):
            return
        self.finalize()
        self._closed = True
        self._worker.close()

    def _valid(self, session: FilterPenSession) -> bool:
        return session.snapshot.valid(
            self.canvas,
            session.command.expected_version,
            native_version=session.command.expected_native_version,
        )

    def _stale(self) -> None:
        self._timer.stop()
        self._token += 1
        self._session = None
        self.failed.emit("The layer changed. The pending filter stroke was stopped.")

    def _ready(self, token: int, image: QImage | None, error: Any) -> None:
        if self._closed or token != self._token or self._session is None:
            return
        if error is not None:
            logger.warning(
                "Filter Pen filter failed", exc_info=(type(error), error, error.__traceback__)
            )
            self._session = None
            self.failed.emit("The filter could not be applied. The original pixels were preserved.")
            return
        if not self._valid(self._session):
            self._stale()
            return
        if image is None or image.isNull() or image.size() != self._session.snapshot.image.size():
            self._session = None
            self.failed.emit(
                "The filter returned an invalid image. The original pixels were preserved."
            )
            return
        self._session.filtered = image
        self._flush()

    def _flush(self) -> None:
        session = self._session
        if session is None:
            self._timer.stop()
            return
        if not self._valid(session):
            self._stale()
            return
        self._updating = True
        try:
            self._consume_points(session)
            if session.filtered is None:
                self._schedule(session)
                return
            self._commit_tiles(session)
            self._schedule(session)
            if (
                session.released
                and not session.announced
                and not session.pending_points
                and session.pending_dab is None
                and not session.pending_tiles
            ):
                session.announced = True
                if session.command._executed:
                    self.finished.emit()
        except Exception:
            logger.exception("Filter Pen stroke update failed")
            self._timer.stop()
            self._session = None
            self.failed.emit(
                "The filter stroke failed. Previously committed pixels were preserved."
            )
        finally:
            self._updating = False

    def _consume_points(self, session: FilterPenSession) -> None:
        deadline = time.perf_counter() + 0.004
        while session.pending_points or session.pending_dab is not None:
            if session.pending_dab is None:
                session.pending_dab = session.coverage.iter_add(session.pending_points.popleft())
            try:
                next(session.pending_dab)
            except StopIteration:
                session.pending_dab = None
            session.pending_tiles.update(session.coverage.take_dirty_tiles())
            if time.perf_counter() >= deadline:
                break

    def _commit_tiles(self, session: FilterPenSession) -> None:
        # Commit a bounded tile batch. Long lines and large tips continue next frame.
        keys = {session.pending_tiles.pop() for _ in range(min(8, len(session.pending_tiles)))}
        if not keys:
            return
        changes = session.command.prepare(
            session.snapshot,
            session.filtered,
            session.coverage,
            session.snapshot.image.rect(),
            session.strength,
            keys,
        )
        if not changes:
            return
        if session.command._executed:
            if not session.command.update(self.canvas, changes):
                raise RuntimeError("The filter stroke could not update its target.")
        else:
            session.command.tiles = changes
            if not self.canvas.submit(session.command):
                raise RuntimeError("The filter stroke could not be committed.")

    def _schedule(self, session: FilterPenSession) -> None:
        if (
            session.pending_points
            or session.pending_dab is not None
            or (session.filtered is not None and (session.pending_tiles or not session.released))
        ):
            # Keep the clock during a live stroke: restarting after an empty
            # commit adds a second 16 ms wait behind the canvas input coalescer.
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()


# Compatibility aliases for existing imports.
EffectPenSession = FilterPenSession
EffectPenService = FilterPenService


_bind_legacy_names(
    __name__,
    {
        "EffectRegistry": "FilterRegistry",
        "EffectPenStrokeCommand": "FilterPenStrokeCommand",
        "EffectPenCoverage": "FilterPenCoverage",
        "EffectPenSnapshot": "FilterPenSnapshot",
        "EffectPenWorker": "FilterPenWorker",
        "EffectPenSession": "FilterPenSession",
        "EffectPenService": "FilterPenService",
    },
)
