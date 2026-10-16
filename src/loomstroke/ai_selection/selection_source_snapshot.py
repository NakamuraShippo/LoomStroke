"""Source snapshot helpers for SAM3 AI Select requests."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .assets import Sam3AssetStore
from .data_model import SourceSnapshotRef
from .selection_bridge import Sam3SelectionContext, Sam3SelectionScope

SAM3_SELECTION_SOURCE_SNAPSHOT_SCHEMA_VERSION = "sam3.selection_source_snapshot.v1"

_SELECTION_SCOPES = {"active_layer", "visible_composite", "selection_bounds", "page"}


class Sam3SelectionSourceSnapshotError(ValueError):
    """Raised when a SAM3 source snapshot cannot be created safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3SelectionSourceSnapshot:
    """File-backed source snapshot and selection context for one AI Select run."""

    source_ref: SourceSnapshotRef
    context: Sam3SelectionContext
    artifact_size_bytes: int
    artifact_sha256: str
    schema_version: str = SAM3_SELECTION_SOURCE_SNAPSHOT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "sourceRef": self.source_ref.to_dict(),
            "context": _context_to_dict(self.context),
            "artifactSizeBytes": self.artifact_size_bytes,
            "artifactSha256": self.artifact_sha256,
        }


def create_selection_source_snapshot_from_qimage(
    image: object,
    *,
    asset_store: Sam3AssetStore,
    scope: Sam3SelectionScope = "visible_composite",
    snapshot_id: str | None = None,
    document_id: str | None = None,
    page_id: str | None = None,
    layer_id: str | None = None,
    dpi: int | None = None,
    available_layer_ids: tuple[str, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> Sam3SelectionSourceSnapshot:
    """Persist a detached QImage as a SAM3 source snapshot."""

    normalized_scope = _normalize_scope(scope)
    snapshot_png, width, height = _encode_qimage_png(image)
    artifact_sha = hashlib.sha256(snapshot_png).hexdigest()
    normalized_snapshot_id = snapshot_id or _default_snapshot_id(normalized_scope, artifact_sha)

    source_ref = asset_store.save_source_snapshot(
        snapshot_png,
        snapshot_id=normalized_snapshot_id,
        kind=normalized_scope,
        width=width,
        height=height,
        dpi=dpi,
        document_id=_optional_text(document_id),
        page_id=_optional_text(page_id),
        layer_id=_optional_text(layer_id),
        content_hash=artifact_sha,
        metadata=metadata,
    )
    context = Sam3SelectionContext(
        snapshot_id=source_ref.snapshot_id,
        scope=normalized_scope,
        canvas_width=source_ref.width,
        canvas_height=source_ref.height,
        document_id=source_ref.document_id,
        page_id=source_ref.page_id,
        layer_id=source_ref.layer_id,
        dpi=source_ref.dpi,
        content_hash=source_ref.content_hash,
        snapshot_path=source_ref.path,
        available_layer_ids=tuple(str(item) for item in available_layer_ids),
    )
    context.to_source_ref()
    return Sam3SelectionSourceSnapshot(
        source_ref=source_ref,
        context=context,
        artifact_size_bytes=len(snapshot_png),
        artifact_sha256=artifact_sha,
    )


def _encode_qimage_png(image: object) -> tuple[bytes, int, int]:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: PLC0415
    from PySide6.QtGui import QImage  # noqa: PLC0415

    if not isinstance(image, QImage):
        raise Sam3SelectionSourceSnapshotError(
            "source_image_invalid",
            "SAM3 source snapshot requires a QImage.",
        )
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        raise Sam3SelectionSourceSnapshotError(
            "source_image_empty",
            "SAM3 source snapshot image is empty.",
        )

    detached = image.copy()
    if detached.format() != QImage.Format.Format_RGBA8888:
        detached = detached.convertToFormat(QImage.Format.Format_RGBA8888)

    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise Sam3SelectionSourceSnapshotError(
            "source_snapshot_encode_failed",
            "Could not open a PNG buffer for the SAM3 source snapshot.",
        )
    try:
        if not detached.save(buffer, "PNG"):
            raise Sam3SelectionSourceSnapshotError(
                "source_snapshot_encode_failed",
                "Could not encode the SAM3 source snapshot as PNG.",
            )
    finally:
        buffer.close()

    payload = bytes(byte_array)
    if not payload:
        raise Sam3SelectionSourceSnapshotError(
            "source_snapshot_encode_failed",
            "SAM3 source snapshot PNG payload is empty.",
        )
    return payload, int(detached.width()), int(detached.height())


def _normalize_scope(scope: str) -> Sam3SelectionScope:
    raw = str(scope).strip()
    if raw in _SELECTION_SCOPES:
        return cast("Sam3SelectionScope", raw)
    raise Sam3SelectionSourceSnapshotError(
        "invalid_source_snapshot_scope",
        f"SAM3 source snapshot scope must be a raw selection scope: {raw!r}.",
    )


def _default_snapshot_id(scope: Sam3SelectionScope, artifact_sha: str) -> str:
    return f"sam3-src-{scope}-{artifact_sha[:16]}"


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _context_to_dict(context: Sam3SelectionContext) -> dict[str, Any]:
    return {
        "snapshotId": context.snapshot_id,
        "scope": context.scope,
        "canvasWidth": context.canvas_width,
        "canvasHeight": context.canvas_height,
        "documentId": context.document_id,
        "pageId": context.page_id,
        "layerId": context.layer_id,
        "dpi": context.dpi,
        "contentHash": context.content_hash,
        "snapshotPath": context.snapshot_path,
        "availableLayerIds": list(context.available_layer_ids),
    }


__all__ = [
    "SAM3_SELECTION_SOURCE_SNAPSHOT_SCHEMA_VERSION",
    "Sam3SelectionSourceSnapshot",
    "Sam3SelectionSourceSnapshotError",
    "create_selection_source_snapshot_from_qimage",
]
