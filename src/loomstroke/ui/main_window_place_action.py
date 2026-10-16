"""``File > Place Image…`` (CT-18): pick a file, choose Link / Embed, place it as a layer."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog, QMessageBox

from loomstroke.core.linked_assets import ResourceMode

from .dialogs.place_mode_dialog import PlaceModeDialog
from .document_path_locator import DocumentPathLocator
from .resource_link_coordinator import ResourceLinkCoordinator
from .translations import translated_literal_domain

logger = logging.getLogger(__name__)

__all__ = ["MainWindowPlaceAction"]

_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.psd);;All files (*)"


def _ts(text: str) -> str:
    return translated_literal_domain("LinksPanel", text, use_fallback=True)


class MainWindowPlaceAction:
    def __init__(
        self,
        window: Any,
        *,
        file_picker: Callable[[], str] | None = None,
        mode_picker: Callable[[str], ResourceMode | None] | None = None,
        message_box: Any = QMessageBox,
    ) -> None:
        self._window = window
        self._file_picker = file_picker or self._pick_file
        self._mode_picker = mode_picker or (lambda name: PlaceModeDialog.ask(name, window))
        self._message_box = message_box

    def coordinator(self) -> ResourceLinkCoordinator:
        host = getattr(self._window, "_host_api", None) or getattr(self._window, "host", None)
        return ResourceLinkCoordinator(
            host, document_dir_provider=DocumentPathLocator(self._window).document_dir
        )

    def run(self) -> str | None:
        chosen = self._file_picker()
        if not chosen:
            return None
        path = Path(chosen)
        mode = self._mode_picker(path.name)
        if mode is None:
            return None
        return self.place(path, mode)

    def place(self, path: Path, mode: ResourceMode) -> str | None:
        coordinator = self.coordinator()
        plan = coordinator.plan_place(path, mode)
        if not plan.is_supported:
            self._message_box.warning(
                self._window,
                _ts("Place Image"),
                _ts("{name} is not a supported image format.").format(name=path.name),
            )
            return None
        if not plan.is_safe:
            self._message_box.warning(
                self._window,
                _ts("Place Image"),
                _ts("{name} is a symbolic link; place the real file instead.").format(
                    name=path.name
                ),
            )
            return None
        layer_id = coordinator.place(plan)
        status_bar = getattr(self._window, "statusBar", None)
        if layer_id and callable(status_bar):
            verb = _ts("Linked") if mode == "linked" else _ts("Embedded")
            status_bar().showMessage(f"{verb} {path.name}", 3000)
        if layer_id and "locator_not_portable" in plan.warnings and mode == "linked":
            logger.info("Placed linked image outside the document folder (absolute locator)")
        return layer_id

    def _pick_file(self) -> str:
        chosen, _filter = QFileDialog.getOpenFileName(self._window, _ts("Place Image"), "", _FILTER)
        return chosen
