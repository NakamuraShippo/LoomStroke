"""Read-only contour snapshots from host canvas selections and raster alpha."""

from copy import deepcopy

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from ..command_layer_access import _layer_transform
from ..vector_text_render_primitives import TextRenderPrimitives
from .raster_clip import visible_snapshot


def _normalized(paths, margin):
    result = []
    for path in paths:
        points = [a["pos"] for a in path["anchors"]]
        if not 2 <= len(points) <= 8192:
            continue
        # Include control points so the editable frame encloses curved paths.
        controls = points + [
            (a["pos"][0] + a[k][0], a["pos"][1] + a[k][1])
            for a in path["anchors"]
            for k in ("h_in", "h_out")
            if k in a
        ]
        xs, ys = zip(*controls, strict=True)
        if max(xs) - min(xs) + max(ys) - min(ys) < 1e-6:
            continue
        x, y = min(xs) - margin, min(ys) - margin
        w, h = max(xs) - x + margin, max(ys) - y + margin
        normalized = deepcopy(path)
        for anchor in normalized["anchors"]:
            anchor["pos"] = [(anchor["pos"][0] - x) / w, (anchor["pos"][1] - y) / h]
            for key in ("h_in", "h_out"):
                if key in anchor:
                    anchor[key] = [anchor[key][0] / w, anchor[key][1] / h]
        result.append(((x, y, w, h), normalized))
    return result


def vector_contours(obj, *, margin=64):
    paths = []
    for path in obj.paths:
        anchors = []
        for anchor in path.anchors:
            data = {"pos": list(anchor.pos)}
            for name in ("h_in", "h_out"):
                value = getattr(anchor, name, None)
                if value is not None:
                    data[name] = list(value)
            anchors.append(data)
        paths.append(
            {"closed": TextRenderPrimitives._path_is_closed(path, path.anchors), "anchors": anchors}
        )
    return _normalized(paths, margin)


def _exposed_contours(points, canvas_size, tolerance):
    """Split a clipped contour at runs along the document's clipping edges."""
    if canvas_size is None:
        return [(True, points)]
    following = np.roll(points, -1, axis=0)
    clipped = np.zeros(len(points), dtype=bool)
    for axis, limits in enumerate(((0, canvas_size[0] - 1), (0, canvas_size[1] - 1))):
        for boundary in limits:
            clipped |= (np.abs(points[:, axis] - boundary) <= tolerance) & (
                np.abs(following[:, axis] - boundary) <= tolerance
            )
    if not clipped.any() or clipped.all():
        # An opaque full-page image still has its existing rectangular contour.
        return [(True, points)]
    first = (int(np.flatnonzero(clipped)[0]) + 1) % len(points)
    runs, run = [], []
    for step in range(len(points)):
        index = (first + step) % len(points)
        if clipped[index]:
            if len(run) >= 2:
                runs.append((False, np.array(run)))
            run = []
        else:
            if not run:
                run.append(points[index])
            run.append(following[index])
    return runs


def raster_contours(image, *, alpha=True, origin=(0, 0), affine=None, margin=64, canvas_size=None):
    if image is None or image.isNull():
        return []
    width, height = image.width(), image.height()
    visible = visible_snapshot(image, canvas_size, alpha=alpha, origin=origin, affine=affine)
    if visible is not None:
        if visible.isNull():
            return []
        image = visible
        width, height = canvas_size
        origin, affine = (0, 0), None
    # Bound tracing cost independently of a 4K/8K document. Smooth only the
    # copied contour; source pixels and the selection mask remain untouched.
    small = (
        image.scaled(
            1024,
            1024,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if max(image.width(), image.height()) > 1024
        else image
    )
    if small.format() == QImage.Format.Format_Alpha8:
        mask = np.frombuffer(small.constBits(), np.uint8).reshape(
            small.height(), small.bytesPerLine()
        )[:, : small.width()]
    elif alpha:
        small = small.convertToFormat(QImage.Format.Format_RGBA8888)
        array = np.frombuffer(small.constBits(), np.uint8).reshape(
            small.height(), small.bytesPerLine()
        )
        mask = array[:, : small.width() * 4].reshape(small.height(), small.width(), 4)[:, :, 3]
    else:
        small = small.convertToFormat(QImage.Format.Format_Grayscale8)
        mask = np.frombuffer(small.constBits(), np.uint8).reshape(
            small.height(), small.bytesPerLine()
        )[:, : small.width()]
    contours, _ = cv2.findContours(
        (mask >= 128).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    paths = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:128]:
        if cv2.contourArea(contour) < 2:
            continue
        simplified = cv2.approxPolyDP(contour, 0.75, True)[:, 0, :]
        if len(simplified) < 3:
            continue
        points = simplified.astype(float) * (width / small.width(), height / small.height())
        if affine is not None:
            points = np.array([affine.apply(float(x), float(y)) for x, y in points])
        else:
            points += origin
        tolerance = max(0.5, max(width / small.width(), height / small.height()) - 0.5)
        for closed, segment in _exposed_contours(points, canvas_size, tolerance):
            paths.append({"closed": closed, "anchors": [{"pos": p.tolist()} for p in segment]})
    paths = paths[:128]
    return _normalized(paths, margin)


def canvas_raster_contours(canvas, *, margin=64):
    get_size = getattr(canvas, "get_canvas_size", None)
    canvas_size = get_size() if callable(get_size) else None
    mask = canvas.get_selection_mask()
    if mask is not None and not mask.isNull():
        return raster_contours(mask, margin=margin, canvas_size=canvas_size)
    layer = canvas.active_layer
    if layer is None or layer.layer_type != "raster":
        return []
    transform = _layer_transform(layer).to_affine()
    return raster_contours(
        layer.image,
        origin=(layer.offset_x, layer.offset_y),
        affine=transform,
        margin=margin,
        canvas_size=canvas_size,
    )
