"""SAM 3.1 backend for AI Select: a drop-in ``AiSelectionWorkerClient`` implementation.

The migration-period adapter (Phase D): it satisfies the legacy async worker
client protocol, so the provider, selection run/refine flows, candidate
system, apply adapters, and MCP all work unchanged. When the legacy SAM3
backend is deleted (next phase) this bridge becomes the only production
client behind the provider.

Session semantics (multiplex non-overlap containment):
- Every ``segment`` call is an INDEPENDENT selection — point prompts always
  reset the worker session, so earlier selections can never suppress a new
  click through the multiplex non-overlap constraint.
- ``refine`` keeps continuity: the candidate's worker object id (recorded at
  segment time) is reused so positive/negative points refine the SAME
  object. If continuity is lost (app restart, image changed), refine reseeds
  the object and restores the selected mask and point history before applying
  the new points, with a warning identifying the recovery.
- Worker object ids never leave this module: candidates carry regular
  LoomStroke candidate/mask-asset ids, and the object mapping lives only in
  the in-memory registry.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from loomstroke.ai_selection.data_model import (
    BlockedReason,
    MaskCandidate,
    MaskCandidateSet,
    Sam3Prompt,
    Sam3Readiness,
    Sam3RefineRequest,
    Sam3SegmentRequest,
    Sam3Warning,
)
from loomstroke.ai_selection.worker_client import AiSelectionWorkerUnavailable

from . import worker_protocol as protocol
from .prompt_validation import bridge_capabilities, prompt_text, validate_prompt
from .refine_recovery import recover_refine, saved_history, saved_text
from .runtime_config import Sam31RuntimeConfig
from .server_manager import WORKER_RUNNING, Sam31ServerError, Sam31ServerManager

logger = logging.getLogger("loomstroke.sam31.bridge")

RUNTIME_NAME = "sam3_1"
_MIN_INSTANCE_AREA_PX = 16
_SETUP_ACTION = "Open Preferences > SAM 3.1 and run Setup."

_BLOCKED_MESSAGES: dict[str, str] = {
    "ROOT_NOT_CONFIGURED": "SAM 3.1 is not set up (no root folder is configured).",
    "ROOT_INVALID": "The configured SAM 3.1 root folder does not exist.",
    "SETUP_REQUIRED": "SAM 3.1 is not set up on this machine.",
    "PYTHON_UNSUPPORTED": "The SAM 3.1 runtime needs Python 3.12 or newer.",
    "CHECKPOINT_MISSING": "The SAM 3.1 checkpoint was not found in the root folder.",
}


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "req"
    return cleaned[:48]


class Sam31BridgeWorkerClient:
    """Implements the legacy ``AiSelectionWorkerClient`` protocol over the SAM31 worker."""

    def __init__(
        self,
        *,
        manager: Sam31ServerManager,
        config: Sam31RuntimeConfig,
        asset_store: Any,
    ) -> None:
        self._manager = manager
        self._config = config
        self._asset_store = asset_store
        # mask_asset_id -> (absolute image path, worker object id)
        self._object_registry: dict[str, tuple[str, int]] = {}
        self._point_history: dict[str, tuple[tuple[float, float, int], ...]] = {}
        self._session_tokens: dict[str, str] = {}

    # ── protocol: readiness / lifecycle ────────────────────────────────

    async def get_readiness(self) -> Sam3Readiness:
        return await asyncio.to_thread(self._readiness_sync)

    async def segment(self, request: Sam3SegmentRequest) -> MaskCandidateSet:
        return await asyncio.to_thread(self._segment_sync, request)

    async def refine(self, request: Sam3RefineRequest) -> MaskCandidateSet:
        return await asyncio.to_thread(self._refine_sync, request)

    async def cancel(self, job_id: str) -> bool:  # noqa: ARG002
        return False  # The shared worker has no per-request cancellation command.

    async def close(self) -> None:
        # The worker is shared and owned by Sam31ServerManager; releasing a
        # bridge must never stop it. Only the refine continuity is dropped.
        self._object_registry.clear()
        self._point_history.clear()
        self._session_tokens.clear()

    def terminate_sync(self) -> None:
        """Legacy tool teardown hook — intentionally a no-op (shared worker)."""

    # ── readiness mapping ──────────────────────────────────────────────

    def _readiness_sync(self) -> Sam3Readiness:
        readiness = self._manager.readiness(self._config)
        if readiness.ready:
            return Sam3Readiness(
                state="ready",
                runtime=RUNTIME_NAME,
                diagnostics={**readiness.diagnostics, "capabilities": bridge_capabilities()},
            )
        return self.blocked_readiness(readiness.blocked_reasons, readiness.diagnostics)

    @staticmethod
    def blocked_readiness(
        codes: tuple[str, ...], diagnostics: dict[str, Any] | None = None
    ) -> Sam3Readiness:
        reasons = tuple(
            BlockedReason(
                code=f"sam31_{code.lower()}",
                message=_BLOCKED_MESSAGES.get(code, f"SAM 3.1 runtime issue: {code}"),
                action=_SETUP_ACTION,
            )
            for code in (codes or ("SETUP_REQUIRED",))
        )
        return Sam3Readiness(
            state="blocked",
            blocked_reasons=reasons,
            recommended_actions=(_SETUP_ACTION,),
            runtime=RUNTIME_NAME,
            diagnostics={**(diagnostics or {}), "capabilities": bridge_capabilities()},
        )

    def _unavailable(
        self, codes: tuple[str, ...], detail: str = ""
    ) -> AiSelectionWorkerUnavailable:
        readiness = self.blocked_readiness(codes)
        message = detail or "; ".join(r.message for r in readiness.blocked_reasons)
        return AiSelectionWorkerUnavailable(message, readiness=readiness)

    # ── worker/model orchestration ─────────────────────────────────────

    def _ensure_worker_and_model(self) -> None:
        if self._manager.worker_state() != WORKER_RUNNING:
            try:
                self._manager.start(self._config)
            except Sam31ServerError as exc:
                raise self._unavailable((exc.code,), str(exc)) from exc
        try:
            state = self._manager.model_status().get("modelState")
            if state != protocol.MODEL_LOADED:
                # Lazy load is allowed here: running AI Select is an explicit
                # user request for SAM 3.1 inference.
                self._manager.load_model()
        except Sam31ServerError as exc:
            code = "OOM" if exc.code == protocol.ERR_OOM else "MODEL_LOAD_FAILED"
            raise self._unavailable((code,), str(exc)) from exc

    def _segment_command(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._manager.segment(command, params)
        except Sam31ServerError as exc:
            code = "OOM" if exc.code == protocol.ERR_OOM else exc.code
            raise self._unavailable((code,), str(exc)) from exc

    # ── prompt translation ─────────────────────────────────────────────

    @staticmethod
    def _rel_points(
        prompt: Sam3Prompt, width: int, height: int
    ) -> tuple[list[list[float]], list[int]]:
        points: list[list[float]] = []
        labels: list[int] = []
        for point in prompt.points:
            points.append([point.x / max(1, width), point.y / max(1, height)])
            labels.append(1 if point.label == "positive" else 0)
        return points, labels

    @staticmethod
    def _rel_box(prompt: Sam3Prompt, width: int, height: int) -> tuple[list[float], bool]:
        box = prompt.boxes[0]
        rel = [
            box.x / max(1, width),
            box.y / max(1, height),
            box.width / max(1, width),
            box.height / max(1, height),
        ]
        return rel, getattr(box, "label", "positive") != "negative"

    # ── segment ────────────────────────────────────────────────────────

    def _segment_sync(self, request: Sam3SegmentRequest) -> MaskCandidateSet:
        prompt = request.prompt
        mode, prompt_warnings = validate_prompt(prompt)
        text = prompt_text(prompt)
        self._ensure_worker_and_model()
        image_path = self._source_image_path(request.source_ref)
        width = int(request.source_ref.width)
        height = int(request.source_ref.height)
        output_dir = self._worker_tmp_dir(request.request_id)
        warnings = list(prompt_warnings)
        try:
            base = {"imagePath": str(image_path), "outputDir": str(output_dir)}
            if mode == "text":
                result = self._segment_command(
                    protocol.CMD_SEGMENT_TEXT,
                    {
                        **base,
                        "prompt": text,
                        "probThresh": self._prob_thresh(request),
                    },
                )
            elif mode in ("point", "click") or (mode == "mixed" and prompt.points):
                points, labels = self._rel_points(prompt, width, height)
                # Independent selection: always a fresh worker session so the
                # multiplex non-overlap constraint never leaks across clicks.
                result = self._segment_command(
                    protocol.CMD_SEGMENT_POINT,
                    {
                        **base,
                        "points": points,
                        "labels": labels,
                        "resetSession": True,
                        "returnAlternatives": not bool(text),
                        "autoConcepts": not bool(text),
                        **(
                            {"prompt": text, "probThresh": self._prob_thresh(request)}
                            if text
                            else {}
                        ),
                    },
                )
            elif mode in ("box", "exemplar") or (mode == "mixed" and prompt.boxes):
                rel_box, positive = self._rel_box(prompt, width, height)
                command = (
                    protocol.CMD_SEGMENT_EXEMPLAR
                    if mode == "exemplar"
                    else protocol.CMD_SEGMENT_BOX
                )
                params = {
                    **base,
                    "boxXywh": rel_box,
                    "positive": positive,
                    "probThresh": self._prob_thresh(request),
                }
                if len(prompt.boxes) > 1:
                    params["boxesXywh"] = [
                        [box.x / width, box.y / height, box.width / width, box.height / height]
                        for box in prompt.boxes
                    ]
                    params["positives"] = [box.label != "negative" for box in prompt.boxes]
                if text:
                    params["prompt"] = text
                result = self._segment_command(command, params)

            candidates = self._materialize_candidates(
                result,
                request=request,
                image_path=str(image_path),
                prefix=f"sam31-seg-{_safe_id(request.request_id)}",
                warnings=warnings,
            )
            return MaskCandidateSet(
                set_id=f"sam31-seg-set-{_safe_id(request.request_id)}",
                job_id=f"sam31-seg-{_safe_id(request.request_id)}",
                prompt=prompt,
                source_ref=request.source_ref,
                candidates=tuple(candidates),
                warnings=tuple(warnings),
                diagnostics={"backend": RUNTIME_NAME},
            )
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    # ── refine ─────────────────────────────────────────────────────────

    def _refine_sync(self, request: Sam3RefineRequest) -> MaskCandidateSet:
        _mode, prompt_warnings = validate_prompt(request.prompt, refine=True)
        self._ensure_worker_and_model()
        image_path = self._source_image_path(request.source_ref)
        width = int(request.source_ref.width)
        height = int(request.source_ref.height)
        points, labels = self._rel_points(request.prompt, width, height)
        output_dir = self._worker_tmp_dir(request.request_id)
        warnings = list(prompt_warnings)
        try:
            registry_entry = self._object_registry.get(request.base_mask_asset_id)
            base = {"imagePath": str(image_path), "outputDir": str(output_dir)}
            try:
                base_asset = self._asset_store.load_mask_asset(request.base_mask_asset_id)
            except (OSError, ValueError):
                base_asset = None
            base_history = self._point_history.get(request.base_mask_asset_id, ())
            if not base_history and base_asset is not None:
                base_history = saved_history(base_asset)
            recovered = False
            if (
                base_asset is not None
                and registry_entry is not None
                and registry_entry[0] == str(image_path)
            ):
                # Same object, same session: positive/negative points refine
                # the existing worker object.
                try:
                    result = self._segment_command(
                        protocol.CMD_REFINE_MASK,
                        {
                            **base,
                            "objectId": registry_entry[1],
                            "points": points,
                            "labels": labels,
                            "baseMaskPath": str(self._asset_store.mask_asset_path(base_asset)),
                            "baseMaskBbox": list(base_asset.bbox),
                            "basePoints": [[x, y] for x, y, _label in base_history],
                            "baseLabels": [label for _x, _y, label in base_history],
                            **(
                                {
                                    "imageSessionToken": self._session_tokens[
                                        request.base_mask_asset_id
                                    ]
                                }
                                if request.base_mask_asset_id in self._session_tokens
                                else {}
                            ),
                        },
                    )
                except AiSelectionWorkerUnavailable as exc:
                    codes = (
                        {reason.code for reason in exc.readiness.blocked_reasons}
                        if exc.readiness
                        else set()
                    )
                    if not codes.intersection({"sam31_session_unknown", "sam31_object_unknown"}):
                        raise
                    recovered = True
            else:
                recovered = True
            if recovered:
                # Continuity lost: reseed the object, then restore the caller's
                # selected mask and history before applying the new points.
                warnings.append(
                    Sam3Warning(
                        code="sam31_refine_fallback_new_object",
                        message="Refine continuity was unavailable; started a new point selection.",
                    )
                )
                text = prompt_text(request.prompt) or (
                    saved_text(base_asset) if base_asset is not None else ""
                )
                if base_asset is not None:
                    # Production managers protect the seed/refine pair against
                    # other shared-worker commands; legacy test doubles may omit it.
                    transaction = getattr(self._manager, "image_transaction", nullcontext)
                    with transaction():
                        result = recover_refine(
                            self._segment_command,
                            base=base,
                            asset=base_asset,
                            store=self._asset_store,
                            history=base_history,
                            points=points,
                            labels=labels,
                            text=text,
                            width=width,
                            height=height,
                        )
                else:
                    result = self._segment_command(
                        protocol.CMD_SEGMENT_POINT,
                        {
                            **base,
                            "points": points,
                            "labels": labels,
                            "resetSession": True,
                            **({"prompt": text} if text else {}),
                        },
                    )
            candidates = self._materialize_candidates(
                result,
                request=request,
                image_path=str(image_path),
                prefix=f"sam31-refine-{_safe_id(request.request_id)}",
                warnings=warnings,
                primary_object_id=result.get("primaryObjectId"),
            )
            return MaskCandidateSet(
                set_id=f"sam31-refine-set-{_safe_id(request.request_id)}",
                job_id=f"sam31-refine-{_safe_id(request.request_id)}",
                prompt=request.prompt,
                source_ref=request.source_ref,
                candidates=tuple(candidates),
                warnings=tuple(warnings),
                diagnostics={"backend": RUNTIME_NAME, "refine": True},
            )
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    # ── materialization (worker PNG -> MaskAsset + MaskCandidate) ──────

    def _remember_session_token(self, asset_id: str, result: dict[str, Any]) -> None:
        token = result.get("imageSessionToken")
        if isinstance(token, str):
            self._session_tokens[asset_id] = token

    def _materialize_candidates(  # noqa: PLR0912
        self,
        result: dict[str, Any],
        *,
        request: Any,
        image_path: str,
        prefix: str,
        warnings: list[Sam3Warning],
        primary_object_id: int | None = None,
    ) -> list[MaskCandidate]:
        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        entries = list(result.get("objects") or [])
        if primary_object_id is not None:
            entries = [
                entry for entry in entries if entry.get("objectId") == primary_object_id
            ] or entries
        min_score = float(getattr(request, "min_score", 0.0) or 0.0)
        split_enabled = bool(getattr(request, "split_disconnected_instances", False))

        raw: list[tuple[dict[str, Any], Any, tuple[int, int, int, int]]] = []
        for entry in entries:
            score = entry.get("score")
            if score is not None and float(score) < min_score:
                continue
            mask_path = entry.get("maskPath")
            if not mask_path or not Path(mask_path).is_file():
                continue
            full = np.asarray(Image.open(mask_path).convert("L"))
            # Different scopes of one clicked object are not separate instances.
            components = self._components(full, split_enabled and not entry.get("pointVariant"), np)
            for component_mask, bbox in components:
                raw.append((entry, component_mask, bbox))

        max_candidates = int(getattr(request, "max_candidates", 16) or 16)
        if len(raw) > max_candidates:
            warnings.append(
                Sam3Warning(
                    code="instance_split_candidates_capped",
                    message="The candidate limit was reached; additional instances were omitted.",
                )
            )
            keep = (
                list(range(max_candidates))
                if any(entry.get("pointVariant") for entry in entries)
                else sorted(
                    range(len(raw)),
                    key=lambda i: int(raw[i][1].sum()),
                    reverse=True,
                )[:max_candidates]
            )
            raw = [raw[i] for i in sorted(keep)]

        candidates: list[MaskCandidate] = []
        for index, (entry, cropped, bbox) in enumerate(raw):
            candidate_id = f"{prefix}-cand-{index}"
            asset_id = f"{prefix}-mask-{index}-{uuid.uuid4().hex[:6]}"
            _x, _y, w, h = bbox
            buffer = io.BytesIO()
            Image.fromarray((cropped > 0).astype(np.uint8) * 255, mode="L").save(
                buffer, format="PNG"
            )
            base_id = getattr(request, "base_mask_asset_id", "")
            previous = self._point_history.get(base_id, ())
            previous_text = ""
            if base_id:
                try:
                    previous_asset = self._asset_store.load_mask_asset(base_id)
                    previous = previous or saved_history(previous_asset)
                    previous_text = saved_text(previous_asset)
                except (OSError, ValueError) as exc:
                    logger.debug("SAM31 refine base metadata unavailable (%s)", type(exc).__name__)
            points, labels = self._rel_points(
                request.prompt, request.source_ref.width, request.source_ref.height
            )
            history = {(x, y): label for x, y, label in previous}
            history.update({(x, y): label for (x, y), label in zip(points, labels, strict=True)})
            point_history = tuple((x, y, label) for (x, y), label in history.items())
            self._asset_store.save_mask_asset(
                buffer.getvalue(),
                asset_id=asset_id,
                kind="alpha_mask",
                width=w,
                height=h,
                bbox=bbox,
                source_candidate_id=candidate_id,
                metadata={
                    "backend": RUNTIME_NAME,
                    "sam31Refine": {
                        "points": point_history,
                        "text": prompt_text(request.prompt)
                        or previous_text
                        or entry.get("semanticLabel")
                        or "",
                    },
                },
            )
            worker_object_id = entry.get("objectId")
            if worker_object_id is not None:
                self._object_registry[asset_id] = (image_path, int(worker_object_id))
                self._point_history[asset_id] = point_history
                self._remember_session_token(asset_id, result)
            candidates.append(
                MaskCandidate(
                    candidate_id=candidate_id,
                    source_image_id=request.source_ref.snapshot_id,
                    prompt=request.prompt,
                    label=request.prompt.text or entry.get("semanticLabel") or request.prompt.mode,
                    score=(float(entry["score"]) if entry.get("score") is not None else None),
                    bbox=bbox,
                    area_px=int((cropped > 0).sum()),
                    mask_asset_id=asset_id,
                    instance_index=index,
                    provenance={
                        "backend": RUNTIME_NAME,
                        "workerObjectScope": "session-internal",
                        **(
                            {"semanticLabel": entry["semanticLabel"]}
                            if entry.get("semanticLabel")
                            else {}
                        ),
                        **(
                            {"pointVariant": entry["pointVariant"]}
                            if entry.get("pointVariant")
                            else {}
                        ),
                    },
                )
            )
        return candidates

    @staticmethod
    def _components(full_mask: Any, split_enabled: bool, np: Any) -> list[Any]:
        ys, xs = np.nonzero(full_mask)
        if len(xs) == 0:
            return []
        whole_bbox = (
            int(xs.min()),
            int(ys.min()),
            int(xs.max() - xs.min() + 1),
            int(ys.max() - ys.min() + 1),
        )
        x0, y0, bw, bh = whole_bbox
        whole_cropped = full_mask[y0 : y0 + bh, x0 : x0 + bw]
        if not split_enabled:
            return [(whole_cropped, whole_bbox)]
        try:
            from loomstroke.ai_selection.instance_split import (  # noqa: PLC0415
                split_mask_components,
            )

            components = split_mask_components(full_mask, min_area_px=_MIN_INSTANCE_AREA_PX)
        except Exception:  # noqa: BLE001 - cv2 unavailable etc.: keep whole mask
            return [(whole_cropped, whole_bbox)]
        if not components:
            return [(whole_cropped, whole_bbox)]
        # SplitComponent.mask_u8 is already cropped to SplitComponent.bbox,
        # and the bbox is in source-image coordinates.
        return [(np.asarray(component.mask_u8), tuple(component.bbox)) for component in components]

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _prob_thresh(request: Sam3SegmentRequest) -> float:
        return min(0.95, max(0.05, float(request.min_score or 0.2)))

    def _source_image_path(self, source_ref: Any) -> Path:
        path = self._asset_store.source_snapshot_path(source_ref)
        if path is None or not Path(path).is_file():
            raise self._unavailable(("SETUP_REQUIRED",), "source snapshot image is unavailable")
        return Path(path)

    def _worker_tmp_dir(self, request_id: str) -> Path:
        root = Path(self._asset_store.root_path) / "worker_tmp"
        directory = root / f"sam31-{_safe_id(request_id)}-{uuid.uuid4().hex[:6]}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory


__all__ = ["RUNTIME_NAME", "Sam31BridgeWorkerClient"]
