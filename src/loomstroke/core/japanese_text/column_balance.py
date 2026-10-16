"""Bounded column balancing using the same layout and logical fitting oracle."""

from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import QRectF

from ..vector_text_layout import TextLayoutCalculator as Layout
from .frame_flow import payload_flow


def balanced_layout(payload, text, font, frame, shape, compose):
    options = payload_flow(payload)
    if not options.get("balance") or options["columns"] < 2 or payload.get("linked_story"):
        return None
    local = deepcopy(payload)
    local.setdefault("layout", {})["text_flow"] = {**options, "balance": False}
    vertical = bool(
        Layout.text_style_defaults(payload).get("vertical", payload.get("vertical", False))
    )
    direction = Layout.text_vertical_flow_from_payload(payload)
    best = compose(local, content=text, font=font, rect=frame, shape_path=shape)
    if not _fits(best, frame, text):
        return best
    lower, upper = 0.01, frame.width() if vertical else frame.height()
    for _ in range(14):
        extent = (lower + upper) * 0.5
        region = QRectF(frame)
        if vertical:
            region.setWidth(extent)
            if direction == "left":
                region.moveRight(frame.right())
        else:
            region.setHeight(extent)
        candidate = compose(local, content=text, font=font, rect=region, shape_path=shape)
        if _fits(candidate, region, text):
            best, upper = candidate, extent
        else:
            lower = extent
    return replace(best, frame=QRectF(frame))


def _fits(layout, frame, text):
    complete = max((line.next_start for line in layout.lines), default=0) >= len(text)
    return (
        complete
        and not any(d.startswith("shape_overflow") for d in layout.diagnostics)
        and (
            layout.logical_bounds.isEmpty()
            or frame.adjusted(-0.001, -0.001, 0.001, 0.001).contains(layout.logical_bounds)
        )
    )
