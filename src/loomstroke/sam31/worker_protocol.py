"""SAM31 worker JSONL protocol: commands, model states, and error codes.

One JSON object per line on stdin/stdout. Requests carry ``id`` and ``cmd``;
responses echo ``id`` with either ``{"ok": true, "result": ...}`` or
``{"ok": false, "error": {"code": ..., "message": ...}}``. Binary mask data
never travels through the pipe — masks are written as grayscale coverage
PNGs into the caller-provided ``outputDir`` and referenced by path.
"""

from __future__ import annotations

PROTOCOL_VERSION = 1

# ── Commands ────────────────────────────────────────────────────────────
CMD_HEALTH = "health"
CMD_CAPABILITIES = "capabilities"
CMD_RUNTIME_INFO = "runtime_info"
CMD_LOAD_MODEL = "load_model"
CMD_UNLOAD_MODEL = "unload_model"
CMD_MODEL_STATUS = "model_status"
CMD_SEGMENT_TEXT = "segment_text"
CMD_SEGMENT_POINT = "segment_point"
CMD_SEGMENT_BOX = "segment_box"
CMD_SEGMENT_EXEMPLAR = "segment_exemplar"
CMD_REFINE_MASK = "refine_mask"
CMD_VIDEO_SESSION_START = "video_session_start"
CMD_VIDEO_ADD_PROMPT = "video_add_prompt"
CMD_VIDEO_PROPAGATE = "video_propagate"
CMD_VIDEO_REFINE = "video_refine"
CMD_VIDEO_SESSION_CLOSE = "video_session_close"
CMD_SHUTDOWN = "shutdown"

IMAGE_COMMANDS = frozenset(
    {
        CMD_SEGMENT_TEXT,
        CMD_SEGMENT_POINT,
        CMD_SEGMENT_BOX,
        CMD_SEGMENT_EXEMPLAR,
        CMD_REFINE_MASK,
    }
)
VIDEO_COMMANDS = frozenset(
    {
        CMD_VIDEO_SESSION_START,
        CMD_VIDEO_ADD_PROMPT,
        CMD_VIDEO_PROPAGATE,
        CMD_VIDEO_REFINE,
        CMD_VIDEO_SESSION_CLOSE,
    }
)

# ── Model lifecycle states (worker process ≠ model on GPU) ─────────────
MODEL_UNLOADED = "unloaded"
MODEL_LOADING = "loading"
MODEL_LOADED = "loaded_multiplex"  # one shared predictor serves image + video
MODEL_UNLOADING = "unloading"
MODEL_ERROR = "error"

# ── Error codes ────────────────────────────────────────────────────────
ERR_UNKNOWN_COMMAND = "unknown_command"
ERR_INVALID_REQUEST = "invalid_request"
ERR_MODEL_NOT_LOADED = "model_not_loaded"
ERR_MODEL_LOAD_FAILED = "model_load_failed"
ERR_CHECKPOINT_MISSING = "checkpoint_missing"
ERR_COMPATIBILITY_UNSUPPORTED = "compatibility_unsupported"
ERR_FEATURE_UNAVAILABLE = "feature_unavailable"
ERR_IMAGE_UNREADABLE = "image_unreadable"
ERR_OUTPUT_DIR_INVALID = "output_dir_invalid"
ERR_SESSION_UNKNOWN = "session_unknown"
ERR_OBJECT_UNKNOWN = "object_unknown"
ERR_OOM = "out_of_memory"
ERR_INTERNAL = "internal_error"
ERR_NOT_IMPLEMENTED = "not_implemented"

# ── Capability feature keys (value = "native" | "shim" | "unavailable") ─
FEATURE_TEXT = "text"
FEATURE_POINT_NEW_OBJECT = "point_new_object"
FEATURE_POINT_REFINE = "point_refine"
FEATURE_NEGATIVE_POINT = "negative_point"
FEATURE_BOX = "box"
FEATURE_EXEMPLAR = "exemplar"
FEATURE_REFINE = "refine"
FEATURE_VIDEO_TRACKING = "video_tracking"

FEATURE_NATIVE = "native"
FEATURE_SHIM = "shim"
FEATURE_UNAVAILABLE = "unavailable"

__all__ = [name for name in dir() if name.isupper()]
