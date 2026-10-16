"""Preserve content-group compositing around units assigned to another panel."""

import copy
from dataclasses import fields

from PySide6.QtGui import QImage

from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.command_types import Command

from .membership import PART_KEY
from .part_structure_command import PanelLayerInsertCommand


class PanelContentShellCommand(Command):
    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(self, source_id, layer_id, parent_id, *, preserve_mask=True):
        super().__init__()
        self.source_id, self.layer_id, self.parent_id = source_id, layer_id, parent_id
        self._insert = None
        self.preserve_mask = preserve_mask
        self.last_error = ""

    def apply(self, canvas):
        if self._insert is None:
            source = canvas._get_layer_by_id(self.source_id)
            if source is None or source.layer_type != "group" or source.clipping:
                self.last_error = "content compositing shell source is missing or clipped"
                return False
            attributes = {
                field.name: QImage(value) if isinstance(value, QImage) else copy.deepcopy(value)
                for field in fields(_Layer)
                if field.name not in {"id", "parent_id", "children", "vector_objects"}
                for value in (getattr(source, field.name),)
            }
            attributes["metadata"] = copy.deepcopy(source.metadata or {})
            attributes["metadata"].setdefault(PART_KEY, {})["role"] = "content_bundle"
            attributes["effect_cache"] = None
            if not self.preserve_mask:
                attributes["layer_mask"] = None
            for key in tuple(attributes):
                if key.startswith("effect_cache_"):
                    attributes[key] = 0
            attributes["texture_version"] = attributes["transform_version"] = 0
            self._insert = PanelLayerInsertCommand(
                self.layer_id, parent_id=self.parent_id, attributes=attributes
            )
        self._executed = self._insert.apply(canvas)
        self.last_error = self._insert.last_error
        return self._executed

    def revert(self, canvas):
        if not self._executed:
            return False
        ok = self._insert.revert(canvas)
        if ok:
            self._executed = False
        self.last_error = self._insert.last_error
        return ok
