"""Detached vector/text copies using existing vector geometry and metadata contracts."""

import math
from copy import deepcopy
from dataclasses import replace

from loomstroke.core.drawing_assist.symmetry_transforms import symmetry_transforms
from loomstroke.core.path_text.metadata import path_settings

from ..vector_tools.common import Anchor, VectorPath, make_vector_object


def object_copies(obj, session):
    if session is None or not session.has_copies:
        return (obj,)
    result = []
    for index, matrix in enumerate(symmetry_transforms(session)):
        if session.include_original and index == 0:
            result.append(obj)
            continue
        result.append(transformed_copy(obj, matrix))
    return tuple(result)


def transformed_copy(obj, matrix):
    if isinstance(obj.meta.get("text_tool"), dict):
        return _text_copy(obj, matrix)
    paths = []

    def vector(handle):
        if handle is None:
            return None
        return (
            matrix.a * handle[0] + matrix.b * handle[1],
            matrix.c * handle[0] + matrix.d * handle[1],
        )

    for path in obj.paths:
        anchors = [
            Anchor(matrix.apply(*a.pos), a.kind, vector(a.h_in), vector(a.h_out))
            for a in path.anchors
        ]
        paths.append(VectorPath(anchors, closed=path.closed))
    meta = deepcopy(obj.meta)
    meta.pop("loomstroke:ids", None)
    parametric = meta.get("shape_manager_parametric")
    if isinstance(parametric, dict):
        from ..vector_tools.parametric_shapes import is_parametric_shape  # noqa: PLC0415

        # Existing parametric handles regenerate axis-aligned shapes. Keep the
        # transformed copy editable by nodes without regenerating its old orientation.
        if is_parametric_shape(obj):
            parametric["editable"] = False
        bbox = parametric.get("reference_bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            points = [matrix.apply(x, y) for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])]
            parametric["reference_bbox"] = [
                min(p[0] for p in points),
                min(p[1] for p in points),
                max(p[0] for p in points),
                max(p[1] for p in points),
            ]
    return make_vector_object(paths, obj.style, meta)


def _text_copy(obj, matrix):
    from ..text_tool.common import extract_text_content, extract_text_payload  # noqa: PLC0415
    from ..text_tool.editing_metadata import replace_edited_text  # noqa: PLC0415

    payload = extract_text_payload(obj)
    frame = payload.get("frame")
    frame = frame if isinstance(frame, dict) else payload
    x, y, w, h = (
        float(frame.get(k, payload.get(k, default)))
        for k, default in (("x", 0), ("y", 0), ("width", 1), ("height", 1))
    )
    transform = payload.get("transform")
    transform = transform if isinstance(transform, dict) else {}
    angle = float(transform.get("rotation_deg", payload.get("rotation_deg", 0)))
    cx, cy = matrix.apply(x + w / 2, y + h / 2)
    direction = math.degrees(math.atan2(matrix.c, matrix.a))
    reflected = matrix.a * matrix.d - matrix.b * matrix.c < 0
    path = path_settings(payload)
    if not reflected:
        angle += direction
    elif path is not None:
        # Factor the reflection into a local X flip and a frame rotation.
        angle = direction + 180 - angle
    result = replace_edited_text(
        obj,
        extract_text_content(payload),
        rect=(cx - w / 2, cy - h / 2, w, h),
        rotation_deg=angle % 360,
    )
    if reflected and path is not None:
        _reflect_text_path(result.meta["text_tool"]["path_text"])
    result.meta.pop("loomstroke:ids", None)
    return replace(result, id=make_vector_object([], obj.style).id)


def _reflect_text_path(settings):
    """Reflect the detached frame-relative contour and its signed normal offset."""
    for anchor in settings["path"]["anchors"]:
        x, y = anchor["pos"]
        anchor["pos"] = [1.0 - float(x), y]
        for key in ("h_in", "h_out"):
            handle = anchor.get(key)
            if handle is not None:
                anchor[key] = [-float(handle[0]), handle[1]]
    settings["offset"] = -float(settings.get("offset", 0))


def preview_object(obj, session):
    copies = object_copies(obj, session)
    if len(copies) == 1 and copies[0] is obj:
        return obj
    return replace(obj, paths=[path for item in copies for path in item.paths])
