"""Separate missing optional masks from invalid image content during recovery."""

from __future__ import annotations

import logging
from collections.abc import Mapping

logger = logging.getLogger(__name__)


def missing_mask_count(owner, archive, manifest) -> int:
    """Inspect references on every page without reading the image payloads."""
    missing = set()
    layers = manifest.get("layers")
    pending = list(layers) if isinstance(layers, list) else []
    pages = manifest.get("pages")
    for page in pages if isinstance(pages, list) else []:
        if isinstance(page, Mapping):
            layers = page.get("layers")
            if isinstance(layers, list):
                pending.extend(layers)
    while pending:
        entry = pending.pop()
        if not isinstance(entry, Mapping):
            continue
        children = entry.get("children")
        if isinstance(children, list):
            pending.extend(children)
        for key in ("layer_mask", "effect_mask"):
            reference = entry.get(key)
            if not isinstance(reference, str):
                continue
            path = owner._normalize_archive_path(reference)
            if "\x00" in path:
                raise ValueError("Invalid mask image reference")
            if path not in archive.NameToInfo:
                missing.add(path)
    return len(missing)


def read_optional_mask(owner, archive, reference: str, *, strict: bool):
    path = owner._normalize_archive_path(reference)
    if "\x00" in path:
        raise ValueError("Invalid mask image reference")
    if path not in archive.NameToInfo:
        logger.warning("A referenced document mask is missing")
        return None
    try:
        return owner._read_image_from_archive(archive, path)
    except ValueError:
        if strict:
            raise
        logger.warning("A document mask could not be decoded")
        return None
