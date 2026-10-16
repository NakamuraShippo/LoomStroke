"""Per-frame contour snapshots for linked story composition."""

from copy import deepcopy

from ..vector_text_layout import TextLayoutCalculator as Layout
from .typography_frame import resolve_shape


def frame_shape(payload):
    if not payload.get("shape_frame"):
        return {}
    return {
        "shape_frame": deepcopy(payload["shape_frame"]),
        "padding": list(Layout.text_padding_from_payload(payload)),
    }


def configure_frame(local, entry):
    """Replace source geometry with this member's contour; rect already has padding."""
    local.pop("shape_frame", None)
    layout = local.setdefault("layout", {})
    layout["wrap_mode"] = "shape" if entry.get("shape_frame") else "word"
    for side, value in zip(
        ("top", "right", "bottom", "left"), entry.get("padding", [0, 0, 0, 0]), strict=True
    ):
        layout["padding_" + side] = value
    if entry.get("shape_frame"):
        local["shape_frame"] = deepcopy(entry["shape_frame"])


def member_shape(payload, entry, frame, own):
    local = deepcopy(payload)
    if not own:
        configure_frame(local, entry)
    return resolve_shape(local, frame)
