"""``Layer > New Screentone Layer...`` action (CT-02 spec "ユーザーフロー").

Builds the carrier raster layer (tone colour wherever the selection covers,
or the whole page after confirmation) and parents a ``manga_screentone``
effect to it through one ``CreateScreentoneCommand``, so a single undo
removes the tone again and redo brings back the same layer ids.  Painting
on the carrier afterwards adds / erases tone without touching the tone
definition.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox

from ..core.filters.screentone import FILTER_TYPE
from ..core.screentone import ScreentoneNormalizer, ScreentoneParams
from ..core.screentone.create_screentone_command import CreateScreentoneCommand

logger = logging.getLogger(__name__)

HISTORY_LABEL = "Create Screentone"
LAYER_NAME = "Screentone"


class MainWindowScreentoneAction:
    """Create a screentone (carrier layer + child effect) from the selection."""

    def __init__(
        self,
        window: Any,
        *,
        message_box: Callable[[], Any] | None = None,
        tone_rgba: tuple[int, int, int, int] = (0, 0, 0, 255),
    ) -> None:
        self._window = window
        self._message_box = message_box or (lambda: QMessageBox)
        self._tone_rgba = tone_rgba

    def run(self) -> bool:
        """Return ``True`` when a screentone layer was created."""
        canvas = self._canvas_service()
        if canvas is None or not callable(getattr(canvas, "submit", None)):
            self._warn("Canvas service is not available.")
            return False
        coverage = self._selection_coverage(canvas)
        if coverage is None and not self._confirm_whole_page():
            return False
        width = int(getattr(canvas, "_width", 0) or 0)
        height = int(getattr(canvas, "_height", 0) or 0)
        if width <= 0 or height <= 0:
            self._warn("Canvas service is not available.")
            return False
        carrier = self.carrier_rgba(width, height, coverage, self._tone_rgba)
        layer_id = self._create(canvas, carrier)
        if layer_id is None:
            self._warn("Failed to create the screentone layer.")
            return False
        self._window.statusBar().showMessage(self._window._ts("Screentone layer created"), 3000)
        return True

    # -- building blocks --------------------------------------------------------

    @staticmethod
    def carrier_rgba(
        width: int,
        height: int,
        coverage: np.ndarray | None,
        tone_rgba: tuple[int, int, int, int],
    ) -> np.ndarray:
        """Straight RGBA carrier: tone colour with alpha = selection coverage (or full)."""
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[..., 0] = int(tone_rgba[0])
        rgba[..., 1] = int(tone_rgba[1])
        rgba[..., 2] = int(tone_rgba[2])
        if coverage is None:
            rgba[..., 3] = int(tone_rgba[3])
        else:
            scale = float(tone_rgba[3]) / 255.0
            rgba[..., 3] = np.round(coverage.astype(np.float32) * scale).astype(np.uint8)
        return rgba

    @staticmethod
    def default_effect_params() -> dict[str, Any]:
        return ScreentoneNormalizer().to_payload(ScreentoneParams())

    def _create(self, canvas: Any, carrier: np.ndarray) -> str | None:
        command = CreateScreentoneCommand(
            name=self._window._ts(LAYER_NAME),
            carrier_rgba=carrier,
            effect_type=FILTER_TYPE,
            effect_params=self.default_effect_params(),
        )
        canvas.begin_transaction(HISTORY_LABEL)
        try:
            if not canvas.submit(command):
                return None
        except Exception:  # pragma: no cover - defensive
            logger.error("Create Screentone failed", exc_info=True)
            return None
        finally:
            canvas.end_transaction()
        return command.layer_id

    # -- helpers ------------------------------------------------------------------

    def _canvas_service(self) -> Any | None:
        host_api = getattr(self._window, "_host_api", None)
        return getattr(host_api, "canvas", None) if host_api else None

    @staticmethod
    def _selection_coverage(canvas: Any) -> np.ndarray | None:
        """Canvas-sized Alpha8 selection coverage as ``(h, w) uint8``, or ``None``."""
        mask = getattr(canvas, "_selection_mask", None)
        if mask is None or not isinstance(mask, QImage) or mask.isNull():
            return None
        width = int(getattr(canvas, "_width", mask.width()) or mask.width())
        height = int(getattr(canvas, "_height", mask.height()) or mask.height())
        alpha = mask
        if alpha.format() != QImage.Format.Format_Alpha8:
            alpha = alpha.convertToFormat(QImage.Format.Format_Alpha8)
        bits = alpha.constBits()
        if hasattr(bits, "setsize"):
            bits.setsize(alpha.bytesPerLine() * alpha.height())
        rows = np.frombuffer(bits, dtype=np.uint8, count=alpha.bytesPerLine() * alpha.height())
        coverage = rows.reshape(alpha.height(), alpha.bytesPerLine())[:, : alpha.width()]
        if coverage.max() == 0:
            return None
        if coverage.shape != (height, width):
            fitted = np.zeros((height, width), dtype=np.uint8)
            h = min(height, coverage.shape[0])
            w = min(width, coverage.shape[1])
            fitted[:h, :w] = coverage[:h, :w]
            return fitted
        return np.ascontiguousarray(coverage)

    def _confirm_whole_page(self) -> bool:
        box = self._message_box()
        answer = box.question(
            self._window,
            self._window._ts("New Screentone Layer"),
            self._window._ts("No selection. Apply the screentone to the whole page?"),
            box.StandardButton.Yes | box.StandardButton.No,
            box.StandardButton.No,
        )
        return answer == box.StandardButton.Yes

    def _warn(self, source_text: str) -> None:
        self._message_box().warning(
            self._window,
            self._window._ts("New Screentone Layer"),
            self._window._ts(source_text),
        )
