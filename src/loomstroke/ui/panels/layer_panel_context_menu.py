"""Context-menu builder for :mod:`layer_panel`."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QMenu


class LayerPanelContextMenuBuilder:
    """Build and show LayerPanel context menus using the panel's existing actions."""

    def __init__(self, panel: Any) -> None:
        self._panel = panel

    def show(self, pos: QPoint) -> None:
        panel = self._panel
        item = panel._list.itemAt(pos)
        if item is None:
            return
        layer_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(layer_id, str) or not layer_id:
            return
        if panel._list.currentItem() is not item:
            panel._list.setCurrentItem(item)

        menu = QMenu(panel)
        layer = panel._resolve_canvas_layer(layer_id)
        is_group = layer is not None and getattr(layer, "layer_type", "") == "group"
        is_mask = panel._is_mask_layer(layer_id)

        action_add = menu.addAction(panel._ts("Add Layer"))
        action_add.triggered.connect(panel._on_add_clicked)
        action_duplicate = menu.addAction(panel._ts("Duplicate Layer"))
        action_duplicate.triggered.connect(panel._on_duplicate_clicked)
        action_delete = menu.addAction(panel._ts("Delete Layer"))
        action_delete.triggered.connect(panel._on_delete_clicked)

        menu.addSeparator()
        self.build_edit_actions(menu, layer_id)

        menu.addSeparator()
        selected_ids = panel._get_selected_layer_ids()
        if len(selected_ids) >= 2:
            action_group = menu.addAction(panel._ts("Group Layers"))
            action_group.triggered.connect(panel._on_group_clicked)
        if is_group:
            from .comic_balloon_rows import balloon_id  # noqa: PLC0415

            action_ungroup = menu.addAction(panel._ts("Ungroup"))
            action_ungroup.setEnabled(balloon_id(layer) is None)
            action_ungroup.triggered.connect(lambda: panel._on_ungroup_clicked(layer_id))

        menu.addSeparator()
        is_clipping = bool(getattr(layer, "clipping", False)) if layer else False
        clip_text = (
            panel._ts("Release Clipping Mask") if is_clipping else panel._ts("Create Clipping Mask")
        )
        action_clip = menu.addAction(clip_text)
        action_clip.triggered.connect(lambda: panel._toggle_clipping(layer_id))

        self.build_mask_and_filter_menu(menu, layer_id, layer, is_group)

        if is_mask:
            menu.addSeparator()
            action_load_sel = menu.addAction(panel._ts("Load as Selection"))
            action_load_sel.triggered.connect(lambda: panel._load_mask_as_selection(layer_id))
            action_invert = menu.addAction(panel._ts("Invert Transparency"))
            action_invert.triggered.connect(lambda: panel._invert_mask_layer(layer_id))

        menu.exec(panel._list.viewport().mapToGlobal(pos))

    def build_edit_actions(self, menu: QMenu, layer_id: str) -> None:
        """Populate rename / merge / rasterize actions in context menu."""
        panel = self._panel
        from .comic_balloon_rows import add_legacy_group_action  # noqa: PLC0415

        add_legacy_group_action(menu, panel, layer_id)
        action_rename = menu.addAction(panel._ts("Rename"))
        action_rename.triggered.connect(lambda: panel._rename_layer(layer_id))

        action_merge = menu.addAction(panel._ts("Merge Down"))
        action_merge.setEnabled(panel._can_merge_down(layer_id))
        action_merge.triggered.connect(panel._on_merge_down_requested)

        action_merge_vis = menu.addAction(panel._ts("Merge Visible"))
        action_merge_vis.setEnabled(panel._can_merge_visible())
        action_merge_vis.triggered.connect(panel._on_merge_visible_requested)

        action_rasterize = menu.addAction(panel._ts("Rasterize"))
        action_rasterize.setEnabled(panel._can_rasterize_layer(layer_id))
        action_rasterize.triggered.connect(lambda: panel._rasterize_layer(layer_id))

        action_convert_to_vector = menu.addAction(panel._ts("Convert to Vector"))
        action_convert_to_vector.setEnabled(panel._is_shape_layer(layer_id))
        action_convert_to_vector.triggered.connect(lambda: panel._convert_to_vector(layer_id))

    def build_mask_and_filter_menu(
        self, menu: QMenu, layer_id: str, layer: object, is_group: bool
    ) -> None:
        """Populate layer-mask and filter sub-items in context menu."""
        panel = self._panel
        has_mask = layer is not None and getattr(layer, "layer_mask", None) is not None
        if has_mask:
            is_editing = bool(getattr(layer, "mask_edit_mode", False))
            edit_text = (
                panel._ts("Finish Mask Editing") if is_editing else panel._ts("Edit Layer Mask")
            )
            action_edit_mask = menu.addAction(edit_text)
            action_edit_mask.triggered.connect(
                lambda: panel._toggle_mask_edit_mode(layer_id, not is_editing)
            )
            action_mask = menu.addAction(panel._ts("Remove Layer Mask"))
            action_mask.triggered.connect(lambda: panel._remove_layer_mask(layer_id))
        else:
            action_mask = menu.addAction(panel._ts("Add Layer Mask"))
            action_mask.triggered.connect(lambda: panel._add_layer_mask(layer_id))

        sam3_action_builder = getattr(panel, "_build_sam3_layer_mask_menu_action", None)
        if callable(sam3_action_builder):
            sam3_action_builder(menu, layer_id)
        sam3_add_action_builder = getattr(panel, "_build_sam3_add_to_layer_mask_menu_action", None)
        if callable(sam3_add_action_builder):
            sam3_add_action_builder(menu, layer_id)
        sam3_overlay_toggle_builder = getattr(
            panel,
            "_build_sam3_toggle_candidate_overlay_menu_action",
            None,
        )
        if callable(sam3_overlay_toggle_builder):
            sam3_overlay_toggle_builder(menu, layer_id)

        filter_menu = menu.addMenu(panel._ts("Add Filter"))
        if is_group:
            filter_menu.setEnabled(False)
        else:
            populate = getattr(panel, "_populate_filter_menu", None) or panel._populate_effect_menu
            populate(filter_menu, layer_id)
        # CT-21: Layer Styles... / Edit Fill... / Rasterize Appearance.
        appearance_menu = getattr(panel, "_appearance_menu", None)
        if callable(appearance_menu):
            appearance_menu().add_context_actions(menu, layer_id)


# Historical context-menu builder hook.
LayerPanelContextMenuBuilder.build_mask_and_effect_menu = (
    LayerPanelContextMenuBuilder.build_mask_and_filter_menu
)
