"""``webtoon_manifest.json`` next to the slices (CT-12 spec 12 §10 / §17).

Relative file names, dimensions, bytes, hash, source page ids and warnings
per slice; profile id + revision; stream geometry.  Never an absolute
document path, prompt or credential.  Written atomically; a manifest
failure must not fail the slice export (the caller reports it).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from loomstroke.io.atomic_export_writer import write_atomically

from ..batch_export_result import BatchExportResult
from .vertical_stream_plan import VerticalStreamPlan
from .webtoon_profile import WebtoonProfile
from .webtoon_slice_artifact import WebtoonSliceArtifact

__all__ = ["WebtoonManifestWriter"]


class WebtoonManifestWriter:
    """Serialise the package description."""

    SCHEMA: ClassVar[str] = "loomstroke.webtoon_manifest.v1"
    FILE_NAME: ClassVar[str] = "webtoon_manifest.json"

    def payload(
        self,
        *,
        profile: WebtoonProfile,
        stream: VerticalStreamPlan,
        artifacts: Sequence[WebtoonSliceArtifact],
        result: BatchExportResult,
        format_id: str,
        episode_name: str = "",
        created: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "created": created or datetime.now(UTC).isoformat(),
            "episode": episode_name,
            "format": format_id,
            "profile": {"id": profile.id, "revision": profile.revision},
            "stream": {
                "width": stream.width,
                "height": stream.height,
                "segments": [
                    {
                        "pageId": span.page_id,
                        "streamY0": span.stream_y0,
                        "streamY1": span.stream_y1,
                    }
                    for span in stream.spans
                ],
            },
            "status": result.status,
            "counts": {
                "exported": result.count("success", "warning"),
                "warnings": result.count("warning"),
                "failed": result.count("failed"),
                "skipped": result.count("skipped", "cancelled"),
            },
            "slices": [
                {
                    "index": artifact.index + 1,
                    "file": artifact.file_name,
                    "width": artifact.width,
                    "height": artifact.height,
                    "bytes": artifact.byte_count,
                    "sha256": artifact.sha256,
                    "sourcePageIds": list(artifact.source_page_ids),
                    "streamY0": artifact.stream_y0,
                    "streamY1": artifact.stream_y1,
                    "warnings": list(artifact.warnings),
                }
                for artifact in artifacts
            ],
        }

    def write(
        self,
        directory: Path,
        *,
        profile: WebtoonProfile,
        stream: VerticalStreamPlan,
        artifacts: Sequence[WebtoonSliceArtifact],
        result: BatchExportResult,
        format_id: str,
        episode_name: str = "",
    ) -> Path:
        target = Path(directory) / self.FILE_NAME
        text = json.dumps(
            self.payload(
                profile=profile,
                stream=stream,
                artifacts=artifacts,
                result=result,
                format_id=format_id,
                episode_name=episode_name,
            ),
            ensure_ascii=False,
            indent=2,
        )

        def encode(tmp: Path) -> None:
            tmp.write_text(text, encoding="utf-8")

        write_atomically(target, encode)
        return target
