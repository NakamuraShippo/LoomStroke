"""Registers the built-in command-backed actions into a catalog."""

from __future__ import annotations

from ..action_catalog import ActionCatalog
from .layer_add_action import LayerAddAction
from .layer_blend_mode_action import LayerBlendModeAction
from .layer_delete_action import LayerDeleteAction
from .layer_duplicate_action import LayerDuplicateAction
from .layer_opacity_action import LayerOpacityAction
from .layer_rename_action import LayerRenameAction
from .layer_visibility_action import LayerVisibilityAction

__all__ = ["BUILTIN_ACTIONS", "register_builtin_actions"]

BUILTIN_ACTIONS = (
    LayerVisibilityAction,
    LayerOpacityAction,
    LayerBlendModeAction,
    LayerRenameAction,
    LayerAddAction,
    LayerDuplicateAction,
    LayerDeleteAction,
)


def register_builtin_actions(catalog: ActionCatalog) -> ActionCatalog:
    for action_type in BUILTIN_ACTIONS:
        adapter = action_type()
        catalog.register(adapter.descriptor, adapter.execute, replace=True)
    return catalog
