"""Convert a text contour to editable vector nodes and back to its snapshot."""

from copy import deepcopy

from PySide6.QtCore import QPointF

from loomstroke.core.japanese_text.typography_painter import text_rotation_transform
from loomstroke.core.path_text.metadata import path_settings
from loomstroke.plugins.vector_tools.common import Anchor, VectorObject, VectorPath, VectorStyle


def node_object(obj):
    payload = obj.meta["text_tool"]
    frame = payload["frame"]
    x, y, w, h = (float(frame[k]) for k in ("x", "y", "width", "height"))
    rotation = text_rotation_transform(payload)
    path = payload["path_text"]["path"]
    anchors = []
    for data in path["anchors"]:
        pos = QPointF(x + data["pos"][0] * w, y + data["pos"][1] * h)
        mapped = rotation.map(pos)
        handles = {}
        for key in ("h_in", "h_out"):
            if data.get(key) is not None:
                delta = data[key]
                end = rotation.map(pos + QPointF(delta[0] * w, delta[1] * h)) - mapped
                handles[key] = (end.x(), end.y())
        anchors.append(
            Anchor(
                (mapped.x(), mapped.y()),
                kind=data.get("kind", "smooth" if handles else "corner"),
                **handles,
            )
        )
    return VectorObject(
        id=obj.id,
        paths=[VectorPath(anchors=anchors, closed=bool(path.get("closed")))],
        style=VectorStyle(stroke_color=(66, 186, 255), stroke_width=1),
        meta={},
    )


def replace_nodes(obj, edited):
    if len(edited.paths) != 1:
        return None
    path = edited.paths[0]
    if not (3 if path.closed else 2) <= len(path.anchors) <= 8192:
        return None
    result = deepcopy(obj)
    payload = result.meta["text_tool"]
    frame = payload["frame"]
    x, y, w, h = (float(frame[k]) for k in ("x", "y", "width", "height"))
    inverse, valid = text_rotation_transform(payload).inverted()
    if not valid or w <= 0 or h <= 0:
        return None
    anchors = []
    for anchor in path.anchors:
        point = QPointF(*anchor.pos)
        pos = inverse.map(point)
        data = {"pos": [(pos.x() - x) / w, (pos.y() - y) / h], "kind": anchor.kind}
        for key in ("h_in", "h_out"):
            delta = getattr(anchor, key)
            if delta is not None:
                offset = inverse.map(point + QPointF(*delta)) - pos
                data[key] = [offset.x() / w, offset.y() / h]
        anchors.append(data)
    settings = payload["path_text"]
    if "handle_source_path" in settings or "handle_smoothness" in settings:
        settings.pop("handle_source_path", None)
        settings["handle_smoothness"] = 0.0
    settings["path"] = {**settings["path"], "anchors": anchors, "closed": bool(path.closed)}
    if not path.closed:
        settings["full_loop"] = False
    return result if path_settings(payload) is not None else None
