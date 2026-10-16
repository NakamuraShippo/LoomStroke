"""Scale managed frame edit records along with the existing Image Size transaction."""

from __future__ import annotations

import copy
import math
from dataclasses import replace

from .membership import PANEL_KEY, PART_KEY
from .metadata import read_frame_layer, write_frame_layer
from .models import PanelFrameError

LAYOUT_NAMESPACE = "loomstroke.comictools.panel_layout"


def _scaled_pair(value, factor):
    if len(value) != 2 or not all(math.isfinite(v) for v in value):
        raise PanelFrameError("invalid frame source coordinates")
    return [v * factor for v in value]


def scale_frame_metadata(metadata, factor):
    """Return a detached copy, retaining identities, source references and extensions."""
    if PART_KEY not in metadata and PANEL_KEY not in metadata:
        return None
    if not math.isfinite(factor) or factor <= 0:
        raise PanelFrameError("invalid frame resample factor")
    result = copy.deepcopy(metadata)
    marker = result.get(PART_KEY)
    if marker is not None:
        if marker.get("version") != 1:
            raise PanelFrameError("unsupported frame part version")
        for name in ("source_image_origin", "paint_basis_origin"):
            if name in marker:
                marker[name] = _scaled_pair(marker[name], factor)
        if "window_width" in marker:
            marker["window_width"] *= factor
        from .vector_page_basis import BASIS_KEY, validate_basis  # noqa: PLC0415

        basis = marker.get(BASIS_KEY)
        if basis:
            validate_basis(basis)
            basis["page_size"] = [max(1, math.floor(v * factor + 0.5)) for v in basis["page_size"]]
            basis["matrix"][4:] = [v * factor for v in basis["matrix"][4:]]
    record = result.get(PANEL_KEY)
    frame = read_frame_layer(record.get("metadata")) if record else None
    if frame is None:
        return result
    changed = replace(
        frame,
        edges=tuple(
            replace(
                edge,
                raster_anchor=(
                    replace(
                        edge.raster_anchor,
                        source_xy_px=tuple(_scaled_pair(edge.raster_anchor.source_xy_px, factor)),
                        base_edge_length_px=edge.raster_anchor.base_edge_length_px * factor,
                    )
                    if edge.raster_anchor
                    else None
                ),
            )
            for edge in frame.edges
        ),
        clip_inset_px=frame.clip_inset_px * factor,
        region_version=frame.region_version + 1,
        clip_mask_region_version=frame.clip_mask_region_version + 1,
    )
    record["metadata"] = write_frame_layer(record["metadata"], changed)
    payload = record["metadata"]["frame_layer"]
    _scale_restoration_records(payload, factor)
    return result


def _scale_restoration_records(payload, factor):
    for edge in (*payload["edges"], *payload.get("retired_edges", ())):
        if "restoration_rect" in edge:
            rect = edge["restoration_rect"]
            if len(rect) != 4 or not all(math.isfinite(v) for v in rect):
                raise PanelFrameError("invalid vector restoration rectangle")
            edge["restoration_rect"] = [v * factor for v in rect]
        for name in ("restoration_endpoints", "restoration_fragment_endpoints"):
            if name in edge:
                edge[name] = [_scaled_pair(p, factor) for p in edge[name]]
        if edge in payload.get("retired_edges", ()) and edge.get("raster_anchor"):
            anchor = edge["raster_anchor"]
            anchor["source_xy_px"] = _scaled_pair(anchor["source_xy_px"], factor)
            anchor["base_edge_length_px"] *= factor


def validate_prepared_resample(canvas, prepared, sx, sy):
    """Reject missing plugin hooks before any image, mask or history changes."""
    managed = any((layer.metadata or {}).get(PART_KEY) for layer in canvas._flatten_layers())
    if not managed:
        return
    if not math.isclose(sx, sy, rel_tol=1e-12, abs_tol=0):
        raise PanelFrameError("managed frames require uniform Image Size scaling")
    found_layout = False
    for layer, metadata, objects in prepared:
        before = layer.metadata or {}
        expected = scale_frame_metadata(before, sx)
        if expected is not None and metadata != expected:
            raise PanelFrameError("frame part resample hook is unavailable")
        layout = (before.get("plugins") or {}).get(LAYOUT_NAMESPACE)
        if layout is not None:
            found_layout = True
            changed = ((metadata or {}).get("plugins") or {}).get(LAYOUT_NAMESPACE)
            if changed is None or any(
                not math.isclose(changed.get(k, -1), layout.get(k, 0) * sx)
                for k in ("gutter_px", "margin_px")
            ):
                raise PanelFrameError("frame layout resample hook is unavailable")
        for original, transformed in zip(layer.vector_objects, objects, strict=True):
            expected = scale_frame_metadata(original.meta, sx)
            if (
                expected
                and PANEL_KEY in expected
                and transformed.meta.get(PANEL_KEY) != expected[PANEL_KEY]
            ):
                raise PanelFrameError("frame carrier resample hook is unavailable")
    if not found_layout:
        raise PanelFrameError("managed frame layout is missing")


def frame_mask_mode(layer, mode):
    """Ownership is a categorical pixel partition, never interpolated coverage."""
    from loomstroke.core.image_size.interpolation import InterpolationMode  # noqa: PLC0415

    marker = (layer.metadata or {}).get(PART_KEY, {})
    return (
        InterpolationMode.NEAREST
        if marker.get("nearest_mask") or marker.get("span_window")
        else mode
    )
