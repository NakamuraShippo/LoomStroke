"""``publication_manifest.json`` — LoomStroke's neutral exchange manifest (CT-26).

Schema-versioned, relative paths only, hashes / sizes / dimensions per
item, binding and roles preserved even when the target format cannot
express them.  Never absolute paths, staging info or credentials.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, ClassVar

from .digital_package_request import DigitalComicPackageRequest

__all__ = ["PublicationManifestWriter"]


class PublicationManifestWriter:
    SCHEMA: ClassVar[str] = "loomstroke.publication_manifest.v1"
    FILE_NAME: ClassVar[str] = "publication_manifest.json"
    CBZ_MEMBER: ClassVar[str] = "META-INF/loomstroke-publication.json"

    def payload(
        self, request: DigitalComicPackageRequest, *, created: str | None = None
    ) -> dict[str, Any]:
        metadata = request.metadata
        return {
            "schema": self.SCHEMA,
            "created": created or datetime.now(UTC).isoformat(),
            "format": request.format_id,
            "binding": request.binding,
            "pageProgression": request.progression,
            "metadata": {
                "title": metadata.title,
                "language": metadata.language,
                "identifier": metadata.identifier,
                "creators": list(metadata.creators),
                "series": metadata.series,
                "volume": metadata.volume,
                "description": metadata.description,
            },
            "items": [
                {
                    "sourceId": item.source_id,
                    "logicalIndex": item.logical_index,
                    "role": item.role,
                    "spreadSide": item.spread_side,
                    "mediaType": item.media_type,
                    "path": item.package_path,
                    "sha256": item.content_hash,
                    "bytes": item.byte_size,
                    "width": item.pixel_size[0],
                    "height": item.pixel_size[1],
                    **({"altText": item.alt_text} if item.alt_text else {}),
                }
                for item in request.items
            ],
        }

    def json_bytes(
        self, request: DigitalComicPackageRequest, *, created: str | None = None
    ) -> bytes:
        return (
            json.dumps(self.payload(request, created=created), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
