"""Persist successfully applied NovelAI images in the shared Gallery store."""

from __future__ import annotations

import asyncio
import io
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from PIL import Image

from loomstroke.core.generation_record import GenerationRecord
from loomstroke.services.thumbnails import generate_gallery_thumbnail


async def record_novelai_image(
    store: Any,
    request: Mapping[str, Any],
    image_bytes: bytes,
    *,
    index: int,
    count: int,
) -> GenerationRecord:
    """Give each applied output its own record and original image file."""
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
    job_id = f"novelai-{uuid4().hex}"
    now = datetime.now().astimezone().isoformat()
    seed = request.get("seed")
    source_id = request.get("source_layer_id")
    record = GenerationRecord(
        job_id=job_id,
        service_id="novelai",
        service_name="NovelAI",
        status="completed",
        created_at=now,
        completed_at=now,
        prompt=str(request.get("prompt") or ""),
        negative_prompt=str(request.get("negative_prompt") or ""),
        model_name=str(request.get("model") or ""),
        seed=int(seed) if seed is not None else None,
        width=width,
        height=height,
        service_snapshot={
            "kind": str(request.get("kind") or "generate"),
            "model": str(request.get("model") or ""),
            "source_layer_id": str(source_id) if source_id is not None else None,
            "variation_job_id": request.get("variation_job_id"),
            "target_layer_id": request.get("target_layer_id"),
            "output_index": index - 1,
            "output_count": count,
        },
    )
    record.image_path = await asyncio.to_thread(store.save_image, job_id, image_bytes)
    thumbnail = await asyncio.to_thread(generate_gallery_thumbnail, image_bytes)
    if thumbnail:
        record.thumbnail_path = await asyncio.to_thread(store.save_thumbnail, job_id, thumbnail)
    store.add_record(record)
    return record
