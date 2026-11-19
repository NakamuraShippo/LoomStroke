"""Selection history helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple, cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .command_types import SelectionRect

HistoryPatchFactory = Callable[
    [str, QRect, QImage, QImage, dict[str, Any] | None],
    Any,
]


class _SelectionExtent(NamedTuple):
    """Selection bounds, including any mask pixels outside the page patch."""

    bounds: SelectionRect | None
    full_bounds: SelectionRect | None
    use_page_mask: bool
    full_mask: NDArray[Any] | None

    @classmethod
    def capture(
        cls,
        canvas: Any,
        bounds: SelectionRect | None,
        full_bounds: SelectionRect | None,
        full_mask: NDArray[Any] | None,
        page_mask: QImage,
    ) -> _SelectionExtent:
        if full_mask is None:
            return cls(bounds, full_bounds, False, None)
        if full_bounds and page_mask.rect().contains(QRect(*full_bounds)):
            visible = canvas._selection_mask_to_numpy(page_mask.copy(*full_bounds))
            if np.array_equal(visible, full_mask):
                return cls(bounds, full_bounds, True, None)
        return cls(bounds, full_bounds, False, full_mask.copy())

    def restore(self, canvas: Any, page_mask: QImage) -> None:
        mask = self.full_mask
        if self.use_page_mask and self.full_bounds is not None:
            mask = canvas._selection_mask_to_numpy(page_mask.copy(*self.full_bounds))
        elif mask is not None:
            mask = mask.copy()
        canvas.set_selection_mask(page_mask if self.bounds is not None else None, self.bounds)
        canvas._selection_bounds_full = self.full_bounds
        canvas._selection_mask_full = mask


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

    def record_snapshot_history(
        self, before: Any | None, after: Any | None, *, expanded_command: Any = None
    ) -> None:
        if before is None or after is None:
            return

        before_img = self.selection_snapshot_image(getattr(before, "mask", None))
        after_img = self.selection_snapshot_image(getattr(after, "mask", None))
        rect = QRect(0, 0, self._owner._width, self._owner._height)
        metadata: dict[str, Any] = {"command": "selection"}
        if expanded_command is not None:
            metadata["before_extent"] = _SelectionExtent.capture(
                self._owner,
                before.bounds,
                expanded_command._before_full_bounds,
                expanded_command._before_full_mask,
                before_img,
            )
            metadata["after_extent"] = _SelectionExtent.capture(
                self._owner,
                after.bounds,
                self._owner._selection_bounds_full,
                self._owner._selection_mask_full,
                after_img,
            )
        patch = self._patch_factory(
            self._owner._selection_id, rect, before_img, after_img, metadata
        )
        self._owner._record_history_patch("Selection", patch)

    def record_content_command_history(self, cmd: Any, *, label: str) -> None:
        prepared_patch = getattr(cmd, "_prepared_history_patch", None)
        if prepared_patch is not None:
            self._owner._record_history_patch(label, prepared_patch)
            layer = getattr(cmd, "_layer_ref", None)
            if self._content_layer_type is not None and isinstance(layer, self._content_layer_type):
                layer.texture_version += 1
                self._owner._emit_layer_content_changed(
                    layer, prepared_patch.rect, prepared_patch.metadata
                )
            return
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
            expansion = getattr(cmd, "_backing_expansion", None)
            if expansion is not None:
                patch.backing_expansion = expansion
            self._owner._record_history_patch(label, patch)
            if self._content_layer_type is not None and isinstance(
                layer,
                self._content_layer_type,
            ):
                layer.texture_version += 1
                self._owner._emit_layer_content_changed(layer, rect, metadata)
        before_selection = getattr(cmd, "_before_selection", None)
        after_selection = getattr(cmd, "_after_selection", None)
        if (
            getattr(cmd, "clear_selection_after", False)
            and getattr(before_selection, "mask", None) is None
            and getattr(after_selection, "mask", None) is None
        ):
            return
        self.record_snapshot_history(
            before_selection,
            after_selection,
            expanded_command=cmd if getattr(cmd, "_backing_expansion", None) is not None else None,
        )

    def selection_snapshot_image(self, mask: Any | None) -> QImage:
        return cast(QImage, self._owner._selection_state().selection_snapshot_image(mask))

    def empty_selection_mask(self) -> QImage:
        return cast(QImage, self._owner._selection_state().empty_selection_mask())

    def fit_selection_mask(self, mask: QImage) -> QImage:
        return cast(QImage, self._owner._selection_state().fit_selection_mask(mask))

    def numpy_to_selection_mask(self, mask: NDArray[Any]) -> QImage:
        return cast(QImage, self._owner._selection_state().numpy_to_selection_mask(mask))
