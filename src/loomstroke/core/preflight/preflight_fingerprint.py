"""Report <-> document correspondence without content (CT-16 §8.1 / §10).

Two hashes: ``structure`` covers page ids / order / size / dpi / role —
cheap enough to recompute from page summaries without hydrating anything
— and ``content`` adds layer ids, text lengths and text hashes.  Neither
includes pixels, absolute paths or the text itself (spec 16 §13).
"""

from __future__ import annotations

import hashlib

from .document_preflight_snapshot import DocumentPreflightSnapshot
from .page_preflight_snapshot import PagePreflightSnapshot

__all__ = ["PreflightFingerprint"]


class PreflightFingerprint:
    """Deterministic hashes of a snapshot."""

    @staticmethod
    def _digest(parts: list[str]) -> str:
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:24]

    @classmethod
    def structure(cls, snapshot: DocumentPreflightSnapshot) -> str:
        return cls.structure_from_rows(
            [
                (page.page_id, page.width, page.height, page.dpi, page.role)
                for page in snapshot.pages
            ]
        )

    @classmethod
    def structure_from_rows(cls, rows: list[tuple[str, int, int, tuple[float, float], str]]) -> str:
        parts = [
            f"{page_id}|{width}x{height}|{float(dpi[0]):g},{float(dpi[1]):g}|{role}"
            for page_id, width, height, dpi, role in rows
        ]
        return cls._digest(parts)

    @classmethod
    def content(cls, snapshot: DocumentPreflightSnapshot) -> str:
        parts = [cls.structure(snapshot), snapshot.reading_direction]
        parts.append("icc" if snapshot.has_color_profile else "noicc")
        for page in snapshot.pages:
            parts.extend(cls._page_parts(page))
        return cls._digest(parts)

    @staticmethod
    def _page_parts(page: PagePreflightSnapshot) -> list[str]:
        parts = [f"page:{page.page_id}:{int(page.hydrated)}:{int(page.has_physical_layout)}"]
        for layer in page.layers:
            parts.append(
                f"layer:{layer.layer_id}:{layer.layer_type}:{int(layer.visible)}:"
                f"{layer.has_pixels}:{layer.object_count}"
            )
        for text in page.texts:
            text_hash = hashlib.sha1(text.content.encode("utf-8")).hexdigest()[:12]  # noqa: S324
            parts.append(
                f"text:{text.object_id}:{text_hash}:{text.font_family}:{text.font_size_pt:g}:"
                f"{text.text_mode}:{int(text.vertical)}:{text.overflow}:{text.font_available}"
            )
        return parts
