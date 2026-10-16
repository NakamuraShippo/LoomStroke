"""Clipboard access boundary (CT-19 spec 19 §7).

Core never touches ``QClipboard``: it talks to this Protocol in bytes
(MIME parts and a PNG-encoded standard image), so headless tests use a
fake and the UI plugs in the Qt adapter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

__all__ = ["ClipboardProvider"]


class ClipboardProvider(Protocol):
    def formats(self) -> Sequence[str]:
        """MIME types currently offered by the OS clipboard."""
        ...

    def data(self, mime: str) -> bytes | None:
        """Raw bytes for ``mime`` (``None`` when absent)."""
        ...

    def image_png(self) -> bytes | None:
        """The standard clipboard image re-encoded as PNG (``None`` when absent)."""
        ...

    def publish(self, parts: Mapping[str, bytes], *, image_png: bytes | None) -> bool:
        """Put ``parts`` (MIME -> bytes) and an optional standard image on the clipboard."""
        ...

    def sequence(self) -> int:
        """Monotonic change counter (for decode caching); 0 when unknown."""
        ...
