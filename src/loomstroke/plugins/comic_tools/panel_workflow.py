"""Presentation-only grouping of existing balloon controls; no command changes."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.translations import translated_literal_domain


def _ts(source):
    return translated_literal_domain("ComicPanelWorkflow", source, use_fallback=True)


def selection_hint():
    return _ts("Select a balloon on the canvas to edit it.")


def contour_mode_label():
    return _ts("Continuous contour (standard)")


def contour_reason(reason):
    return _ts(str(reason))


def contour_preview_hint(note):
    return _ts(
        "Continuous contour preview: drag the root, middle and tip, then confirm.{note}"
    ).format(note=note)


def make_forms_responsive(page):
    """Let the scroll page grow vertically without long layer IDs forcing width."""
    for layout in page.findChildren(QLayout):
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    for form in page.findChildren(QFormLayout):
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    for combo in page.findChildren(QComboBox):
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    for label in page.findChildren(QLabel):
        label.setWordWrap(True)
    page.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)


class WorkflowSection(QWidget):
    def __init__(self, source, *, collapsible=False):
        super().__init__()
        self.source = source
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.heading = QToolButton() if collapsible else QLabel()
        self.heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        if collapsible:
            self.heading.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.heading.setCheckable(True)
        else:
            font = self.heading.font()
            font.setBold(True)
            self.heading.setFont(font)
        layout.addWidget(self.heading)
        self.content = QWidget()
        self.form = QFormLayout(self.content)
        self.form.setContentsMargins(8, 0, 0, 8)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addWidget(self.content)
        if collapsible:
            self.heading.toggled.connect(self.set_expanded)
        self.set_expanded(not collapsible)
        self.retranslate()

    def set_expanded(self, expanded):
        self.content.setVisible(expanded)
        if isinstance(self.heading, QToolButton):
            self.heading.setArrowType(
                Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
            )

    def retranslate(self):
        self.heading.setText(_ts(self.source))


class BalloonWorkflow:
    """Move existing form rows without replacing their widgets or signals."""

    def __init__(self, page, groups):
        old = page.layout()
        rows = {}
        while old.rowCount():
            row = old.takeRow(0)
            field = row.fieldItem.widget()
            rows[field] = (row.labelItem.widget() if row.labelItem else None, field)
        # A QFormLayout caches height-for-width through the hidden nested rig
        # forms. Stack sections with a box layout so expansion cannot compress
        # combo boxes/labels to a few pixels while the headings consume space.
        retired = QWidget()
        retired.setLayout(old)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sections = {}
        for key, title, collapsed, widgets in groups:
            section = WorkflowSection(title, collapsible=collapsed)
            self.sections[key] = section
            for widget in widgets:
                label, field = rows.pop(widget)
                if label is None:
                    section.form.addRow(field)
                else:
                    section.form.addRow(label, field)
            layout.addWidget(section)
        if rows:
            raise ValueError("Ungrouped comic controls must not be discarded")
        retired.deleteLater()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setWidget(page)
        make_forms_responsive(page)

    def retranslate(self):
        for section in self.sections.values():
            section.retranslate()


def fold_rows(form, source, widgets):
    section = WorkflowSection(source, collapsible=True)
    for widget in widgets:
        row = form.takeRow(widget)
        if row.labelItem:
            section.form.addRow(row.labelItem.widget(), widget)
        else:
            section.form.addRow(widget)
    form.addRow(section)
    return section
