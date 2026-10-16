"""Finish panel numeric edits with Enter using the existing operation methods."""


def bind_panel_numeric_inputs(panel):
    # Ratio is shared with "Split by ratio": focus loss must not resize a panel
    # before that split button runs. Enter is the explicit editing completion.
    panel.ratio_spin.lineEdit().returnPressed.connect(
        lambda: panel._tool.set_selected_ratio(panel.ratio_spin.value())
    )
    panel.gutter_spin.lineEdit().returnPressed.connect(
        lambda: panel._tool.set_gutter(panel.gutter_spin.value())
    )
