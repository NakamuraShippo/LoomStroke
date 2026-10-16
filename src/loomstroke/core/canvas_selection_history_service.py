"""Selection history helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

HistoryPatchFactory = Callable[
    [str, QRect, QImage, QImage, dict[str, Any] | None],
    Any,
]


class CanvasSelectionHistoryService:
    """Build and record history patches for selection snapshots."""

    def __init__(
        self,
        owner: Any,
        *,
        patch_factory: HistoryPatchFactory,
        content_layer_type: type[Any] | tuple[type[Any], ...] | None = None,
    ) -> None:
        self._owner = owner
        self._patch_factory = patch_factory
        self._content_layer_type = content_layer_type

    def record_selection_history(self, cmd: Any) -> None:
        before = getattr(cmd, "_before", None)
        after = getattr(cmd, "_after", None)
        self.record_snapshot_history(before, after)

    def record_snapshot_history(self, before: Any | None, after: Any | None) -> None:
        if before is None or after is None:
            return

        before_img = self.selection_snapshot_image(getattr(before, "mask", None))
        after_img = self.selection_snapshot_image(getattr(after, "mask", None))
        rect = QRect(0, 0, self._owner._width, self._owner._height)
        patch = self._patch_factory(
            self._owner._selection_id,
            rect,
            before_img,
            after_img,
            {"command": "selection"},
        )
        self._owner._record_history_patch("Selection", patch)

    def record_content_command_history(self, cmd: Any, *, label: str) -> None:
        bounds = getattr(cmd, "_bounds", None)
        before_layer = getattr(cmd, "_before_layer", None)
        after_layer = getattr(cmd, "_after_layer", None)
        layer = getattr(cmd, "_layer_ref", None)
        if (
            isinstance(bounds, tuple)
            and len(bounds) == 4
            and layer is not None
            and hasattr(layer, "id")
            and isinstance(before_layer, QImage)
            and isinstance(after_layer, QImage)
        ):
            x, y, w, h = bounds
            rect = QRect(int(x), int(y), int(w), int(h))
            metadata = {"command": label.lower().replace(" ", "_")}
            patch = self._patch_factory(
                layer.id,
                rect,
                before_layer,
                after_layer,
                metadata,
            )
            self._owner._record_history_patch(label, patch)
            if self._content_layer_type is not None and isinstance(
                layer,
                self._content_layer_type,
            ):
                layer.texture_version += 1
                self._owner._emit_layer_content_changed(layer, rect, metadata)
        self.record_snapshot_history(
            getattr(cmd, "_before_selection", None),
            getattr(cmd, "_after_selection", None),
        )

    def selection_snapshot_image(self, mask: Any | None) -> QImage:
        return cast(QImage, self._owner._selection_state().selection_snapshot_image(mask))

    def empty_selection_mask(self) -> QImage:
        return cast(QImage, self._owner._selection_state().empty_selection_mask())

    def fit_selection_mask(self, mask: QImage) -> QImage:
        return cast(QImage, self._owner._selection_state().fit_selection_mask(mask))

    def numpy_to_selection_mask(self, mask: NDArray[Any]) -> QImage:
        return cast(QImage, self._owner._selection_state().numpy_to_selection_mask(mask))
