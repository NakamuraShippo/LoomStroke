from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, TypeAlias

import numpy as np
from numpy.typing import NDArray

from .commands import SELECTION_MASK_LAYER_ID, BlendMode, Command
from .layer import Layer

Float32Array: TypeAlias = NDArray[np.float32]
Uint8Array: TypeAlias = NDArray[np.uint8]


@dataclass(slots=True)
class _LegacyHistoryPatch:
    layer_id: str
    position: tuple[int, int]
    before: Uint8Array
    after: Uint8Array


class _LegacyHistory:
    __slots__ = ("_limit", "_redo_stack", "_undo_stack")

    def __init__(self, *, limit: int = 50) -> None:
        self._undo_stack: list[_LegacyHistoryPatch] = []
        self._redo_stack: list[_LegacyHistoryPatch] = []
        self._limit = max(1, int(limit))

    def record(self, patch: _LegacyHistoryPatch) -> None:
        self._undo_stack.append(patch)
        self._redo_stack.clear()
        if len(self._undo_stack) > self._limit:
            self._undo_stack.pop(0)

    def pop_undo(self) -> _LegacyHistoryPatch | None:
        if not self._undo_stack:
            return None
        return self._undo_stack.pop()

    def pop_redo(self) -> _LegacyHistoryPatch | None:
        if not self._redo_stack:
            return None
        return self._redo_stack.pop()

    def push_redo(self, patch: _LegacyHistoryPatch) -> None:
        self._redo_stack.append(patch)

    def push_undo(self, patch: _LegacyHistoryPatch) -> None:
        self._undo_stack.append(patch)

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo_count(self) -> int:
        return len(self._undo_stack)

    def redo_count(self) -> int:
        return len(self._redo_stack)

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()


_COMMAND_TYPES: tuple[type[Command], ...] = (Command,)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CanvasConfig:
    """Lightweight configuration for the legacy canvas facade."""

    width: int = 1920
    height: int = 1080
    background_color: tuple[int, int, int, int] = (0, 0, 0, 0)
    max_layers: int | None = None
    use_memmap_threshold: int = 10_000_000
    history_limit: int = 50


@dataclass(frozen=True)
class CanvasDocument:
    """Minimal document snapshot used by legacy tests."""

    width: int
    height: int
    layers: tuple[tuple[str, str, bool, float], ...]


