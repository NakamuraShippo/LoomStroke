"""Keep guide editors modeless while committing only accepted previews."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from ..canvas.guide_canvas_bridge import guide_canvas
from ..canvas.guide_dialog_preview import close_guide_preview, preview_page_matches


def show_guide_editor(dialog, canvas, commit):
    canvas = guide_canvas(canvas)
    previous = getattr(canvas, "_guide_editor", None)
    if previous is not None and previous is not dialog:
        previous.reject()
    canvas._guide_editor = dialog
    dialog.setWindowModality(Qt.NonModal)
    dialog.setAttribute(Qt.WA_DeleteOnClose)

    def finished(code):
        try:
            if code == QDialog.Accepted and preview_page_matches(dialog):
                commit()
        finally:
            close_guide_preview(dialog)
            if getattr(canvas, "_guide_editor", None) is dialog:
                canvas._guide_editor = None

    dialog.finished.connect(finished)

    def destroyed():
        if getattr(canvas, "_guide_editor", None) is dialog:
            canvas._guide_editor = None

    dialog.destroyed.connect(destroyed)
    try:
        dialog.show()
    except Exception:
        close_guide_preview(dialog)
        if getattr(canvas, "_guide_editor", None) is dialog:
            canvas._guide_editor = None
        dialog.deleteLater()
        raise
    return dialog
