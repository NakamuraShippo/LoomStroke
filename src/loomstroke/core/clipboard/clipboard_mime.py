"""MIME names used on the OS clipboard (CT-19 spec 19 §5 / §8.1)."""

from __future__ import annotations

from typing import ClassVar

__all__ = ["ClipboardMime"]


class ClipboardMime:
    SELECTION: ClassVar[str] = "application/x-loomstroke-selection+json"
    SELECTION_IMAGE: ClassVar[str] = "application/x-loomstroke-selection-image"
    SELECTION_MASK: ClassVar[str] = "application/x-loomstroke-selection-mask"
    VECTOR: ClassVar[str] = "application/x-loomstroke-vector+json"
    PNG: ClassVar[str] = "image/png"

    #: Part name -> MIME of the binary part referenced from the envelope.
    PART_MIMES: ClassVar[dict[str, str]] = {
        "image": SELECTION_IMAGE,
        "mask": SELECTION_MASK,
    }
