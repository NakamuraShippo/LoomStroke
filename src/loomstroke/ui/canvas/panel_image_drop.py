"""Give semantic panel targets first refusal of ordinary image-file drops."""


def dispatch_panel_image_drop(widget, event, image_paths):
    emit = getattr(widget, "_emit_canvas_drop", None)
    viewport = getattr(widget, "viewport", None)
    if not callable(emit) or viewport is None:
        return False
    pos = event.position()
    point = viewport.widget_to_canvas(pos.x(), pos.y())
    payload = {
        "mime": "application/x-loomstroke-image-files",
        "image_paths": tuple(image_paths),
        "pos_canvas": tuple(point),
        "handled": False,
    }
    emit(payload)
    return bool(payload["handled"])
