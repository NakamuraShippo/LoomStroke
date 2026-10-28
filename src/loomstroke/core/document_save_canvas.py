"""Select save-candidate geometry from an explicit page canvas owner."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from loomstroke.host_api import CanvasSpec as HostCanvasSpec
from loomstroke.io.layer_io import CanvasSpec as PersistCanvasSpec
from loomstroke.io.layer_io import DocumentMetadata


def has_save_canvas_owner(pages: Any) -> bool:
    """Only use the private capability when its implementation declares it.

    Attribute-generating proxies and older/custom page services keep their
    existing metadata-driven path until they explicitly implement ownership.
    """
    return callable(getattr(type(pages), "_document_canvas_for_save", None))


def select_document_canvas(
    default: HostCanvasSpec | None,
    page_override: PersistCanvasSpec | None,
    live: HostCanvasSpec | None,
) -> HostCanvasSpec:
    if page_override is not None and default is not None:
        return default
    selected = live if live is not None else default
    if selected is None:
        raise RuntimeError("The document canvas could not be determined for saving.")
    return selected


def select_save_metadata(metadata: DocumentMetadata, pages: Any) -> DocumentMetadata:
    """Return a new candidate; never commit page/default changes before capture."""
    if not has_save_canvas_owner(pages):
        return metadata
    canvas = pages._document_canvas_for_save()
    return replace(
        metadata,
        width=canvas.width,
        height=canvas.height,
        dpi=canvas.dpi,
        background_color=canvas.background_color,
    )


def apply_save_canvas(metadata: DocumentMetadata, canvas: HostCanvasSpec) -> None:
    """Align the captured candidate after its owner has successfully prepared."""
    metadata.width = canvas.width
    metadata.height = canvas.height
    metadata.dpi = canvas.dpi
    metadata.background_color = canvas.background_color
