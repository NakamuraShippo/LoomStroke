"""What was written for one slice — manifest material (CT-12 §10)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WebtoonSliceArtifact"]


@dataclass(frozen=True, slots=True)
class WebtoonSliceArtifact:
    """Relative file name only; absolute paths never leave the exporter."""

    index: int
    file_name: str
    width: int
    height: int
    byte_count: int
    sha256: str
    source_page_ids: tuple[str, ...]
    stream_y0: int
    stream_y1: int
    warnings: tuple[str, ...] = ()
