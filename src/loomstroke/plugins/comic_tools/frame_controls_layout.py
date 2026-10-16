"""Presentation and contextual disclosure for the existing frame operations."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.ui.widgets.settings_section import SettingsSection

ACTION_LABELS = {
    "Preview split": "Split panel",
    "Preview inclined boundary": "Change boundary slope",
    "Preview template inclination": "Change template slope",
}


def labelled_field(view, layout, key, source, field, ts):
    label = QLabel(ts(source))
    label.setBuddy(field)
    label.setWordWrap(True)
    view.field_labels[key] = label
    view._sources.append((label, source))
    layout.addRow(label, field)


def _form(layout):
    form = QFormLayout()
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    layout.addLayout(form)
    return form


def _button(view, layout, source, callback, ts):
    button = QPushButton(ts(ACTION_LABELS.get(source, source)))
    button.setToolTip(ts(ACTION_LABELS.get(source, source)))
    view._sources.append((button, source))
    view.buttons[source] = button
    button.clicked.connect(lambda checked=False: view.run(callback))
    layout.addWidget(button)
    return button


def _section(view, layout, key, source):
    section = SettingsSection(source, view, domain="ComicPanelFrames")
    view.sections[key] = section
    layout.addWidget(section)
    return section.content_layout


def build_frame_controls(view, ts):  # noqa: PLR0915 - linear widget construction
    workflow = view.workflow
    view.field_labels, view.buttons, view.sections = {}, {}, {}
    view._last_repair, view._preview_visible = None, False
    view._preview_preparing = False
    view._reveal_timer = QTimer(view)
    view._reveal_timer.setSingleShot(True)
    view._reveal_timer.timeout.connect(lambda: _reveal_preview(view))
    layout = QVBoxLayout(view)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    view.selected_label = QLabel()
    view.selected_label.setWordWrap(True)
    layout.addWidget(view.selected_label)
    view.status = QLabel()
    view.status.setWordWrap(True)
    layout.addWidget(view.status)
    _button(view, layout, "Convert panel", lambda: workflow.convert(view.panel_id()), ts)
    row = QHBoxLayout()
    layout.addLayout(row)
    _button(view, row, "Draw here", lambda: workflow.draw_here(view.panel_id()), ts)
    _button(
        view, row, "New rough drawing", lambda: workflow.draw_here(view.panel_id(), new=True), ts
    )

    view.preview_host = QWidget(view)
    preview_layout = QVBoxLayout(view.preview_host)
    preview_layout.setContentsMargins(0, 0, 0, 0)
    view.preview_label = QLabel()
    view.preview_label.setWordWrap(True)
    preview_layout.addWidget(view.preview_label)
    view.table = QTableWidget(0, 4)
    view.table.setMinimumHeight(80)
    view.table.setMaximumHeight(160)
    view.table.horizontalHeader().setStretchLastSection(True)
    preview_layout.addWidget(view.table)
    row = QHBoxLayout()
    preview_layout.addLayout(row)
    _button(view, row, "Cancel", view.cancel, ts)
    layout.addWidget(view.preview_host)

    contents = _section(view, layout, "contents", "Images and balloons")
    form = _form(contents)
    view.fit = QComboBox()
    for value, source in (("cover", "Cover panel"), ("contain", "Fit whole image")):
        view.fit.addItem(ts(source), value)
    labelled_field(view, form, "fit", "Image fit", view.fit, ts)
    view.distribution = QComboBox()
    for value, source in (
        ("same_panel", "All images in this panel"),
        ("reading_order", "Distribute in reading order"),
    ):
        view.distribution.addItem(ts(source), value)
    labelled_field(view, form, "distribution", "Multiple images", view.distribution, ts)
    for source, callback in (
        ("Remember drawing layer", lambda: workflow.remember_current(view.panel_id())),
        (
            "Attach selected balloon",
            lambda: workflow.attach_balloon(workflow.tool.selected_balloon_id, view.panel_id()),
        ),
        ("Wrap selected text in a balloon", lambda: workflow.wrap_selected_text(view.panel_id())),
        ("Clip selected content", lambda: workflow.display_current(view.panel_id(), "clip")),
        (
            "Allow selected content overflow",
            lambda: workflow.display_current(view.panel_id(), "overflow"),
        ),
        (
            "Bring selected content to page front",
            lambda: workflow.display_current(view.panel_id(), "front"),
        ),
        ("Make shared view independent", lambda: workflow.independent_current(view.panel_id())),
    ):
        _button(view, contents, source, callback, ts)

    edges = _section(view, layout, "edges", "Edit frame lines")
    form = _form(edges)
    view.edge = QComboBox()
    labelled_field(view, form, "edge", "Frame edge", view.edge, ts)
    view.erase_width = QDoubleSpinBox()
    view.erase_width.setRange(1, 1000)
    view.erase_width.setValue(12)
    view.erase_width.setSuffix(" px")
    labelled_field(view, form, "erase_width", "Vector edge erase width", view.erase_width, ts)
    for source, callback in (
        ("Rasterize edge", lambda: workflow.rasterize(view.panel_id(), view.edge_id())),
        ("Edit vector edge", view.edit_vector_edge),
        (
            "Erase vector edge",
            lambda: workflow.vector_erasure.arm(
                view.panel_id(), view.edge_id(), view.erase_width.value()
            ),
        ),
        ("Paint edge", lambda: workflow.paint_edge(view.panel_id(), view.edge_id())),
        ("Restore vector edge", lambda: workflow.restore(view.panel_id(), view.edge_id())),
    ):
        _button(view, edges, source, callback, ts)

    structure = _section(view, layout, "structure", "Rearrange panels")
    form = _form(structure)
    view.split_policy = QComboBox()
    for value, source in (
        ("move", "Move"),
        ("copy", "Independent copy"),
        ("shared", "Shared page coordinates"),
    ):
        view.split_policy.addItem(ts(source), value)
    labelled_field(view, form, "split_policy", "Split image handling", view.split_policy, ts)
    for source, callback in (
        ("Preview split", view.preview),
        ("Split and duplicate panel content", view.preview_duplicate),
    ):
        _button(view, structure, source, callback, ts)
    form = _form(structure)
    view.incline = QDoubleSpinBox()
    view.incline.setRange(-0.9, 0.9)
    view.incline.setDecimals(2)
    view.incline.setSingleStep(0.05)
    labelled_field(view, form, "incline", "Boundary slope", view.incline, ts)
    _button(view, structure, "Preview inclined boundary", view.preview_incline, ts)
    _button(view, structure, "Preview template inclination", view.preview_template_incline, ts)
    view.incline.lineEdit().returnPressed.connect(lambda: view.run(view.preview_incline))
    from .frame_arrangement_controls import FrameArrangementControls  # noqa: PLC0415

    view.arrangement = FrameArrangementControls(view)
    structure.addWidget(view.arrangement)
    for source, disposition in (
        ("Delete panel and keep content", "keep"),
        ("Delete panel and its content", "delete"),
    ):
        _button(
            view,
            structure,
            source,
            lambda value=disposition: workflow.delete_panel(view.panel_id(), value),
            ts,
        )

    repair = _section(view, layout, "repair", "Boundary and content repair")
    from .frame_repair_controls import FrameRepairControls  # noqa: PLC0415

    view.repairs = FrameRepairControls(view)
    repair.addWidget(view.repairs)
    for combo in (
        view.edge,
        view.fit,
        view.distribution,
        view.split_policy,
        view.repairs.items,
        view.repairs.clip_panels,
        view.repairs.edges,
        view.arrangement.targets,
    ):
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)


def refresh_frame_context(view, state, ts):
    pid = view.workflow.tool.selected_panel_id
    record = state.panel_records.get(pid) if state else None
    frame = read_frame_layer(record.metadata) if record else None
    view.selected_label.setText(
        ts("Selected panel: {panel}").format(
            panel=ts("Panel {number}").format(number=record.reading_order + 1)
        )
        if record
        else ts("Select a panel to edit its frame.")
    )
    view.status.setVisible(bool(view.status.text()))
    view.buttons["Convert panel"].setVisible(record is not None and frame is None)
    for source, button in view.buttons.items():
        if source not in {"Convert panel", "Cancel", "Preview template inclination"}:
            button.setEnabled(frame is not None)
    view.buttons["Convert panel"].setEnabled(record is not None)
    view.buttons["Preview template inclination"].setEnabled(
        bool(state and state.layout and not state.layout.root.is_leaf)
    )
    for source in ("Draw here", "New rough drawing"):
        view.buttons[source].setEnabled(frame is not None and view.workflow.draft is None)
    view.edge.setEnabled(frame is not None)
    view.erase_width.setEnabled(frame is not None)
    refresh_preview_context(view, ts)


def refresh_preview_context(view, ts):
    preparing = view.workflow.preparation.task is not None
    preview = view.workflow.draft is not None or preparing
    view.preview_host.setVisible(preview)
    view.table.setVisible(
        bool(preview and view.table.rowCount() and (view.workflow.split or view.workflow.template))
    )
    view.preview_label.setText(
        ts("Preparing panel operation…")
        if view.workflow.preparation.task
        else ts("Release to apply. Esc cancels the drag.")
    )
    if hasattr(view, "repairs"):
        repair = view.workflow.repair
        if repair is not None and repair is not view._last_repair:
            view.sections["repair"].set_expanded(True)
        view._last_repair = repair
    if preview and (not view._preview_visible or (view._preview_preparing and not preparing)):
        view._reveal_timer.start(0)
    view._preview_visible = preview
    view._preview_preparing = preparing
    for section in view.sections.values():
        section.setEnabled(not preparing)
    if preparing:
        for source, button in view.buttons.items():
            if source != "Cancel":
                button.setEnabled(False)
    panel = getattr(view.workflow.tool, "_panel_widget", None)
    state = getattr(view, "_current_state", None)
    selected = view.workflow.tool.selected_panel_id
    for widget, source in getattr(panel, "_panel_sources", ()):
        if isinstance(widget, QPushButton):
            allowed = state is not None
            if source in {
                "Merge with next",
                "Delete panel",
                "Change ratio",
                "Move up",
                "Move down",
            }:
                allowed = bool(state and selected in state.panel_records)
            widget.setEnabled(allowed and not preparing)


def _reveal_preview(view):
    if view.workflow.draft is None or view.workflow.preparation.auto_commit:
        return
    from .tool import PANEL_ID  # noqa: PLC0415 - avoid panel construction import cycle

    tool = view.workflow.tool
    show_panel = getattr(tool.host_api, "set_panel_visible", None)
    if callable(show_panel):
        show_panel(PANEL_ID, True)
    panel = getattr(tool, "_panel_widget", None)
    if panel is not None:
        panel.tabs.setCurrentIndex(0)
    dock = view.parentWidget()
    while dock is not None and not isinstance(dock, QDockWidget):
        dock = dock.parentWidget()
    if dock is not None:
        dock.raise_()
    parent = view.parentWidget()
    while parent is not None and not isinstance(parent, QScrollArea):
        parent = parent.parentWidget()
    if parent is not None:
        parent.ensureWidgetVisible(view.preview_host)
    # Showing the dock changes the native surface's exposed region. Re-present
    # after Qt has processed that layout, even when the projection is unchanged.
    QTimer.singleShot(0, view, view.workflow.refresh_view)
