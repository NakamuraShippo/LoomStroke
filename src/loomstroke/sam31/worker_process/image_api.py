"""Still-image segmentation over 1-frame multiplex sessions.

Every image command shares one active predictor session per source image
(keyed by path + mtime + size, so edited files re-open). Object IDs in
responses are worker-session-scoped and must be mapped by the caller to its
own candidate/entity IDs — they are not persistent.
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from loomstroke.sam31 import worker_protocol as protocol

from .image_session_identity import image_session_token, reset_image_session
from .runtime import Sam31WorkerRuntime, WorkerError


def _point_inputs(request: dict[str, Any]) -> Any:
    from .point_mask_policy import parse_points  # noqa: PLC0415

    try:
        return parse_points(request.get("points"), request.get("labels"))
    except (TypeError, ValueError) as exc:
        raise WorkerError(protocol.ERR_INVALID_REQUEST, str(exc)) from exc


def _validated_output_dir(request: dict[str, Any]) -> Path:
    raw = request.get("outputDir")
    if not raw:
        raise WorkerError(protocol.ERR_OUTPUT_DIR_INVALID, "outputDir is required")
    directory = Path(raw)
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise WorkerError(protocol.ERR_OUTPUT_DIR_INVALID, f"outputDir is not a directory: {raw}")
    return directory


def _image_key(image_path: Path) -> tuple[str, int, int]:
    stat = image_path.stat()
    return (str(image_path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def _ensure_image_session(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> str:
    predictor = runtime.require_predictor()
    raw = request.get("imagePath")
    if not raw:
        raise WorkerError(protocol.ERR_INVALID_REQUEST, "imagePath is required")
    image_path = Path(raw)
    if not image_path.is_file():
        raise WorkerError(protocol.ERR_IMAGE_UNREADABLE, f"image not found: {raw}")
    key = _image_key(image_path)
    if runtime.image_session_key == key and runtime.image_session_id is not None:
        return runtime.image_session_id

    runtime._close_image_session()
    frame_dir = runtime.sessions_root() / uuid.uuid4().hex[:12]
    frame_dir.mkdir(parents=True)
    shutil.copy2(image_path, frame_dir / f"0{image_path.suffix or '.png'}")
    response = predictor.handle_request({"type": "start_session", "resource_path": str(frame_dir)})
    runtime.image_session_key = key
    runtime.image_session_id = response["session_id"]
    runtime.image_frame_dir = frame_dir
    return runtime.image_session_id


def _write_outputs(
    runtime: Sam31WorkerRuntime,
    outputs: dict[str, Any],
    output_dir: Path,
    *,
    primary_object_id: int | None = None,
) -> dict[str, Any]:
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    masks = np.asarray(outputs.get("out_binary_masks"))
    object_ids = [int(v) for v in np.asarray(outputs.get("out_obj_ids")).reshape(-1)]
    probs = [float(v) for v in np.asarray(outputs.get("out_probs")).reshape(-1)]
    boxes = np.asarray(outputs.get("out_boxes_xywh")).reshape(-1, 4)

    entries: list[dict[str, Any]] = []
    for index, object_id in enumerate(object_ids):
        mask = masks[index]
        mask_name = f"sam31_{uuid.uuid4().hex[:12]}.png"
        mask_path = output_dir / mask_name
        Image.fromarray((mask.astype(np.uint8)) * 255, mode="L").save(mask_path)
        entries.append(
            {
                "objectId": object_id,
                "maskPath": str(mask_path),
                "score": probs[index] if index < len(probs) else 0.0,
                "bboxXywh": [float(v) for v in boxes[index]],
                "areaPx": int(mask.sum()),
            }
        )
        if "out_point_variants" in outputs:
            entries[-1]["pointVariant"] = outputs["out_point_variants"][index]
        if "out_semantic_labels" in outputs:
            label = outputs["out_semantic_labels"][index]
            if label:
                entries[-1]["semanticLabel"] = label
    result: dict[str, Any] = {
        "objects": entries,
        "maskEncoding": "grayscale_coverage_png",
        "imageSessionToken": image_session_token(runtime),
    }
    if primary_object_id is not None:
        result["primaryObjectId"] = primary_object_id
    return result


def _add_prompt(runtime: Sam31WorkerRuntime, session_id: str, **prompt: Any) -> dict[str, Any]:
    predictor = runtime.require_predictor()
    request = {
        "type": "add_prompt",
        "session_id": session_id,
        "frame_index": 0,
    }
    request.update(prompt)
    try:
        response = predictor.handle_request(request)
    except Exception as exc:  # noqa: BLE001
        if "out of memory" in str(exc).lower():
            raise WorkerError(protocol.ERR_OOM, str(exc)) from exc
        raise WorkerError(protocol.ERR_INTERNAL, f"{type(exc).__name__}: {exc}") from exc
    outputs = response.get("outputs")
    if outputs is None:
        raise WorkerError(protocol.ERR_INTERNAL, "predictor returned no outputs")
    return outputs


def segment_text(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> dict[str, Any]:
    from .point_mask_policy import remember_outputs  # noqa: PLC0415

    output_dir = _validated_output_dir(request)
    prompt = request.get("prompt")
    if not prompt or not isinstance(prompt, str):
        raise WorkerError(protocol.ERR_INVALID_REQUEST, "prompt (text) is required")
    session_id = _ensure_image_session(runtime, request)
    reset_image_session(runtime, session_id)
    runtime.image_point_state.clear()
    outputs = _add_prompt(
        runtime,
        session_id,
        text=prompt,
        output_prob_thresh=float(request.get("probThresh", 0.5)),
    )
    remember_outputs(runtime.image_point_state, outputs)
    return _write_outputs(runtime, outputs, output_dir)


def segment_point(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> dict[str, Any]:
    from .point_alternatives import capture_alternatives, expand_outputs  # noqa: PLC0415
    from .point_mask_policy import filter_concepts, remember_outputs  # noqa: PLC0415

    prompt_points = _point_inputs(request)
    output_dir = _validated_output_dir(request)
    points = request.get("points")
    labels = request.get("labels")
    if not points or labels is None or len(points) != len(labels):
        raise WorkerError(
            protocol.ERR_INVALID_REQUEST,
            "points and labels of equal length are required",
        )
    session_id = _ensure_image_session(runtime, request)

    if request.get("prompt") and request.get("objectId") is None:
        reset_image_session(runtime, session_id)
        runtime.image_point_state.clear()
        outputs = _add_prompt(
            runtime,
            session_id,
            text=str(request["prompt"]),
            output_prob_thresh=float(request.get("probThresh", 0.5)),
        )
        outputs = filter_concepts(outputs, prompt_points)
        remember_outputs(runtime.image_point_state, outputs, prompt_points)
        ids = outputs["out_obj_ids"]
        return _write_outputs(
            runtime,
            outputs,
            output_dir,
            primary_object_id=int(ids[0]) if len(ids) == 1 else None,
        )

    concepts = []
    if (
        request.get("autoConcepts")
        and not request.get("prompt")
        and request.get("objectId") is None
        and len(prompt_points) == 1
        and prompt_points[0][2] == 1
    ):
        from .auto_concepts import discover  # noqa: PLC0415

        concepts = discover(runtime, session_id, _add_prompt)
        reset_image_session(runtime, session_id)
        runtime.image_point_state.clear()

    object_id = request.get("objectId")
    if object_id is None:
        self_capabilities = runtime.capabilities()["features"]
        if self_capabilities[protocol.FEATURE_POINT_NEW_OBJECT] == protocol.FEATURE_UNAVAILABLE:
            raise WorkerError(
                protocol.ERR_FEATURE_UNAVAILABLE,
                "point new-object selection is unavailable on this sam3 version",
            )
        object_id = runtime.allocate_point_object_id()
        if bool(request.get("resetSession", False)):
            runtime.image_point_state.clear()
            reset_image_session(runtime, session_id)
    capture = nullcontext([])
    if (
        request.get("returnAlternatives")
        and request.get("objectId") is None
        and len(prompt_points) == 1
        and prompt_points[0][2] == 1
    ):
        from PIL import Image  # noqa: PLC0415

        with Image.open(request["imagePath"]) as source:
            shape = (source.height, source.width)
        capture = capture_alternatives(runtime.require_predictor(), shape)
    with capture as alternatives:
        outputs = _add_prompt(
            runtime,
            session_id,
            points=[[float(x), float(y)] for x, y in points],
            point_labels=[int(v) for v in labels],
            obj_id=int(object_id),
        )
    remember_outputs(runtime.image_point_state, outputs, prompt_points)
    outputs = expand_outputs(outputs, alternatives, prompt_points, int(object_id))
    if concepts:
        from .auto_concepts import prepend_candidates  # noqa: PLC0415

        outputs = prepend_candidates(outputs, concepts, prompt_points[0], int(object_id))
    if outputs.get("out_point_variants"):
        # All scope candidates share one tracker ID. Cache the cleaned preferred
        # scope only; the bridge supplies any explicitly chosen alternative asset.
        remember_outputs(
            runtime.image_point_state,
            {
                "out_obj_ids": outputs["out_obj_ids"][:1],
                "out_binary_masks": outputs["out_binary_masks"][:1],
            },
            prompt_points,
        )
    return _write_outputs(runtime, outputs, output_dir, primary_object_id=int(object_id))


def _segment_box_like(
    runtime: Sam31WorkerRuntime, request: dict[str, Any], *, kind: str
) -> dict[str, Any]:
    from .point_mask_policy import remember_outputs  # noqa: PLC0415

    output_dir = _validated_output_dir(request)
    from .box_inputs import boxes_and_labels  # noqa: PLC0415

    boxes, labels = boxes_and_labels(request)
    session_id = _ensure_image_session(runtime, request)
    if bool(request.get("resetSession", True)):
        runtime.image_point_state.clear()
        reset_image_session(runtime, session_id)
    prompt: dict[str, Any] = {
        "bounding_boxes": boxes,
        "bounding_box_labels": labels,
        "output_prob_thresh": float(request.get("probThresh", 0.5)),
    }
    text = request.get("prompt")
    if text:
        prompt["text"] = str(text)
    outputs = _add_prompt(runtime, session_id, **prompt)
    remember_outputs(runtime.image_point_state, outputs)
    result = _write_outputs(runtime, outputs, output_dir)
    result["promptKind"] = kind
    return result


def segment_box(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> dict[str, Any]:
    return _segment_box_like(runtime, request, kind="box")


def segment_exemplar(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> dict[str, Any]:
    """Concept detection from a visual exemplar box (future cross-image use)."""
    return _segment_box_like(runtime, request, kind="exemplar")


def refine_mask(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> dict[str, Any]:
    from .point_mask_policy import (  # noqa: PLC0415
        load_base_mask,
        merge_points,
        parse_points,
        remember_outputs,
        stabilize_outputs,
    )

    prompt_points = _point_inputs(request)
    output_dir = _validated_output_dir(request)
    object_id = request.get("objectId")
    if object_id is None:
        raise WorkerError(protocol.ERR_INVALID_REQUEST, "objectId is required")
    points = request.get("points")
    labels = request.get("labels")
    if not points or labels is None or len(points) != len(labels):
        raise WorkerError(
            protocol.ERR_INVALID_REQUEST,
            "refine_mask requires points/labels (upstream boxes are concept-level"
            " prompts and cannot refine a single object)",
        )
    session_id = _ensure_image_session(runtime, request)
    expected_token = request.get("imageSessionToken")
    if expected_token is not None and expected_token != image_session_token(runtime):
        raise WorkerError(
            protocol.ERR_SESSION_UNKNOWN, "The saved image object session is no longer current."
        )
    previous = runtime.image_point_state.get(int(object_id))
    if previous is None:
        raise WorkerError(protocol.ERR_SESSION_UNKNOWN, "image refinement state is unavailable")
    if request.get("baseMaskPath"):
        try:
            base_points = request.get("basePoints", [])
            previous = load_base_mask(
                request["baseMaskPath"],
                request.get("baseMaskBbox", []),
                previous.mask.shape,
                parse_points(base_points, request.get("baseLabels", [])) if base_points else (),
            )
        except (OSError, TypeError, ValueError) as exc:
            raise WorkerError(protocol.ERR_INVALID_REQUEST, str(exc)) from exc
    model_points = merge_points(previous.points, prompt_points)
    outputs = _add_prompt(
        runtime,
        session_id,
        points=[[x, y] for x, y, _label in model_points],
        point_labels=[label for _x, _y, label in model_points],
        obj_id=int(object_id),
    )
    try:
        outputs = stabilize_outputs(outputs, previous, int(object_id), prompt_points)
    except ValueError as exc:
        raise WorkerError(protocol.ERR_INTERNAL, str(exc)) from exc
    # The supplied asset may be an older candidate: replace its cached history too.
    runtime.image_point_state[int(object_id)] = previous
    remember_outputs(runtime.image_point_state, outputs, prompt_points)
    return _write_outputs(runtime, outputs, output_dir, primary_object_id=int(object_id))


HANDLERS = {
    protocol.CMD_SEGMENT_TEXT: segment_text,
    protocol.CMD_SEGMENT_POINT: segment_point,
    protocol.CMD_SEGMENT_BOX: segment_box,
    protocol.CMD_SEGMENT_EXEMPLAR: segment_exemplar,
    protocol.CMD_REFINE_MASK: refine_mask,
}

__all__ = ["HANDLERS"]
