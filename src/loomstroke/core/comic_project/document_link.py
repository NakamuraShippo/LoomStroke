"""Link from a comic project to an art document (CT-17 §7.1).

Only a stable document id, the last known basename and a content hash are
stored; absolute paths are local-only and never enter the sidecar.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DocumentLink"]


@dataclass(frozen=True, slots=True)
class DocumentLink:
    document_id: str
    basename: str = ""
    content_hash: str = ""
