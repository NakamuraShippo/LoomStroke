"""Renderer-neutral immutable snapshot models for direct vector rendering.

Data contract from
``docs/dev/plan/future/vector/LOOMSTROKE_WGPU_RETAINED_VECTOR_RENDERING_DESIGN_SPEC.md``
(§7).  These models are the boundary between the mutable vector document
(``_Layer.vector_objects`` / ``plugins.vector_tools.common.VectorObject``) and
the WGPU vector renderer / CPU geometry compile workers:

- Pure Python data only (str/int/float/bool/tuple).  No Qt types, no live
  document references, so instances can cross worker-thread boundaries.
- Version fields are runtime-only content signatures.  They are never
  persisted to ``.lstroke`` and unchanged content always derives the same
  signature, which is what lets GPU caches survive layer switches.
- ``VectorRenderLayerSnapshot`` is distinct from the thin
  ``core.canvas_models.VectorLayerSnapshot`` view, which shares live
  ``VectorObject`` references and stays in place for existing callers.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field

AffineTuple = tuple[float, float, float, float, float, float]

IDENTITY_TRANSFORM: AffineTuple = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

_SIGNATURE_BYTES = 8


def stable_signature(value: object) -> int:
    """Return a deterministic non-negative int signature for plain data.

    Used to derive runtime version fields from snapshot content.  Accepts
    nested mappings/sequences of primitives; unknown objects degrade to
    ``repr`` so signature derivation never raises.
    """

    digest = hashlib.blake2b(digest_size=_SIGNATURE_BYTES)
    _feed_signature(digest, value)
    return int.from_bytes(digest.digest(), "big")


def _feed_signature(digest: hashlib.blake2b, value: object) -> None:
    if value is None:
        digest.update(b"n")
    elif isinstance(value, bool):
        digest.update(b"b1" if value else b"b0")
    elif isinstance(value, int):
        digest.update(b"i" + str(value).encode("ascii"))
    elif isinstance(value, float):
        digest.update(b"f" + repr(value).encode("ascii"))
    elif isinstance(value, str):
        encoded = value.encode("utf-8")
        digest.update(b"s" + str(len(encoded)).encode("ascii") + b":" + encoded)
    elif isinstance(value, bytes):
        digest.update(b"y" + str(len(value)).encode("ascii") + b":" + value)
    elif isinstance(value, Mapping):
        digest.update(b"m{")
        for key in sorted(value, key=repr):
            _feed_signature(digest, key)
            digest.update(b"=")
            _feed_signature(digest, value[key])
        digest.update(b"}")
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        digest.update(b"l[")
        for item in items:
            _feed_signature(digest, item)
            digest.update(b",")
        digest.update(b"]")
    else:
        digest.update(b"r" + repr(value).encode("utf-8", errors="replace"))


@dataclass(frozen=True, slots=True)
class VectorAnchorSnapshot:
    """Immutable copy of one path anchor (handles are relative offsets)."""

    pos: tuple[float, float]
    kind: str = "corner"
    h_in: tuple[float, float] | None = None
    h_out: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class VectorPathSnapshot:
    """Immutable copy of one subpath with its stable id when known."""

    path_id: str
    anchors: tuple[VectorAnchorSnapshot, ...]
    closed: bool = False


@dataclass(frozen=True, slots=True)
class VectorStyleSnapshot:
    """Immutable copy of the vector style contract."""

    stroke_color: tuple[int, int, int] = (0, 0, 0)
    stroke_width: float = 1.0
    fill_enabled: bool = False
    fill_color: tuple[int, int, int] | None = None
    join: str = "miter"
    cap: str = "butt"
    miter_limit: float = 4.0
    dash: tuple[float, ...] = ()

    @property
    def stroke_geometry_signature(self) -> int:
        """Signature of the style fields that change stroke *geometry*.

        Stroke width/join/cap/miter/dash invalidate tessellated stroke
        geometry, while color-only edits must not (spec §8.2).
        """

        return stable_signature(
            (
                float(self.stroke_width),
                str(self.join),
                str(self.cap),
                float(self.miter_limit),
                tuple(float(v) for v in self.dash),
            )
        )


@dataclass(frozen=True, slots=True)
class GlyphRunSnapshot:
    """Resolved glyph run for direct text rendering (spec §12.2).

    Positions are canvas-space baseline pen positions produced by the Qt
    layout adapter — the GPU renderer never re-lays-out strings.  Rotations
    are per-glyph degrees (vertical typography); ``cluster_map`` maps each
    glyph back to the source-text code-point index.
    """

    font_resource_id: str
    font_pixel_size: float
    glyph_ids: tuple[int, ...]
    positions: tuple[tuple[float, float], ...]
    advances: tuple[float, ...]
    rotations: tuple[float, ...]
    cluster_map: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TextObjectSnapshot:
    """Immutable direct-text renderer input for one text object (spec §7.4).

    Built by the TextLayoutAdapter only when every feature the object uses
    is supported by the direct path; unsupported objects keep ``None`` and
    fall back to the compatibility raster path (spec §12.5).
    """

    object_id: str
    font_family: str
    font_style: str
    font_pixel_size: float
    color_rgba: tuple[int, int, int, int]
    frame: tuple[float, float, float, float]  # clip rect (x0, y0, x1, y1)
    glyph_runs: tuple[GlyphRunSnapshot, ...] = ()
    layout_version: int = 0
    glyph_resource_version: int = 0


@dataclass(frozen=True, slots=True)
class VectorObjectSnapshot:
    """Immutable renderer input for one vector object.

    Version fields are content signatures (see module docstring); cache keys
    combine ``object_id`` with the relevant version so index changes alone
    never invalidate GPU resources.
    """

    object_id: str
    object_kind: str  # "draw" | "shape" | "text"
    paths: tuple[VectorPathSnapshot, ...]
    style: VectorStyleSnapshot
    transform: AffineTuple = IDENTITY_TRANSFORM
    geometry_version: int = 0
    style_version: int = 0
    transform_version: int = 0
    resource_version: int = 0
    metadata_signature: int = 0
    is_preview: bool = False
    # Direct text rendering (V5): resolved glyph runs, or None when the
    # object routes through the compatibility raster path.
    text: TextObjectSnapshot | None = None
    # CT-15: variable stroke width (profile) objects stay on the raster path.
    has_variable_width: bool = False


@dataclass(frozen=True, slots=True)
class MaskRenderRef:
    """Presence marker for a layer-attached mask (texture stays with the compositor)."""

    kind: str = "pixel"
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class FilterRenderRef:
    """Presence marker for a filter child layer; retain legacy constructor storage."""

    effect_type: str
    visible: bool = True

    @property
    def filter_type(self) -> str:
        return self.effect_type


EffectRenderRef = FilterRenderRef


@dataclass(frozen=True, slots=True)
class VectorRenderLayerSnapshot:
    """Immutable renderer input for one vector layer."""

    layer_id: str
    vector_kind: str
    visible: bool
    opacity: float
    blend_mode: str
    parent_id: str | None
    clipping: bool
    layer_transform: AffineTuple
    layer_version: int
    object_order_version: int
    mask_ref: MaskRenderRef | None = None
    effect_refs: tuple[FilterRenderRef, ...] = ()
    objects: tuple[VectorObjectSnapshot, ...] = ()

    @property
    def filter_refs(self) -> tuple[FilterRenderRef, ...]:
        return self.effect_refs


@dataclass(frozen=True, slots=True)
class VectorPreviewSnapshot:
    """Runtime-only preview object rendered on top of the base scene.

    ``hidden_object_id`` names the committed object the preview replaces so
    renderers can hide the base object without mutating the document.
    """

    layer_id: str | None
    obj: VectorObjectSnapshot
    hidden_object_id: str | None = None


@dataclass(frozen=True, slots=True)
class VectorSceneSnapshot:
    """Complete immutable vector renderer input for one frame.

    ``scene_version`` is a derived identity for diagnostics; cache validity
    is decided by the fine-grained per-object/per-layer versions.
    """

    scene_version: int
    canvas_width: int
    canvas_height: int
    color_space: str = "srgb"
    layers: tuple[VectorRenderLayerSnapshot, ...] = field(default_factory=tuple)
    preview: VectorPreviewSnapshot | None = None

    def layer(self, layer_id: str) -> VectorRenderLayerSnapshot | None:
        for layer in self.layers:
            if layer.layer_id == layer_id:
                return layer
        return None
