"""Pure CPU helpers for splitting a SAM3 concept mask into per-object components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .runtime_imports import load_cv2, load_numpy


@dataclass(frozen=True, slots=True)
class SplitComponent:
    """One connected-component slice of a source mask, cropped to its bbox."""

    mask_u8: Any
    """2D uint8 ndarray (0/255), cropped to ``bbox`` and containing only this component."""

    bbox: tuple[int, int, int, int]
    """``(x, y, w, h)`` in the source mask's coordinate space."""

    area_px: int


def split_mask_components(
    mask_u8: Any,
    *,
    min_area_px: int,
    connectivity: int = 8,
) -> list[SplitComponent]:
    """Split a binary mask into per-object connected components.

    Components are returned sorted by descending area; components smaller
    than ``min_area_px`` are dropped. Callers should treat an empty result as
    "nothing usable" and fall back to the original, unsplit mask.
    """

    np, cv2 = _load_cv2_runtime()
    binary = np.where(np.asarray(mask_u8) > 0, 255, 0).astype(np.uint8)
    component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        binary,
        connectivity=connectivity,
    )
    components: list[SplitComponent] = []
    for label in range(1, component_count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area_px:
            continue
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        cropped_labels = labels[y : y + h, x : x + w]
        component_mask = np.where(cropped_labels == label, 255, 0).astype(np.uint8)
        components.append(SplitComponent(mask_u8=component_mask, bbox=(x, y, w, h), area_px=area))
    components.sort(key=lambda component: component.area_px, reverse=True)
    return components


def _load_cv2_runtime() -> tuple[Any, Any]:
    return load_numpy(), load_cv2()
