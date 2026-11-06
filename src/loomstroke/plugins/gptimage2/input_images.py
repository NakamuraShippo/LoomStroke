"""Validate image-edit inputs before capturing layers or reading credentials."""

from __future__ import annotations

import re
from collections.abc import Sequence
from io import BytesIO
from typing import Any

MAX_INPUT_LAYERS = 16
MAX_INPUT_IMAGE_BYTES = 50 * 1024 * 1024


def normalize_input_alpha_color(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
        raise ValueError("Input alpha fill color must be #RRGGBB")
    return value.lower()


def fill_input_alpha(image_bytes: bytes, color: str) -> bytes:
    """Composite an upload copy over an opaque color, preserving opaque inputs."""
    from PIL import Image  # noqa: PLC0415

    color = normalize_input_alpha_color(color)
    with Image.open(BytesIO(image_bytes)) as image:
        rgba = image.convert("RGBA")
        if rgba.getchannel("A").getextrema() == (255, 255):
            return image_bytes
        background = Image.new("RGBA", rgba.size, color)
        background.alpha_composite(rgba)
        output = BytesIO()
        background.convert("RGB").save(output, format="PNG")
        return output.getvalue()


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
