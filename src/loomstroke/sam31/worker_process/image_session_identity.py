"""Private identity guards against worker restart and reused object IDs after reset."""

from __future__ import annotations

import uuid
from typing import Any


def image_session_token(runtime: Any) -> str:
    instance = getattr(runtime, "worker_instance_id", None)
    if instance is None:
        instance = uuid.uuid4().hex
        runtime.worker_instance_id = instance
    session = getattr(runtime, "image_session_id", None) or "none"
    generation = getattr(runtime, "image_session_generation", 0)
    return f"{instance}:{session}:{generation}"


def reset_image_session(runtime: Any, session_id: str) -> None:
    runtime.require_predictor().handle_request({"type": "reset_session", "session_id": session_id})
    runtime.image_session_generation = getattr(runtime, "image_session_generation", 0) + 1