class Canvas:
    """Compatibility canvas implementing the legacy API surface."""

    def __init__(
        self,
        config: CanvasConfig | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
        background_color: tuple[int, int, int, int] | None = None,
    ) -> None:
        if config is None:
            config = CanvasConfig()
        if width is not None or height is not None or background_color is not None:
            config = CanvasConfig(
                width=width if width is not None else config.width,
                height=height if height is not None else config.height,
                background_color=(
                    background_color if background_color is not None else config.background_color
                ),
                max_layers=config.max_layers,
                use_memmap_threshold=config.use_memmap_threshold,
                history_limit=config.history_limit,
            )
        self.config = config
        self.layers: list[Layer] = []
        self._active_index = -1
        self._transaction_depth = 0
        self._dirty = False
        self._composite_cache: Uint8Array | None = None
        self._document_id = f"doc-{uuid.uuid4().hex[:8]}"
        self._history = _LegacyHistory(limit=config.history_limit)
        self._selection_mask: Uint8Array | None = None
        self._selection_bounds: tuple[int, int, int, int] | None = None
        self._selection_id = SELECTION_MASK_LAYER_ID
        self._selection_mask_full: Uint8Array | None = None

        # Initialize with a background and drawing layer to match legacy expectations.
        self.add_layer("Background", make_active=False, fill_color=config.background_color)
        self.add_layer("Layer 1", make_active=True)

    @property
    def active_layer(self) -> Layer | None:
        if 0 <= self._active_index < len(self.layers):
            return self.layers[self._active_index]
        return None

    def get_layers(self) -> list[Layer]:
        return list(self.layers)

    def get_selection(self) -> Any:
        from ..host_api import Selection  # noqa: PLC0415

        if self._selection_mask is None or self._selection_bounds is None:
            return None
        x, y, w, h = self._selection_bounds
        if w <= 0 or h <= 0:
            return None
        return Selection(mask_layer_id=self._selection_id, bounds=(x, y, w, h))

    def get_selection_mask(self) -> Uint8Array | None:
        if self._selection_mask is None:
            return None
        return self._selection_mask.copy()

    def get_selection_bounds(self) -> tuple[int, int, int, int] | None:
        if self._selection_bounds is None:
            return None
        x, y, width, height = self._selection_bounds
        return (x, y, width, height)

    def set_selection_mask(
        self,
        mask: Uint8Array | None,
        bounds: tuple[int, int, int, int] | None,
    ) -> None:
        if mask is None:
            self._selection_mask = None
            self._selection_bounds = None
            self._selection_mask_full = None
            return
        arr = np.asarray(mask, dtype=np.uint8)
        if arr.shape != (self.config.height, self.config.width):
            arr = np.resize(arr, (self.config.height, self.config.width)).astype(
                np.uint8, copy=False
            )
        self._selection_mask = arr.copy()
        if bounds is None:
            computed = self._selection_bounds_from_mask(self._selection_mask)
            if computed is None:
                self._selection_mask = None
                self._selection_bounds = None
                return
            self._selection_bounds = computed
        else:
            x, y, width, height = bounds
            self._selection_bounds = (x, y, width, height)

    def _selection_bounds_from_mask(
        self, mask: Uint8Array | None
    ) -> tuple[int, int, int, int] | None:
        if mask is None:
            return None
        ys, xs = np.where(mask > 0)
        if xs.size == 0 or ys.size == 0:
            return None
        min_x = int(xs.min())
        max_x = int(xs.max())
        min_y = int(ys.min())
        max_y = int(ys.max())
        return (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)

    def _record_selection_patch(self, before: Uint8Array, after: Uint8Array) -> None:
        self._history.record(
            _LegacyHistoryPatch(
                layer_id=self._selection_id,
                position=(0, 0),
                before=before.copy(),
                after=after.copy(),
            )
        )

    def add_layer(
        self,
        name: str,
        *,
        make_active: bool = True,
        fill_color: tuple[int, int, int, int] | None = None,
    ) -> Layer:
        layer = Layer(
            name=name,
            width=self.config.width,
            height=self.config.height,
            fill_color=fill_color,
        )
        self.layers.append(layer)
        if make_active or self._active_index == -1:
            self._active_index = len(self.layers) - 1
        self._invalidate_composite()
        return layer

    def remove_layer(self, layer_id: str) -> bool:
        for index, layer in enumerate(self.layers):
            if layer.id == layer_id:
                del self.layers[index]
                if self._active_index >= len(self.layers):
                    self._active_index = len(self.layers) - 1
                self._invalidate_composite()
                return True
        return False

    def set_active_layer(self, layer_id: str) -> None:
        for index, layer in enumerate(self.layers):
            if layer.id == layer_id:
                self._active_index = index
                return
        logger.debug("Layer %s not found when attempting to activate", layer_id)

    def set_layer_locked(self, layer_id: str, locked: bool) -> None:
        layer = self.get_layer_by_id(layer_id)
        if layer is None:
            logger.debug("Layer %s not found when attempting to lock", layer_id)
            return
        layer.locked = locked

    def set_layer_blend_mode(self, layer_id: str, mode: str | BlendMode | None) -> None:
        layer = self.get_layer_by_id(layer_id)
        if layer is None:
            logger.debug("Layer %s not found when attempting to change blend mode", layer_id)
            return
        canonical = self._canonical_blend_mode(mode)
        layer.blend_mode = canonical
        self._invalidate_composite()

    def get_layer_by_id(self, layer_id: str) -> Layer | None:
        for layer in self.layers:
            if layer.id == layer_id:
                return layer
        return None

    def submit(self, cmd: Command, *, merge_key: str | None = None) -> bool:
        if not isinstance(cmd, _COMMAND_TYPES) and not callable(getattr(cmd, "apply", None)):
            raise TypeError("Canvas.submit expects a Command instance")
        if merge_key is not None and hasattr(cmd, "merge_key"):
            cmd.merge_key = merge_key
        apply_method = cmd.apply
        if not callable(apply_method):
            raise TypeError("Canvas.submit expects a Command instance")
        if not apply_method(self):
            return False
        self._dirty = True
        self._invalidate_composite()
        return True

    def begin_transaction(self, label: str | None = None) -> None:
        self._transaction_depth += 1
        logger.debug("Transaction started: %s (depth=%s)", label, self._transaction_depth)

    def end_transaction(self) -> None:
        if self._transaction_depth == 0:
            return
        self._transaction_depth -= 1
        logger.debug("Transaction finished (depth=%s)", self._transaction_depth)
        if self._transaction_depth == 0:
            self._invalidate_composite()

    def is_busy(self) -> bool:
        return self._transaction_depth > 0

    def clear_dirty(self) -> None:
        self._dirty = False
        for layer in self.layers:
            layer.clear_dirty()

    def is_dirty(self) -> bool:
        if self._dirty:
            return True
        return any(layer.dirty_region is not None for layer in self.layers)

    def size(self) -> tuple[int, int]:
        return self.config.width, self.config.height

    def get_document(self) -> CanvasDocument:
        layers = tuple(
            (layer.id, layer.name, layer.visible, layer.opacity) for layer in self.layers
        )
        return CanvasDocument(
            width=self.config.width,
            height=self.config.height,
            layers=layers,
        )

    def get_composite_image(self) -> Uint8Array:
        if self._composite_cache is None:
            self._composite_cache = self._compose_layers()
        return self._composite_cache.copy()

    def load_image(self, image: NDArray[Any]) -> None:
        layer = self.active_layer
        if layer is None:
            raise ValueError("No active layer available")
        if layer.locked:
            logger.debug("Layer %s is locked; load_image ignored", layer.id)
            return
        self._assign_image_to_layer(layer, image)
        self._dirty = True
        self._invalidate_composite()

    def apply_image(
        self,
        image: NDArray[Any],
        *,
        position: tuple[int, int] = (0, 0),
        blend_mode: str = "normal",
        opacity: float = 1.0,
        metadata: dict[str, object] | None = None,
    ) -> None:
        del blend_mode, metadata  # Not used in compatibility layer
        layer = self.active_layer
        if layer is None:
            raise ValueError("No active layer available")
        if layer.locked:
            logger.debug("Layer %s is locked; apply_image ignored", layer.id)
            return
        y, x = position[1], position[0]
        patch = self._prepare_image(image, opacity=opacity)
        h, w = patch.shape[:2]
        y1 = max(0, y)
        x1 = max(0, x)
        y2 = min(self.config.height, y + h)
        x2 = min(self.config.width, x + w)
        if y1 >= y2 or x1 >= x2:
            return
        selection_mask = None
        selection_bounds = None
        if isinstance(self._selection_mask, np.ndarray) and isinstance(
            self._selection_bounds, tuple
        ):
            selection_mask = self._selection_mask
            selection_bounds = self._selection_bounds
        if selection_mask is not None and selection_bounds is not None:
            sel_x, sel_y, sel_w, sel_h = selection_bounds
            sel_x1 = max(0, sel_x)
            sel_y1 = max(0, sel_y)
            sel_x2 = min(self.config.width, sel_x + sel_w)
            sel_y2 = min(self.config.height, sel_y + sel_h)
            x1 = max(x1, sel_x1)
            y1 = max(y1, sel_y1)
            x2 = min(x2, sel_x2)
            y2 = min(y2, sel_y2)
            if y1 >= y2 or x1 >= x2:
                return
        src_y1 = y1 - y
        src_y2 = src_y1 + (y2 - y1)
        src_x1 = x1 - x
        src_x2 = src_x1 + (x2 - x1)
        dest = layer.buffer[y1:y2, x1:x2]
        before = np.clip(dest, 0.0, 255.0).astype(np.uint8)
        src = patch[src_y1:src_y2, src_x1:src_x2]
        if selection_mask is not None:
            mask_patch = selection_mask[y1:y2, x1:x2].astype(np.float32)
            if np.all(mask_patch <= 0):
                return
            src = src.copy()
            src[:, :, 3] *= mask_patch / 255.0
        self._alpha_blend(dest, src)
        layer.mark_dirty(x1, y1, x2 - x1, y2 - y1)
        self._dirty = True
        self._invalidate_composite()
        after = np.clip(dest, 0.0, 255.0).astype(np.uint8)
        self._history.record(
            _LegacyHistoryPatch(
                layer_id=layer.id,
                position=(x1, y1),
                before=before,
                after=after,
            )
        )

    def undo_image(self) -> bool:
        patch = self._history.pop_undo()
        if patch is None:
            return False
        if not self._apply_history_patch(patch, use_after=False):
            return False
        self._history.push_redo(patch)
        return True

    # --- History-aware helpers --------------------------------------------

    def history_can_undo(self) -> bool:
        return self._history.can_undo()

    def history_can_redo(self) -> bool:
        return self._history.can_redo()

    def history_undo(self) -> bool:
        return self.undo_image()

    def history_redo(self) -> bool:
        patch = self._history.pop_redo()
        if patch is None:
            return False
        if not self._apply_history_patch(patch, use_after=True):
            return False

        self._history.push_undo(patch)
        return True

    def history_clear(self) -> None:
        self._history.clear()

    def history_undo_count(self) -> int:
        return self._history.undo_count()

    def history_redo_count(self) -> int:
        return self._history.redo_count()

    def _apply_history_patch(
        self,
        patch: _LegacyHistoryPatch,
        *,
        use_after: bool,
    ) -> bool:
        if patch.layer_id == self._selection_id:
            data = patch.after if use_after else patch.before
            mask = np.asarray(data, dtype=np.uint8)
            if mask.ndim != 2:
                return False
            if not np.any(mask):
                self._selection_mask = None
                self._selection_bounds = None
            else:
                self._selection_mask = mask.copy()
                self._selection_bounds = self._selection_bounds_from_mask(self._selection_mask)
            return True
        layer = self.get_layer_by_id(patch.layer_id)
        if layer is None:
            return False
        data = (patch.after if use_after else patch.before).astype(np.float32)
        x, y = patch.position
        height, width = data.shape[:2]
        if height <= 0 or width <= 0:
            return False
        x1 = max(0, min(self.config.width, int(x)))
        y1 = max(0, min(self.config.height, int(y)))
        x2 = min(self.config.width, x1 + width)
        y2 = min(self.config.height, y1 + height)
        if x1 >= x2 or y1 >= y2:
            return False
        dest = layer.buffer[y1:y2, x1:x2]
        src = data[: y2 - y1, : x2 - x1]
        dest[: src.shape[0], : src.shape[1]] = src
        layer.mark_dirty(x1, y1, src.shape[1], src.shape[0])
        self._dirty = True
        self._invalidate_composite()
        return True

    def _assign_image_to_layer(self, layer: Layer, image: NDArray[Any]) -> None:
        prepared = self._prepare_image(image)
        if prepared.shape != layer.buffer.shape:
            raise ValueError("Image dimensions do not match layer dimensions")
        layer.buffer[:] = prepared
        layer.mark_dirty(0, 0, layer.width, layer.height)

    def _prepare_image(self, image: NDArray[Any], *, opacity: float = 1.0) -> Float32Array:
        if image.ndim != 3 or image.shape[2] not in (3, 4):
            raise ValueError("Expected image with 3 or 4 channels")
        arr: Float32Array = image.astype(np.float32).copy()
        if arr.shape[2] == 3:
            alpha = np.full((*arr.shape[:2], 1), 255.0, dtype=np.float32)
            arr = np.concatenate([arr, alpha], axis=2)
        arr[:, :, 3] = np.clip(arr[:, :, 3] * opacity, 0.0, 255.0)
        return arr

    def _compose_layers(self) -> Uint8Array:
        height = self.config.height
        width = self.config.width

        accum_rgb: Float32Array = np.zeros((height, width, 3), dtype=np.float32)
        accum_alpha: Float32Array = np.zeros((height, width), dtype=np.float32)

        for layer in self.layers:
            if not layer.visible or layer.opacity <= 0.0:
                continue
            buffer = layer.buffer
            if buffer.shape != (height, width, 4):
                continue

            mode = self._canonical_blend_mode(getattr(layer, "blend_mode", "normal"))
            src = buffer.astype(np.float32) / 255.0
            src_rgb = src[:, :, :3]
            src_alpha = np.clip(src[:, :, 3], 0.0, 1.0) * float(layer.opacity)
            if np.all(src_alpha <= 0):
                continue

            dst_rgb = np.divide(
                accum_rgb,
                np.maximum(accum_alpha[..., None], 1e-6),
                out=np.zeros_like(accum_rgb),
            )

            blend_rgb = self._blend_rgb(mode, src_rgb, dst_rgb)
            blend_rgb = np.clip(blend_rgb, 0.0, 1.0)

            accum_rgb = (
                blend_rgb * src_alpha[..., None] + accum_rgb * (1.0 - src_alpha[..., None])
            ).astype(np.float32, copy=False)
            accum_alpha = (src_alpha + accum_alpha * (1.0 - src_alpha)).astype(
                np.float32, copy=False
            )

        output: Uint8Array = np.zeros((height, width, 4), dtype=np.uint8)
        alpha = np.clip(accum_alpha, 0.0, 1.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            rgb = np.where(
                alpha[..., None] > 0,
                accum_rgb / np.clip(alpha[..., None], 1e-6, 1.0),
                0.0,
            )
        output[:, :, :3] = np.clip(rgb * 255.0, 0.0, 255.0).astype(np.uint8, copy=False)
        output[:, :, 3] = np.clip(alpha * 255.0, 0.0, 255.0).astype(np.uint8, copy=False)
        return output

    def _canonical_blend_mode(self, mode: str | BlendMode | None) -> str:
        if mode is None:
            return "normal"
        if isinstance(mode, BlendMode):
            return str(mode.value).replace("_", " ")
        value = str(mode).strip().lower().replace("_", " ")
        return value or "normal"

    def _blend_rgb(  # noqa: PLR0912
        self,
        mode: str,
        src: Float32Array,
        dst: Float32Array,
    ) -> Float32Array:
        mode = mode.lower()
        src = np.clip(src, 0.0, 1.0).astype(np.float32, copy=False)
        dst = np.clip(dst, 0.0, 1.0).astype(np.float32, copy=False)

        if mode == "normal":
            result = src
        elif mode == "multiply":
            result = src * dst
        elif mode == "screen":
            result = np.asarray(1.0 - (1.0 - src) * (1.0 - dst), dtype=np.float32)
        elif mode == "overlay":
            mask = dst <= 0.5
            result = np.empty_like(src)
            result[mask] = 2.0 * src[mask] * dst[mask]
            result[~mask] = 1.0 - 2.0 * (1.0 - src[~mask]) * (1.0 - dst[~mask])
        elif mode == "hard light":
            mask = src <= 0.5
            result = np.empty_like(src)
            result[mask] = 2.0 * src[mask] * dst[mask]
            result[~mask] = 1.0 - 2.0 * (1.0 - src[~mask]) * (1.0 - dst[~mask])
        elif mode == "soft light":
            result = np.empty_like(src)
            mask = src <= 0.5
            result[mask] = dst[mask] - (1.0 - 2.0 * src[mask]) * dst[mask] * (1.0 - dst[mask])
            result[~mask] = dst[~mask] + (2.0 * src[~mask] - 1.0) * (
                np.sqrt(dst[~mask]) - dst[~mask]
            )
        elif mode == "color dodge":
            result = np.empty_like(src)
            denominator = 1.0 - src
            safe = denominator > 1e-5
            result[safe] = np.minimum(1.0, dst[safe] / denominator[safe])
            result[~safe] = 1.0
        elif mode == "color burn":
            result = np.empty_like(src)
            safe = src > 1e-5
            result[safe] = 1.0 - np.minimum(1.0, (1.0 - dst[safe]) / src[safe])
            result[~safe] = 0.0
        elif mode == "darken":
            result = np.minimum(src, dst)
        elif mode == "lighten":
            result = np.maximum(src, dst)
        elif mode == "difference":
            result = np.abs(dst - src)
        elif mode == "exclusion":
            result = src + dst - 2.0 * src * dst
        else:
            result = src

        return np.asarray(result, dtype=np.float32)

    def _alpha_blend(self, dest: Float32Array, src: Float32Array) -> None:
        dest_alpha = dest[:, :, 3] / 255.0
        src_alpha = src[:, :, 3] / 255.0
        out_alpha = src_alpha + dest_alpha * (1.0 - src_alpha)
        dest_rgb = dest[:, :, :3] / 255.0
        src_rgb = src[:, :, :3] / 255.0
        with np.errstate(divide="ignore", invalid="ignore"):
            out_rgb = np.where(
                out_alpha[..., None] > 0,
                (
                    src_rgb * src_alpha[..., None]
                    + dest_rgb * dest_alpha[..., None] * (1.0 - src_alpha[..., None])
                ),
                dest_rgb,
            )
        dest[:, :, :3] = np.clip(out_rgb * 255.0, 0.0, 255.0)
        dest[:, :, 3] = np.clip(out_alpha * 255.0, 0.0, 255.0)

    def _invalidate_composite(self) -> None:
        self._composite_cache = None
