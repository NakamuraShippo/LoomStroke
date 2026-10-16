"""Bounded, image-local semantic candidates for a text-free single click.

The same SAM 3.1 detector checks a small vocabulary. This is closed-vocabulary
discovery, not a general-purpose image captioner. Packed masks avoid keeping
dozens of full-resolution boolean arrays resident between clicks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

CONCEPTS = (
    "glasses",
    "hair",
    "face",
    "shirt",
    "jacket",
    "dress",
    "hat",
    "hand",
    "shoe",
    "bag",
    "person",
    "animal",
)
MIN_SCORE = 0.65
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_PER_CONCEPT = 8


@dataclass(frozen=True)
class Concept:
    label: str
    score: float
    area: int
    shape: tuple[int, int]
    packed: np.ndarray

    def contains(self, x: float, y: float) -> bool:
        height, width = self.shape
        index = min(height - 1, int(y * height)) * width + min(width - 1, int(x * width))
        return bool(self.packed[index // 8] & (1 << (7 - index % 8)))

    def mask(self) -> np.ndarray:
        return (
            np.unpackbits(self.packed, count=self.shape[0] * self.shape[1])
            .reshape(self.shape)
            .astype(bool)
        )


def discover(runtime: Any, session_id: str, add_prompt: Callable[..., Any]) -> list[Concept]:
    """Cache only a completed scan; reset tracker state again before point inference."""
    if runtime.image_concept_cache is not None:
        return runtime.image_concept_cache
    concepts: list[Concept] = []
    used_bytes = 0
    predictor = runtime.require_predictor()
    for label in CONCEPTS:
        predictor.handle_request({"type": "reset_session", "session_id": session_id})
        outputs = add_prompt(runtime, session_id, text=label, output_prob_thresh=MIN_SCORE)
        masks = np.asarray(outputs["out_binary_masks"], dtype=bool)
        scores = np.asarray(outputs["out_probs"])
        for index in np.argsort(-scores)[:MAX_PER_CONCEPT]:
            score = float(scores[index])
            mask = masks[index]
            area = int(mask.sum())
            if not np.isfinite(score) or score < MIN_SCORE or not area:
                continue
            packed_size = (mask.size + 7) // 8
            if used_bytes + packed_size > MAX_CACHE_BYTES:
                continue
            concepts.append(Concept(label, score, area, mask.shape, np.packbits(mask)))
            used_bytes += packed_size
    runtime.image_concept_cache = concepts
    return concepts


def prepend_candidates(
    outputs: dict[str, Any],
    concepts: list[Concept],
    point: tuple[float, float, int],
    object_id: int,
) -> dict[str, Any]:
    """Offer small semantic parts first, whole subjects next, and retain native scopes."""
    matches = sorted(
        (concept for concept in concepts if concept.contains(point[0], point[1])),
        key=lambda concept: (concept.label in ("person", "animal"), concept.area, -concept.score),
    )
    native_masks = np.asarray(outputs["out_binary_masks"], dtype=bool)
    masks: list[np.ndarray] = []
    labels: list[str] = []
    scores: list[float] = []
    for concept in matches:
        if len(masks) == 3:
            break
        mask = concept.mask()
        if native_masks.size and mask.shape != native_masks[0].shape:
            continue
        if any(
            np.count_nonzero(mask & other) / max(1, np.count_nonzero(mask | other)) >= 0.98
            for other in masks
        ):
            continue
        masks.append(mask)
        labels.append(concept.label)
        scores.append(concept.score)
    if not masks:
        return outputs
    semantic_count = len(masks)
    # Keep original scopes even if they closely resemble the inferred object.
    masks.extend(native_masks)
    labels.extend([""] * len(native_masks))
    scores.extend(np.asarray(outputs["out_probs"]).tolist())
    boxes = []
    for mask in masks:
        ys, xs = np.nonzero(mask)
        height, width = mask.shape
        boxes.append(
            [
                xs.min() / width,
                ys.min() / height,
                (xs.max() - xs.min() + 1) / width,
                (ys.max() - ys.min() + 1) / height,
            ]
            if len(xs)
            else [0, 0, 0, 0]
        )
    return {
        **outputs,
        "out_binary_masks": np.asarray(masks),
        "out_obj_ids": np.full(len(masks), object_id),
        "out_probs": np.asarray(scores),
        "out_boxes_xywh": np.asarray(boxes),
        "out_semantic_labels": labels,
        "out_point_variants": ["auto_concept"] * semantic_count
        + list(outputs.get("out_point_variants", ["model_preferred"] * len(native_masks))),
    }
