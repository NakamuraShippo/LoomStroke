"""Read-only comic reference controls which navigate to the real source layer."""

from PySide6.QtWidgets import QPushButton

from .comic_frame_rows import ts


def configure_reference_row(widget, source_id, select, emit):
    for control in (widget.lock_box, widget.visibility_box, widget.alpha_lock_box):
        control.setEnabled(False)
    widget.mask_thumbnail_label.hide()
    button = QPushButton(ts("Go to source"), widget)
    widget._comic_source_button = button

    def jump():
        if select(source_id):
            emit(source_id)

    button.clicked.connect(jump)
    widget.thumbnail_clicked.connect(jump)
    widget.layout().addWidget(button)
