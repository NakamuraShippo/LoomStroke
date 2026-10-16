"""Connect new-document colour settings to the existing canvas reset path."""

from loomstroke.core.native_cmyk.new_document_command import NewCmykDocumentCommand


def reset_new_document_canvas(canvas, spec, background) -> None:
    if spec.color.mode_id != "rgba8":
        NewCmykDocumentCommand(
            spec.width_px, spec.height_px, spec.dpi, background, spec.color
        ).apply(canvas)
        return
    reset = getattr(canvas, "reset_document", None)
    if callable(reset):
        reset(
            width=spec.width_px, height=spec.height_px, dpi=max(1, spec.dpi), background=background
        )
    else:
        updater = getattr(canvas, "update_canvas_config", None)
        if callable(updater):
            updater(width=spec.width_px, height=spec.height_px, dpi=max(1, spec.dpi))
