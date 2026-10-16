"""Model lifecycle and shared state for the SAM31 worker process.

The worker process is decoupled from the model's GPU residency:
``Worker running`` and ``model loaded`` are independent states. A single
multiplex predictor serves both still images (1-frame sessions) and video —
there is no separate image model, so the lifecycle has one loaded state.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from loomstroke.sam31 import worker_protocol as protocol
from loomstroke.sam31.compatibility import (
    CompatibilityReport,
    CompatibilityStatus,
    apply_all_shims,
)

logger = logging.getLogger("loomstroke.sam31.worker")


class WorkerError(Exception):
    """Command failure carrying a protocol error code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Sam31WorkerRuntime:
    """Holds the predictor, sessions, and compatibility report."""

    def __init__(self, checkpoint_path: str | None, state_dir: str | None) -> None:
        self.checkpoint_path = checkpoint_path
        self.state_dir = Path(state_dir) if state_dir else None
        self.model_state = protocol.MODEL_UNLOADED
        self.model_error: str | None = None
        self.predictor: Any | None = None
        self.compatibility: CompatibilityReport | None = None
        # image session cache: one active predictor session per source image
        self.image_session_key: tuple[str, int, int] | None = None
        self.image_session_id: str | None = None
        self.worker_instance_id = uuid.uuid4().hex
        self.image_session_generation = 0
        self.image_frame_dir: Path | None = None
        self.next_point_object_id = 10001
        self.image_point_state: dict[int, Any] = {}
        self.image_concept_cache: list[Any] | None = None
        # video sessions: token -> predictor session id
        self.video_sessions: dict[str, str] = {}

    # ── model lifecycle ────────────────────────────────────────────────

    def load_model(self, checkpoint_path: str | None = None) -> dict[str, Any]:
        if self.model_state == protocol.MODEL_LOADED:
            return {"modelState": self.model_state, "alreadyLoaded": True}
        path = checkpoint_path or self.checkpoint_path
        if not path or not Path(path).is_file():
            raise WorkerError(
                protocol.ERR_CHECKPOINT_MISSING,
                f"SAM 3.1 checkpoint not found: {path!r}",
            )
        self.model_state = protocol.MODEL_LOADING
        self.model_error = None
        started = time.time()
        try:
            from sam3.model_builder import (  # noqa: PLC0415
                build_sam3_multiplex_video_predictor,
            )

            predictor = build_sam3_multiplex_video_predictor(
                checkpoint_path=path,
                use_fa3=False,
                async_loading_frames=False,
            )
            report = apply_all_shims(predictor)
            blocking = [
                result
                for result in report.results
                if result.name in ("windows_sdp", "multiplex_session")
                and result.status in CompatibilityStatus.TERMINAL_BAD
            ]
            if blocking:
                raise WorkerError(
                    protocol.ERR_COMPATIBILITY_UNSUPPORTED,
                    "; ".join(f"{r.name}: {r.status} ({r.detail})" for r in blocking),
                )
            self.predictor = predictor
            self.compatibility = report
            self.checkpoint_path = path
            self.model_state = protocol.MODEL_LOADED
            return {
                "modelState": self.model_state,
                "loadSeconds": round(time.time() - started, 2),
                "compatibility": report.as_payload(),
            }
        except WorkerError:
            self.model_state = protocol.MODEL_ERROR
            raise
        except Exception as exc:  # noqa: BLE001
            self.model_state = protocol.MODEL_ERROR
            self.model_error = f"{type(exc).__name__}: {exc}"
            code = (
                protocol.ERR_OOM
                if "out of memory" in str(exc).lower()
                else protocol.ERR_MODEL_LOAD_FAILED
            )
            raise WorkerError(code, self.model_error) from exc

    def unload_model(self) -> dict[str, Any]:
        if self.model_state != protocol.MODEL_LOADED:
            return {"modelState": self.model_state, "alreadyUnloaded": True}
        self.model_state = protocol.MODEL_UNLOADING
        allocated_before = self._torch_allocated_mb()
        self._close_image_session()
        for token in list(self.video_sessions):
            self._close_video_session(token)
        self.predictor = None
        import gc  # noqa: PLC0415

        gc.collect()
        try:
            import torch  # noqa: PLC0415

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
        self.model_state = protocol.MODEL_UNLOADED
        freed = max(0.0, allocated_before - self._torch_allocated_mb())
        return {"modelState": self.model_state, "freedMb": round(freed, 1)}

    def require_predictor(self) -> Any:
        if self.model_state != protocol.MODEL_LOADED or self.predictor is None:
            raise WorkerError(
                protocol.ERR_MODEL_NOT_LOADED,
                "SAM 3.1 model is not loaded; call load_model first.",
            )
        return self.predictor

    # ── info commands ──────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "protocolVersion": protocol.PROTOCOL_VERSION,
            "modelState": self.model_state,
            "modelError": self.model_error,
        }

    def capabilities(self) -> dict[str, Any]:
        features = dict.fromkeys(
            (
                protocol.FEATURE_TEXT,
                protocol.FEATURE_POINT_NEW_OBJECT,
                protocol.FEATURE_POINT_REFINE,
                protocol.FEATURE_NEGATIVE_POINT,
                protocol.FEATURE_BOX,
                protocol.FEATURE_EXEMPLAR,
                protocol.FEATURE_REFINE,
                protocol.FEATURE_VIDEO_TRACKING,
            ),
            protocol.FEATURE_UNAVAILABLE,
        )
        payload: dict[str, Any] = {
            "protocolVersion": protocol.PROTOCOL_VERSION,
            "modelState": self.model_state,
            "features": features,
        }
        report = self.compatibility
        if report is None:
            payload["note"] = "load_model resolves compatibility and features"
            return payload
        payload["compatibility"] = report.as_payload()
        base_ok = all(
            report.status_of(name) not in CompatibilityStatus.TERMINAL_BAD
            for name in ("windows_sdp", "multiplex_session")
        )
        if base_ok:
            native = protocol.FEATURE_NATIVE
            for name in (
                protocol.FEATURE_TEXT,
                protocol.FEATURE_POINT_REFINE,
                protocol.FEATURE_NEGATIVE_POINT,
                protocol.FEATURE_BOX,
                protocol.FEATURE_EXEMPLAR,
                protocol.FEATURE_REFINE,
                protocol.FEATURE_VIDEO_TRACKING,
            ):
                features[name] = native
            point_status = report.status_of("point_new_object")
            if point_status == CompatibilityStatus.NATIVE:
                features[protocol.FEATURE_POINT_NEW_OBJECT] = native
            elif point_status == CompatibilityStatus.SHIM_APPLIED:
                features[protocol.FEATURE_POINT_NEW_OBJECT] = protocol.FEATURE_SHIM
        return payload

    def runtime_info(self) -> dict[str, Any]:
        import platform  # noqa: PLC0415
        import sys  # noqa: PLC0415

        info: dict[str, Any] = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "modelState": self.model_state,
            "checkpointPath": self.checkpoint_path,
        }
        try:
            import torch  # noqa: PLC0415

            info["torch"] = torch.__version__
            info["cudaAvailable"] = torch.cuda.is_available()
            if torch.cuda.is_available():
                properties = torch.cuda.get_device_properties(0)
                info["gpuName"] = properties.name
                info["vramTotalMb"] = round(properties.total_memory / 1e6)
                info["torchAllocatedMb"] = round(torch.cuda.memory_allocated() / 1e6)
                info["torchReservedMb"] = round(torch.cuda.memory_reserved() / 1e6)
        except Exception as exc:  # noqa: BLE001
            info["torchError"] = f"{type(exc).__name__}: {exc}"
        return info

    # ── shared helpers for the API modules ─────────────────────────────

    def _torch_allocated_mb(self) -> float:
        try:
            import torch  # noqa: PLC0415

            if torch.cuda.is_available():
                return torch.cuda.memory_allocated() / 1e6
        except Exception:  # noqa: BLE001
            pass
        return 0.0

    def sessions_root(self) -> Path:
        base = self.state_dir or Path.cwd() / "sam31_state"
        directory = base / "sessions"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def allocate_point_object_id(self) -> int:
        self.next_point_object_id += 1
        return self.next_point_object_id

    def _close_image_session(self) -> None:
        self.image_point_state.clear()
        self.image_concept_cache = None
        if self.image_session_id is not None and self.predictor is not None:
            try:
                self.predictor.handle_request(
                    {"type": "close_session", "session_id": self.image_session_id}
                )
            except Exception:  # noqa: BLE001
                logger.exception("closing image session failed")
        self.image_session_key = None
        self.image_session_id = None
        self.image_frame_dir = None

    def _close_video_session(self, token: str) -> None:
        session_id = self.video_sessions.pop(token, None)
        if session_id is not None and self.predictor is not None:
            try:
                self.predictor.handle_request({"type": "close_session", "session_id": session_id})
            except Exception:  # noqa: BLE001
                logger.exception("closing video session failed")

    def new_video_token(self, session_id: str) -> str:
        token = uuid.uuid4().hex
        self.video_sessions[token] = session_id
        return token


__all__ = ["Sam31WorkerRuntime", "WorkerError"]
