"""Release a managed content container while preserving its literal Undo parent."""

from loomstroke.core.commands_layer_tree import ReparentLayerCommand


class PanelContentReleaseCommand(ReparentLayerCommand):
    def revert(self, canvas):
        # A panel-row drop normally resolves to its content group. Undo of the
        # content group itself must return to the panel, even with no balloons.
        return bool(
            canvas._layer_tree().reparent_layer(
                self._layer_id,
                self._old_parent_id,
                self._old_position,
                _membership_restore=self._membership_before,
            )
        )
