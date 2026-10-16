"""Place shared content at its original page plane, below its panel frame."""

from .part_structure_command import PanelLayerInsertCommand


class SharedBackgroundInsertCommand(PanelLayerInsertCommand):
    def __init__(self, layer_id, *, anchor_ids, attributes):
        super().__init__(layer_id, parent_id=None, attributes=attributes)
        self.anchor_ids = tuple(anchor_ids)

    def apply(self, canvas):
        positions = []
        for anchor_id in self.anchor_ids:
            anchor = canvas._get_layer_by_id(anchor_id)
            seen = set()
            while anchor is not None and anchor.parent_id:
                if anchor.id in seen:
                    self.last_error = "shared background anchor has cyclic ancestry"
                    return False
                seen.add(anchor.id)
                anchor = canvas._get_layer_by_id(anchor.parent_id)
            if anchor is None or anchor not in canvas._layers:
                self.last_error = "shared background anchor is missing"
                return False
            positions.append(canvas._layers.index(anchor))
        self.position = min(positions)
        return super().apply(canvas)
