"""Document-local master page definitions (CT-07 T3a, spec 07 §5.3/§8).

Pure model + defensive parsing for the ``loomstroke.comictools.masters``
document namespace and the per-layer instance marker.  Design decisions
(2026-08-03, user approved):

- **Masters are captured from pages** (vector layers), not edited in a
  dedicated master editor — v1 keeps the capture workflow the templates
  already use.  Vector content is stored in the same dict form
  ``.lstroke`` uses (the shared layer_io codec), so the definitions are
  plain JSON inside document metadata: no schema change (§10.1).
- **Instances are materialized**: applying a master creates a normal
  locked group whose layer metadata carries a
  :class:`MasterInstanceRef`.  Old readers that ignore the marker still
  see the content; a master whose definition disappears leaves its
  instances intact (``detached``, §13).
- Malformed master entries fall back by being **skipped with a
  warning** — one broken master must not take down the rest.

No Qt, no I/O, no service dependencies.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .comic_metadata_codec import read_masters_metadata

logger = logging.getLogger(__name__)

__all__ = [
    "MASTER_INSTANCE_LAYER_NAMESPACE",
    "MasterDefinition",
    "MasterInstanceRef",
    "MasterLayerDefinition",
    "master_from_payload",
    "master_instance_marker",
    "masters_namespace_payload",
    "read_master_definitions",
    "read_master_instance",
]

_SCHEMA_VERSION = 1

#: Layer-metadata namespace that links a materialized group to its master.
MASTER_INSTANCE_LAYER_NAMESPACE = "loomstroke.comictools.master_instance"


@dataclass(frozen=True, slots=True)
class MasterLayerDefinition:
    """One master layer; vector objects are stored dicts (lstroke form)."""

    name: str
    vector_objects: tuple[Mapping[str, Any], ...] = ()
    opacity: float = 1.0
    blend_mode: str = "normal"
    visible: bool = True


@dataclass(frozen=True, slots=True)
class MasterDefinition:
    """A document-local master (spec 07 §8)."""

    id: str
    name: str
    revision: int = 1
    target_roles: tuple[str, ...] = ()
    layers: tuple[MasterLayerDefinition, ...] = ()


@dataclass(frozen=True, slots=True)
class MasterInstanceRef:
    """What a materialized instance group remembers about its master."""

    master_id: str
    revision: int
    slot_id: str


# ---------------------------------------------------------------------
# document namespace <-> definitions


def read_master_definitions(
    document_metadata: Mapping[str, Any] | None,
) -> tuple[MasterDefinition, ...]:
    """Parse every well-formed master; skip broken entries with a warning."""
    try:
        namespace = read_masters_metadata(document_metadata)
    except ValueError:
        logger.warning("comic_masters_metadata_malformed: container malformed; ignoring")
        return ()
    entries = namespace.get("masters")
    if not isinstance(entries, list):
        return ()
    definitions: list[MasterDefinition] = []
    for index, entry in enumerate(entries):
        definition = _master_from_payload(entry)
        if definition is None:
            logger.warning("comic_master_entry_invalid: skipping masters[%s]", index)
            continue
        definitions.append(definition)
    return tuple(definitions)


def masters_namespace_payload(
    masters: Sequence[MasterDefinition],
) -> dict[str, Any]:
    """The full namespace value to merge back into document metadata."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "masters": [_master_to_payload(master) for master in masters],
    }


def _master_to_payload(master: MasterDefinition) -> dict[str, Any]:
    return {
        "id": master.id,
        "name": master.name,
        "revision": int(master.revision),
        "target_roles": list(master.target_roles),
        "layers": [
            {
                "name": layer.name,
                "vector_objects": [dict(obj) for obj in layer.vector_objects],
                "opacity": float(layer.opacity),
                "blend_mode": layer.blend_mode,
                "visible": bool(layer.visible),
            }
            for layer in master.layers
        ],
    }


def master_from_payload(raw: Any) -> MasterDefinition | None:
    """Public parser for one masters-namespace entry (None if malformed)."""
    return _master_from_payload(raw)


def _master_from_payload(raw: Any) -> MasterDefinition | None:
    if not isinstance(raw, Mapping):
        return None
    master_id = raw.get("id")
    name = raw.get("name")
    if not isinstance(master_id, str) or not master_id or not isinstance(name, str):
        return None
    revision_raw = raw.get("revision", 1)
    revision = revision_raw if type(revision_raw) is int and revision_raw >= 1 else 1
    roles_raw = raw.get("target_roles", [])
    target_roles = (
        tuple(role for role in roles_raw if isinstance(role, str))
        if isinstance(roles_raw, list)
        else ()
    )
    layers_raw = raw.get("layers", [])
    layers: list[MasterLayerDefinition] = []
    if isinstance(layers_raw, list):
        for layer_raw in layers_raw:
            layer = _layer_from_payload(layer_raw)
            if layer is not None:
                layers.append(layer)
    return MasterDefinition(
        id=master_id,
        name=name,
        revision=revision,
        target_roles=target_roles,
        layers=tuple(layers),
    )


def _layer_from_payload(raw: Any) -> MasterLayerDefinition | None:
    if not isinstance(raw, Mapping):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        return None
    objects_raw = raw.get("vector_objects", [])
    vector_objects = (
        tuple(obj for obj in objects_raw if isinstance(obj, Mapping))
        if isinstance(objects_raw, list)
        else ()
    )
    opacity_raw = raw.get("opacity", 1.0)
    try:
        opacity = min(1.0, max(0.0, float(opacity_raw)))
    except (TypeError, ValueError):
        opacity = 1.0
    blend_mode = raw.get("blend_mode", "normal")
    return MasterLayerDefinition(
        name=name,
        vector_objects=vector_objects,
        opacity=opacity,
        blend_mode=blend_mode if isinstance(blend_mode, str) else "normal",
        visible=bool(raw.get("visible", True)),
    )


# ---------------------------------------------------------------------
# layer instance markers


def master_instance_marker(ref: MasterInstanceRef) -> dict[str, Any]:
    """The layer-metadata payload for a materialized instance group."""
    return {
        "plugins": {
            MASTER_INSTANCE_LAYER_NAMESPACE: {
                "schema_version": _SCHEMA_VERSION,
                "master_id": ref.master_id,
                "revision": int(ref.revision),
                "slot_id": ref.slot_id,
            }
        }
    }


def read_master_instance(layer_metadata: Mapping[str, Any] | None) -> MasterInstanceRef | None:
    """The instance marker, or ``None`` for a layer that is not one.
    Never matches by name (spec 07 §9): slot ids only."""
    if not isinstance(layer_metadata, Mapping):
        return None
    plugins = layer_metadata.get("plugins")
    if not isinstance(plugins, Mapping):
        return None
    namespace = plugins.get(MASTER_INSTANCE_LAYER_NAMESPACE)
    if not isinstance(namespace, Mapping):
        return None
    master_id = namespace.get("master_id")
    slot_id = namespace.get("slot_id")
    revision_raw = namespace.get("revision", 1)
    if not isinstance(master_id, str) or not master_id or not isinstance(slot_id, str):
        return None
    revision = revision_raw if type(revision_raw) is int and revision_raw >= 1 else 1
    return MasterInstanceRef(master_id=master_id, revision=revision, slot_id=slot_id)
