"""Reset guide placement only after a fresh document has been created."""

from typing import Any

from .canvas.composition_interaction import clear_composition_guides
from .canvas.guide_canvas_bridge import guide_canvas


def reset_new_document_guides(window: Any) -> None:
    canvas = guide_canvas(getattr(window, "canvas", None))
    editor = getattr(canvas, "_guide_editor", None)
    if editor is not None:
        editor.reject()
    for attribute in ("_layout_dialog_preview", "_composition_dialog_preview"):
        preview = getattr(canvas, attribute, None)
        if preview is not None:
            preview.close()
    settings = getattr(window, "_settings", None)
    if settings is not None:
        clear_composition_guides(canvas, settings)
        if settings.get("view/composition_overlay_rotation", 0.0) != 0.0:
            settings.set("view/composition_overlay_rotation", 0.0)
    feedback = getattr(canvas, "_guide_snap_feedback", None)
    if feedback is not None:
        feedback.clear()
    controller = getattr(canvas, "_guide_controller", None)
    if controller is not None:
        # PageService already owns the fresh page; never clear the old file's
        # persisted guides through a view-state writer at this boundary.
        controller.reload_page_state()
