"""Local SAM3 overlay preview PNG composition helpers."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from .assets import Sam3AssetStore
    from .data_model import MaskCandidate, MaskCandidateSet, OverlayOptions

_INSTANCE_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 200, 255),
    (255, 92, 140),
    (78, 230, 128),
    (255, 202, 72),
    (170, 118, 255),
    (255, 132, 48),
)
_SINGLE_COLOR = (0, 200, 255)
_HEAT_COLORS: tuple[tuple[int, int, int], ...] = (
    (68, 214, 111),
    (245, 212, 72),
    (255, 128, 48),
    (255, 72, 104),
)


def compose_overlay_preview_png(
    asset_store: Sam3AssetStore,
    candidate_set: MaskCandidateSet,
    options: OverlayOptions,
) -> tuple[bytes, int, int, tuple[int, int, int, int], dict[str, Any]]:
    """Return a binary-free metadata tuple plus composed overlay PNG bytes."""

    from PIL import Image  # noqa: PLC0415

    option_payload = options.to_dict()
    opacity = float(option_payload["opacity"])
    color_mode = _normalize_color_mode(option_payload.get("color_mode"))
    candidates = list(candidate_set.candidates)
    if bool(option_payload.get("include_rejected", False)):
        candidates.extend(candidate_set.rejected_candidates)
    if not candidates:
        raise ValueError("SAM3 overlay preview requires at least one candidate.")

    source_path = asset_store.source_snapshot_path(candidate_set.source_ref)
    with Image.open(source_path) as source_image:
        base = source_image.convert("RGBA")

    source_width, source_height = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    used_bboxes: list[tuple[int, int, int, int]] = []
    skipped_count = 0

    for index, candidate in enumerate(candidates):
        placement = _clamped_placement(candidate.bbox, source_width, source_height)
        if placement is None:
            skipped_count += 1
            continue

        left, top, visible_width, visible_height, crop_box, mask_size = placement
        mask_asset = asset_store.load_mask_asset(candidate.mask_asset_id)
        alpha = _load_mask_alpha(Image, asset_store.mask_asset_path(mask_asset), mask_size)
        alpha = alpha.crop(crop_box)
        if alpha.size != (visible_width, visible_height):
            alpha = alpha.resize((visible_width, visible_height), _nearest_resample(Image))
        alpha = _apply_opacity(alpha, opacity)

        colored = Image.new(
            "RGBA",
            (visible_width, visible_height),
            (*_candidate_color(index, candidate, color_mode), 0),
        )
        colored.putalpha(alpha)
        overlay.alpha_composite(colored, (left, top))
        used_bboxes.append((left, top, visible_width, visible_height))

    preview = Image.alpha_composite(base, overlay)
    output = io.BytesIO()
    preview.save(output, format="PNG")

    preview_bbox = _union_bbox(used_bboxes) if used_bboxes else (0, 0, source_width, source_height)
    metadata = {
        "provider": "sam3_mask_provider",
        "source": "sam3_overlay_preview",
        "candidateSetId": candidate_set.set_id,
        "candidateCount": len(candidates),
        "activeCandidateCount": len(candidate_set.candidates),
        "rejectedCandidateCount": len(candidate_set.rejected_candidates),
        "skippedCandidateCount": skipped_count,
        "opacity": opacity,
        "colorMode": color_mode,
        "includeRejected": bool(option_payload.get("include_rejected", False)),
    }
    return output.getvalue(), source_width, source_height, preview_bbox, metadata


def _load_mask_alpha(image_module: Any, path: Path, size: tuple[int, int]) -> Any:
    with image_module.open(path) as mask_image:
        if "A" in mask_image.getbands():
            alpha = mask_image.getchannel("A")
            if alpha.getextrema() == (0, 0):
                alpha = mask_image.convert("L")
        else:
            alpha = mask_image.convert("L")
        if alpha.size != size:
            alpha = alpha.resize(size, _nearest_resample(image_module))
        return alpha.copy()


def _clamped_placement(
    bbox: tuple[int, int, int, int],
    source_width: int,
    source_height: int,
) -> tuple[int, int, int, int, tuple[int, int, int, int], tuple[int, int]] | None:
    x, y, width, height = (int(value) for value in bbox)
    if width <= 0 or height <= 0:
        return None

    left = max(0, x)
    top = max(0, y)
    right = min(int(source_width), x + width)
    bottom = min(int(source_height), y + height)
    if right <= left or bottom <= top:
        return None

    crop_left = left - x
    crop_top = top - y
    crop_right = crop_left + (right - left)
    crop_bottom = crop_top + (bottom - top)
    return (
        left,
        top,
        right - left,
        bottom - top,
        (crop_left, crop_top, crop_right, crop_bottom),
        (width, height),
    )


def _apply_opacity(alpha: Any, opacity: float) -> Any:
    bounded = max(0.0, min(1.0, float(opacity)))
    if bounded >= 1.0:
        return alpha
    if bounded <= 0.0:
        return alpha.point(lambda _value: 0)
    return alpha.point(lambda value: int(max(0, min(255, round(value * bounded)))))


def _candidate_color(index: int, candidate: MaskCandidate, color_mode: str) -> tuple[int, int, int]:
    if color_mode == "single":
        return _SINGLE_COLOR
    if color_mode == "heat":
        score = candidate.score if candidate.score is not None else 0.0
        bucket = int(max(0.0, min(0.999, float(score))) * len(_HEAT_COLORS))
        return _HEAT_COLORS[bucket]
    return _INSTANCE_PALETTE[index % len(_INSTANCE_PALETTE)]


def _normalize_color_mode(value: Any) -> str:
    text = str(value or "instance").strip().lower()
    return text if text in {"instance", "single", "heat"} else "instance"


def _nearest_resample(image_module: Any) -> Any:
    resampling = getattr(image_module, "Resampling", image_module)
    return resampling.NEAREST


def _union_bbox(bboxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    left = min(x for x, _y, _width, _height in bboxes)
    top = min(y for _x, y, _width, _height in bboxes)
    right = max(x + width for x, _y, width, _height in bboxes)
    bottom = max(y + height for _x, y, _width, height in bboxes)
    return left, top, right - left, bottom - top


__all__ = ["compose_overlay_preview_png"]
