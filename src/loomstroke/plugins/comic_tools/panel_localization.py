"""Translation of balloon controls without rebuilding their edit state."""

from PySide6.QtCore import QSignalBlocker

from loomstroke.ui.translations import translate_with_fallback


def refresh_balloon_labels(panel, kinds, modes):
    def ts(source):
        return translate_with_fallback("ComicToolsPanelWidget", source)

    for combo in (panel.kind_combo, panel.bz_kind_combo):
        with QSignalBlocker(combo):
            for index in range(combo.count()):
                combo.setItemText(index, ts(kinds[combo.itemData(index)]))
    sources = dict(modes)
    with QSignalBlocker(panel.rig_mode_combo):
        for index in range(panel.rig_mode_combo.count()):
            source = sources[panel.rig_mode_combo.itemData(index)]
            # The standard contour label already has a dedicated domain.
            if panel.rig_mode_combo.itemData(index) != "contour_rebuild":
                panel.rig_mode_combo.setItemText(index, ts(source))
    panel.dialogue_table.setHorizontalHeaderLabels(
        [ts(source) for source in ("Page", "Panel", "Speaker", "Dialogue", "Status")]
    )
