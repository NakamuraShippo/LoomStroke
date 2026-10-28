"""Persist each AI Effect image through the host's generation history store."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from .core.generation_record import GenerationRecord
from .io.layer_io import LayerIO
from .io.lstroke_archive_policy import png_dimensions_from_header
from .plugins.ai_effect.gallery_bridge import extract_ai_effect_payload, matches_param_hash
from .plugins.ai_effect.gateway_protocol import OutputImage
from .services.generation_history_store import GenerationHistoryStore
from .services.thumbnails import generate_gallery_thumbnail

logger = logging.getLogger(__name__)
# Match the existing Gallery reuse ceiling; decoded pixels use the archive budget.
_MAX_CACHE_PNG_BYTES = 64 * 1024 * 1024


class _AIEffectGalleryAdapter:
    """Bridge the generation controller's GalleryStore to the host store.

    The primary record retains its backend job ID and complete output metadata.
    Additional images get independent records/files, with their backend job ID
    and output index in the opaque AI Effect snapshot. Only the primary record
    participates in cache lookup; a sibling must never stand in for its image.
    Persistence failures are logged independently so other outputs can survive.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    def get_by_param_hash(self, param_hash: str) -> GenerationRecord | None:
        try:
            records = self._store.list_records()
        except Exception:
            logger.exception("GenerationHistoryStore.list_records failed")
            return None
        for record in records:
            payload = extract_ai_effect_payload(record)
            if payload is not None and payload.get("gallery_output_index", 0) != 0:
                continue
            if matches_param_hash(record, param_hash):
                return record
        return None

    def append(self, record: GenerationRecord, images: list[bytes]) -> None:
        if not images:
            self._append_image(record, None)
            return
        # add_record prepends: save in reverse so the primary remains first.
        # Build siblings before saving/mutating the primary's file paths.
        for index in range(len(images) - 1, -1, -1):
            output_record = record if index == 0 else self._sibling_record(record, index)
            self._append_image(output_record, images[index])

    def _read_cached_image(self, record: GenerationRecord, param_hash: str) -> OutputImage:
        """Read only the bounded original of a current, single-image primary."""
        store = self._store
        if type(store) is not GenerationHistoryStore or type(record) is not GenerationRecord:
            raise ValueError("Unsupported cache store or record")
        metadata = self._cache_metadata(record, param_hash)
        relative, internal_id, width, height = metadata
        if store.get_record(record.job_id) is not record:
            raise ValueError("Cached record is no longer available")
        rel = Path(relative)
        parts = relative.replace("\\", "/").split("/")
        if (
            rel.anchor
            or len(parts) != 2
            or parts[0] != "images"
            or rel.suffix.lower() != ".png"
            or any(not part or part in (".", "..") or ":" in part for part in parts)
        ):
            raise ValueError("Invalid cached image path")
        base = store.base_path.resolve()
        path = store.resolve_path(relative).resolve()
        if not path.is_relative_to(base):
            raise ValueError("Cached image is outside its store")
        if path.stat().st_size > _MAX_CACHE_PNG_BYTES:
            raise ValueError("Cached image exceeds the Gallery byte budget")
        with path.open("rb") as stream:
            header = stream.read(33)
            actual_width, actual_height = png_dimensions_from_header(header, "cached PNG")
            pixels = actual_width * actual_height
            if (
                (actual_width, actual_height) != (width, height)
                or pixels > LayerIO._MAX_LAYER_IMAGE_PIXELS
                or pixels * 4 > LayerIO._MAX_LAYER_IMAGE_BYTES
            ):
                raise ValueError("Cached image dimensions exceed the image budget")
            data = header + stream.read(_MAX_CACHE_PNG_BYTES + 1 - len(header))
        if len(data) > _MAX_CACHE_PNG_BYTES:
            raise ValueError("Cached image exceeds the Gallery byte budget")
        if (
            store.get_record(record.job_id) is not record
            or self._cache_metadata(record, param_hash) != metadata
        ):
            raise ValueError("Cached record changed while reading")
        return OutputImage(internal_id, data, width, height)

    @staticmethod
    def _cache_metadata(record: GenerationRecord, param_hash: str) -> tuple[str, str, int, int]:
        snapshot = record.service_snapshot
        payload = snapshot.get("ai_effect") if type(snapshot) is dict else None
        if (
            type(payload) is not dict
            or type(param_hash) is not str
            or type(payload.get("param_hash")) is not str
            or payload["param_hash"] != param_hash
            or type(payload.get("gallery_output_index", 0)) is not int
            or payload.get("gallery_output_index", 0) != 0
            or type(record.job_id) is not str
            or type(record.status) is not str
            or record.status != "completed"
            or type(record.image_path) is not str
            or not record.image_path
            or type(record.output_images) is not list
            or len(record.output_images) != 1
        ):
            raise ValueError("Invalid cached image record")
        image = record.output_images[0]
        if (
            type(image) is not dict
            or type(image.get("internal_id")) is not str
            or type(image.get("width")) is not int
            or type(image.get("height")) is not int
            or image["width"] <= 0
            or image["height"] <= 0
        ):
            raise ValueError("Invalid cached image metadata")
        return record.image_path, image["internal_id"], image["width"], image["height"]

    @staticmethod
    def _sibling_record(record: GenerationRecord, index: int) -> GenerationRecord:
        sibling = copy.deepcopy(record)
        sibling.job_id = f"{record.job_id}__output_{index}"
        sibling.image_path = None
        sibling.thumbnail_path = None
        sibling.output_images = copy.deepcopy(record.output_images[index : index + 1])
        if sibling.output_images:
            output = sibling.output_images[0]
            sibling.width = output.get("width")
            sibling.height = output.get("height")
        else:
            sibling.width = sibling.height = None
        payload = sibling.service_snapshot.setdefault("ai_effect", {})
        payload["gateway_job_id"] = record.job_id
        payload["gallery_output_index"] = index
        return sibling

    def _append_image(self, record: GenerationRecord, image: bytes | None) -> None:
        try:
            if image is not None:
                record.image_path = self._store.save_image(record.job_id, image)
                # Thumbnails remain best-effort, matching the single-image path.
                try:
                    thumb_bytes = generate_gallery_thumbnail(image)
                except Exception:
                    logger.exception("generate_gallery_thumbnail failed for job=%s", record.job_id)
                    thumb_bytes = None
                if thumb_bytes:
                    try:
                        record.thumbnail_path = self._store.save_thumbnail(
                            record.job_id, thumb_bytes
                        )
                    except Exception:
                        logger.exception(
                            "GenerationHistoryStore.save_thumbnail failed for job=%s", record.job_id
                        )
            self._store.add_record(record)
        except Exception:
            logger.exception("GenerationHistoryStore append failed for job=%s", record.job_id)
