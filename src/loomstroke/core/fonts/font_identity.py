"""What actually rendered / would be embedded (CT-25 ``FontIdentity``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["FontIdentity"]

SourceKind = Literal["system", "document", "package", "substitute"]


@dataclass(frozen=True, slots=True)
class FontIdentity:
    postscript_name: str | None
    file_fingerprint: str | None
    face_index: int = 0
    source_kind: SourceKind = "system"
    family: str = ""
    style: str = ""
