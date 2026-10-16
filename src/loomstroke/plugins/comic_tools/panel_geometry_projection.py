"""Project a prepared geometry plan into detached layer models for worker planning."""

import copy
from dataclasses import fields
from types import SimpleNamespace

from PySide6.QtGui import QImage

from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.materialize_command import PanelFrameMaterializeCommand
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.part_move_command import PanelPartMoveCommand
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.comic_panels.source_guard_command import PanelSourceGuardCommand

from .commands import ComicEditCommand


def _detached(value):
    return QImage(value) if isinstance(value, QImage) else copy.deepcopy(value)


def _project_carrier(item, models):
    if item._ensure_layers or item._removals:
        raise PanelFrameError("geometry projection requires existing frame layers")
    for layer_id, obj in item._additions:
        models[layer_id].vector_objects.append(copy.deepcopy(obj))
    for layer_id, obj in item._replacements:
        objects = models[layer_id].vector_objects
        index = next(i for i, old in enumerate(objects) if old.id == obj.id)
        objects[index] = copy.deepcopy(obj)
    for layer_id, metadata in item._layer_metadata_updates.items():
        models[layer_id].metadata = copy.deepcopy(metadata)


def project_panel_geometry(state, command, page_size):
    allowed = {field.name for field in fields(_Layer)}
    models = {
        part.layer_id: _Layer(
            id=part.layer_id,
            name=part.layer_id,
            parent_id=part.parent_id,
            texture_version=part.texture_version,
            **{
                key: _detached(value)
                for key, value in part.values.items()
                if key in allowed
                and key not in {"id", "name", "parent_id", "children", "texture_version"}
            },
        )
        for part in state.panel_parts.values()
    }
    for part in state.panel_parts.values():
        models[part.layer_id].children = [models[key] for key in part.children]
    roots = [node for node in models.values() if node.parent_id is None]

    def siblings(parent_id):
        return models[parent_id].children if parent_id else roots

    def register(node):
        models[node.id] = node
        for child in node.children:
            register(child)

    def visit(item):
        if isinstance(item, PanelEditCommand):
            for child in item.commands:
                visit(child)
        elif isinstance(item, PanelSourceGuardCommand):
            return
        elif isinstance(item, ComicEditCommand):
            _project_carrier(item, models)
        elif isinstance(item, PanelLayerPatchCommand):
            for key, value in item.after.items():
                setattr(models[item.layer_id], key, _detached(value))
        elif isinstance(item, PanelLayerInsertCommand):
            node = copy.copy(item._layer)
            for key, value in vars(item._layer).items():
                if key != "children":
                    setattr(node, key, _detached(value))
            node.children = []
            target = siblings(node.parent_id)
            index = len(target) if item.position < 0 else min(item.position, len(target))
            target.insert(index, node)
            register(node)
        elif isinstance(item, PanelFrameMaterializeCommand):
            source = models[item._source_id]
            target = siblings(source.parent_id)
            node = item._prepare_group(source)
            source.vector_objects = [
                obj for obj in source.vector_objects if obj.id != item._expected.id
            ]
            target.insert(target.index(source) + 1, node)
            register(node)
        elif isinstance(item, PanelPartMoveCommand):
            node = models[item.layer_id]
            siblings(node.parent_id).remove(node)
            node.parent_id = item.parent_id
            siblings(node.parent_id).append(node)
        else:
            raise PanelFrameError("unsupported geometry projection command")

    visit(command)
    from .service import ComicPageController  # noqa: PLC0415 - avoid service/planner cycle

    return ComicPageController(page_key="projected-geometry").read_state(
        SimpleNamespace(_layers=roots), page_size=page_size
    )
