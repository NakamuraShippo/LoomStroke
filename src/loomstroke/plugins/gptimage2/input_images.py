"""Validate image-edit inputs before capturing layers or reading credentials."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

MAX_INPUT_LAYERS = 16
MAX_INPUT_IMAGE_BYTES = 50 * 1024 * 1024


def normalize_layer_ids(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("input_layer_ids must be a sequence of layer IDs")
    ordered: list[str] = []
    for item in value:
        if item is None:
            continue
        if not isinstance(item, str):
            raise ValueError("input_layer_ids must contain strings")
        layer_id = item.strip()
        if layer_id and layer_id not in ordered:
            ordered.append(layer_id)
            if len(ordered) > MAX_INPUT_LAYERS:
                raise ValueError(f"At most {MAX_INPUT_LAYERS} input images are supported")
    return ordered


def normalize_input_pngs(value: Any) -> list[bytes]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("input_pngs must be a sequence of image bytes")
    if len(value) > MAX_INPUT_LAYERS:
        raise ValueError(f"At most {MAX_INPUT_LAYERS} input images are supported")
    images: list[bytes] = []
    for item in value:
        if not isinstance(item, (bytes, bytearray, memoryview)) or not item:
            raise ValueError("Each input image must contain image bytes")
        data = bytes(item)
        if len(data) >= MAX_INPUT_IMAGE_BYTES:
            raise ValueError("Each input image must be smaller than 50 MB")
        images.append(data)
    return images
