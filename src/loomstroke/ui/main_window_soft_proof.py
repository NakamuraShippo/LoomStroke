"""View-menu soft proof controller (CT-11 P2c, spec 11 §8.2/§10).

Session-only: the chosen profile and the on/off state never touch the
document or ``.lstroke`` (spec 11 §11).  The LUT is built off the UI
thread (spec 11 §12) and delivered back through a queued Qt signal; a
generation counter drops results that arrive after the user toggled
again.  Proofing is honest by construction: while the LUT is still
building, the canvas stays *unproofed* and the menu item stays checked
only after the LUT is actually applied.
"""

from __future__ import annotations

import logging
from concurrent.futures import Executor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..io.color_profile_registry import ColorProfileRegistry
from ..io.soft_proof_lut import build_proof_lut

logger = logging.getLogger(__name__)

__all__ = ["MainWindowSoftProofController"]


class MainWindowSoftProofController(QObject):
    """Owns the proof registry, the worker build, and the menu state."""

    _lut_ready = Signal(int, object)  # (generation, ProofLut | Exception)

    def __init__(
        self,
        window: Any,
        *,
        executor: Executor | None = None,
        file_dialog: Any = QFileDialog,
        message_box: Any = QMessageBox,
    ) -> None:
        super().__init__(window if isinstance(window, QObject) else None)
        self._window = window
        self._executor = executor or ThreadPoolExecutor(max_workers=1)
        self._file_dialog = file_dialog
        self._message_box = message_box
        self._registry = ColorProfileRegistry()
        self._proof_profile_id: str | None = None
        self._proof_profile_name: str = ""
        self._enabled = False
        self._generation = 0
        # Queued back to the UI thread (worker threads emit this signal).
        self._lut_ready.connect(self._on_lut_ready)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def proof_profile_name(self) -> str:
        return self._proof_profile_name

    def on_toggle(self, on: bool) -> None:
        if not on:
            self._enabled = False
            self._generation += 1  # in-flight builds become stale
            self._apply_lut(None)
            return
        if self._proof_profile_id is None and not self.choose_profile():
            self._sync_action_checked(False)
            return
        self._enabled = True
        self._start_build()

    def choose_profile(self) -> bool:
        """Pick and register an ICC profile; rebuild if proofing is on."""

        window = self._window
        path, _ = self._file_dialog.getOpenFileName(
            window,
            window._ts("Choose Proof Profile"),
            getattr(window, "_last_file_dir", ""),
            window._ts("ICC Profile (*.icc *.icm);;All Files (*.*)"),
        )
        if not path:
            return False
        try:
            info = self._registry.register(Path(path).read_bytes())
        except (OSError, ValueError):
            logger.warning("proof profile rejected: %s", path, exc_info=True)
            self._message_box.warning(
                window,
                window._ts("Soft Proof"),
                window._ts("The file is not a usable ICC profile."),
            )
            return False
        self._proof_profile_id = info.profile_id
        self._proof_profile_name = info.name
        if self._enabled:
            self._start_build()
        return True

    def _start_build(self) -> None:
        profile_id = self._proof_profile_id
        if profile_id is None:
            return
        source_id = self._registry.srgb().profile_id
        self._generation += 1
        generation = self._generation

        def _build() -> None:
            try:
                lut = build_proof_lut(
                    self._registry,
                    source_profile_id=source_id,
                    proof_profile_id=profile_id,
                )
            except Exception as error:  # delivered to the UI thread below
                self._lut_ready.emit(generation, error)
                return
            self._lut_ready.emit(generation, lut)

        self._executor.submit(_build)

    def _on_lut_ready(self, generation: int, result: Any) -> None:
        if generation != self._generation or not self._enabled:
            return  # toggled off or superseded while building
        if isinstance(result, Exception):
            logger.warning("soft proof LUT build failed", exc_info=result)
            self._enabled = False
            self._sync_action_checked(False)
            self._message_box.warning(
                self._window,
                self._window._ts("Soft Proof"),
                self._window._ts("Soft proof could not be enabled for this profile."),
            )
            return
        self._apply_lut(result)
        self._sync_action_checked(True)

    def _apply_lut(self, lut: Any | None) -> None:
        canvas = getattr(self._window, "canvas", None)
        setter = getattr(canvas, "set_soft_proof_lut", None)
        if callable(setter):
            setter(lut)

    def _sync_action_checked(self, checked: bool) -> None:
        actions = getattr(self._window, "_shortcut_actions", {})
        action = actions.get("view.soft_proof")
        if action is None:
            return
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)
