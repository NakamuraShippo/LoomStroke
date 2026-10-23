"""Fit editable text contours with the precise pen's existing Bezier math."""

import math
from copy import deepcopy

import numpy as np

from loomstroke.core.path_text.geometry import PathTrack
from loomstroke.core.path_text.metadata import path_settings
from loomstroke.plugins.vector_tools.common import Anchor
from loomstroke.plugins.vector_tools.curve_fitting import CurveFitting


def fit_text_handles(obj, value):
    """Always fit from a retained baseline; zero restores its exact nodes."""
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return obj
    if not math.isfinite(value) or not 0 <= value <= 5:
        return obj
    result = deepcopy(obj)
    payload = result.meta["text_tool"]
    settings = payload["path_text"]
    source = settings.get("handle_source_path", settings["path"])
    candidate = {**payload, "path_text": {**settings, "path": source, "smoothing": 0}}
    if path_settings(candidate) is None:
        source = settings["path"]
        candidate["path_text"]["path"] = source
    if value == 0:
        settings["path"] = source
        settings.pop("handle_source_path", None)
        settings["handle_smoothness"] = 0.0
        return result
    anchors = _fitted_anchors(candidate, value)
    if anchors is None:
        return obj
    w, h = (float(payload["frame"][key]) for key in ("width", "height"))
    settings["path"] = {
        **source,
        "anchors": [
            {
                "pos": [a.pos[0] / w, a.pos[1] / h],
                "kind": a.kind,
                "h_in": [a.h_in[0] / w, a.h_in[1] / h] if a.h_in is not None else None,
                "h_out": [a.h_out[0] / w, a.h_out[1] / h] if a.h_out is not None else None,
            }
            for a in anchors
        ],
    }
    settings["handle_source_path"] = source
    settings["handle_smoothness"] = value
    return result


def _fitted_anchors(payload, value):
    fitter = CurveFitting()
    source = payload["path_text"]["path"]
    closed = bool(source.get("closed"))
    w, h = (float(payload["frame"][key]) for key in ("width", "height"))
    if w <= 0 or h <= 0:
        return None
    if value <= 0.1:
        # The precise pen's low-strength branch retains anchors and adjusts
        # their handles. Work directly in frame-local pixels to avoid thousands
        # of Qt objects and repeated metadata copies during a slider gesture.
        anchors = [
            Anchor(
                (a["pos"][0] * w, a["pos"][1] * h),
                kind=a.get("kind", "corner"),
                h_in=(a["h_in"][0] * w, a["h_in"][1] * h) if a.get("h_in") else None,
                h_out=(a["h_out"][0] * w, a["h_out"][1] * h) if a.get("h_out") else None,
            )
            for a in source["anchors"]
        ]
        updated = list(anchors)
        count = len(anchors)
        for i, anchor in enumerate(anchors):
            if not closed and i in (0, count - 1):
                continue
            hin, hout = fitter._compute_smooth_handles(
                anchors[(i - 1) % count].pos,
                anchor.pos,
                anchors[(i + 1) % count].pos,
                strength=value,
            )
            updated[i] = Anchor(anchor.pos, kind="smooth", h_in=hin, h_out=hout)
        return updated
    # Sample curves and raw raster contours at equal arc-length intervals.
    # Bound fitting work independently of source anchor count; display-only
    # contour smoothing never feeds into the actual editable handles.
    track = PathTrack.from_payload(payload)
    if track is None:
        return None
    count = min(512, max(64, math.ceil(track.length / 2)))
    distances = np.linspace(0, track.length, count + 1)
    points = np.column_stack(
        [np.interp(distances, track.distances, track.points[:, axis]) for axis in (0, 1)]
    )
    positions = [(float(px), float(py)) for px, py in points]
    if closed:
        positions[-1] = positions[0]
    anchors = fitter._fit_curve_to_points(positions, value, closed)
    if closed:
        anchors = anchors[:-1]
        if len(anchors) < 3:
            # A tiny closed contour still needs three editable nodes.
            positions = [positions[i] for i in (0, count // 3, 2 * count // 3)]
            anchors = []
            for i, pos in enumerate(positions):
                hin, hout = fitter._compute_smooth_handles(
                    positions[(i - 1) % 3],
                    pos,
                    positions[(i + 1) % 3],
                    strength=1,
                )
                anchors.append(Anchor(pos, kind="smooth", h_in=hin, h_out=hout))
    return anchors
