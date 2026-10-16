"""QImage transform helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QTransform


class CanvasTransformImageService:
    """Create transformed QImages without mutating canvas owner state."""

    def transformed_image(
        self,
        image: QImage,
        transform_type: str,
        params: Mapping[str, Any],
    ) -> QImage | None:
        if transform_type == "rotate":
            angle_rad = params.get("angle", 0.0)
            angle_deg = math.degrees(angle_rad)
            w, h = image.width(), image.height()
            cx, cy = w / 2.0, h / 2.0
            transform = QTransform()
            transform.translate(cx, cy)
            transform.rotate(angle_deg)
            transform.translate(-cx, -cy)
            return image.transformed(
                transform,
                Qt.TransformationMode.SmoothTransformation,
            )

        if transform_type == "flip":
            h_flip = params.get("horizontal", False)
            v_flip = params.get("vertical", False)
            return image.mirrored(h_flip, v_flip)

        if transform_type == "scale":
            sx = params.get("sx", 1.0)
            sy = params.get("sy", 1.0)
            new_w = max(1, round(image.width() * sx))
            new_h = max(1, round(image.height() * sy))
            return image.scaled(
                new_w,
                new_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        return None
