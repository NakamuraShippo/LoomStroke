"""Fail before legacy canvas transforms can desynchronize managed frame data."""

from loomstroke.core.canvas_meta_transform_registry import CanvasMetaTransformRegistry

from .canvas_transform_plan import LAYOUT_NAMESPACE
from .drawing_state import panel_input_busy
from .membership import PART_KEY
from .models import PanelFrameError


def require_supported_canvas_geometry(canvas, operation):
    if operation == "image resample":
        return  # The prepared metadata is validated before the resample transaction.
    if any(
        (layer.metadata or {}).get(PART_KEY, {}).get("role") == "panel"
        for layer in canvas._flatten_layers()
    ):
        if panel_input_busy(canvas):
            raise PanelFrameError("finish the current edit before transforming comic frames")
        if operation in {
            "flip_h",
            "flip_v",
            "flip_hv",
            "rot90",
            "rot-90",
            "rot180",
            "crop/canvas resize",
        }:
            registry = CanvasMetaTransformRegistry.shared()
            mode = "translate:0:0" if operation == "crop/canvas resize" else operation
            for layer in canvas._flatten_layers():
                if (
                    LAYOUT_NAMESPACE in ((layer.metadata or {}).get("plugins") or {})
                    and registry.transform(layer.metadata, mode, canvas._width, canvas._height)
                    is not None
                ):
                    return
            raise PanelFrameError(f"frame layout transform hook is unavailable for {operation}")
        raise PanelFrameError(
            f"Managed comic frames require the pending {operation} geometry integration"
        )
