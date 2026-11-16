"""Blend a live filter's output with its input before mask and opacity."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from .canvas_blend_service import CanvasBlendService
from .commands import BlendMode
from .mask_coverage import mask_coverage_alpha8


def filter_has_no_output(filter_type: str, params: dict[str, Any]) -> bool:
    """Match built-in parameter values with no visible filter output."""
    if filter_type == "gaussian_blur":
        return float(params.get("radius", 5.0)) < 1.0
    if filter_type == "sharpen":
        return float(params.get("amount", 50.0)) <= 0.0 or float(params.get("radius", 1.0)) <= 0.0
    if filter_type == "noise_reduction":
        return float(params.get("strength", 10.0)) <= 0.0
    if filter_type == "mosaic":
        return float(params.get("cell_size", 8.0)) < 2.0
    return False


def blend_filter_layer_result(
    source: QImage,
    filtered: QImage,
    mode: BlendMode,
    mask: QImage | None,
    opacity: float,
) -> QImage:
    """Use the canvas blend equations while retaining the filter's output alpha."""
    if (
        filtered is source
        or filtered.isNull()
        or filtered.size() != source.size()
        or opacity <= 0.0
    ):
        return source
    original = _rgba(source)
    result = _rgba(filtered)
    source_rgb = original[:, :, :3].astype(np.float32)
    filtered_rgb = result[:, :, :3].astype(np.float32)
    if mode == BlendMode.DISSOLVE:
        y, x = np.indices(original.shape[:2], dtype=np.uint64)
        choose_filtered = ((x * 1664525 + y * 1013904223) & 0xFFFFFFFF) < 0x80000000
        blended_rgb = np.where(choose_filtered[:, :, None], filtered_rgb, source_rgb)
    else:
        blended_rgb = CanvasBlendService.blended_rgb_straight(filtered_rgb, source_rgb, mode)
        if blended_rgb is None:
            return filtered

    amount: Any = min(1.0, max(0.0, float(opacity)))
    if mask is not None and not mask.isNull():
        coverage = mask_coverage_alpha8(mask)
        if coverage.size() != source.size():
            coverage = coverage.scaled(
                source.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        rows = np.frombuffer(
            coverage.constBits(), dtype=np.uint8, count=coverage.bytesPerLine() * source.height()
        ).reshape(source.height(), coverage.bytesPerLine())[:, : source.width()]
        amount = amount * (rows[:, :, None].astype(np.float32) / 255.0)

    output = original.copy().astype(np.float32)
    output[:, :, :3] += (blended_rgb - source_rgb) * amount
    output[:, :, 3:4] += (result[:, :, 3:4].astype(np.float32) - output[:, :, 3:4]) * amount
    pixels = np.clip(output + 0.5, 0, 255).astype(np.uint8)
    image = QImage(
        pixels.data,
        source.width(),
        source.height(),
        source.width() * 4,
        QImage.Format.Format_RGBA8888,
    )
    return image.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _rgba(image: QImage) -> np.ndarray:
    straight = image.convertToFormat(QImage.Format.Format_RGBA8888)
    height, width = straight.height(), straight.width()
    return (
        np.frombuffer(straight.constBits(), dtype=np.uint8, count=straight.bytesPerLine() * height)
        .reshape(height, straight.bytesPerLine())[:, : width * 4]
        .reshape(height, width, 4)
        .copy()
    )
