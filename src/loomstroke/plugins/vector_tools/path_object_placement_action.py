"""Shared context-toolbar entry for placing a layer along a vector path."""

from __future__ import annotations

from ..sdk.tool import ContextWidgetSpec


def placement_widget(tool) -> ContextWidgetSpec:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    label = translated_literal_domain(
        "PathObjectPlacement", "Objects Along Path", use_fallback=True
    )

    def open_panel():
        if tool.host_api is None:
            return
        from loomstroke.ui.dialogs.path_object_placement_dialog import (  # noqa: PLC0415
            open_path_object_placement,
        )

        open_path_object_placement(tool.host_api)

    return ContextWidgetSpec(
        key="objects_along_path", kind="button", label=label, on_clicked=open_panel, row=1
    )
