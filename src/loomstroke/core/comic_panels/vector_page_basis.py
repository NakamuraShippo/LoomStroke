"""Keep Qt frame coverage stable while editable vector coordinates follow the page."""

import copy
import math
from collections import OrderedDict
from dataclasses import replace

from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D

from .canvas_transform_basis import _multiply, mapped_rect, page_matrix, transform_mask
from .membership import PART_KEY
from .models import PanelFrameError

BASIS_KEY = "vector_page_basis"


def validate_basis(basis):
    if not isinstance(basis, dict) or basis.get("version") != 1:
        raise PanelFrameError("unsupported frame vector page basis")
    size, values = basis.get("page_size"), basis.get("matrix")
    if (
        not isinstance(size, (list, tuple))
        or len(size) != 2
        or any(type(v) is not int or v <= 0 for v in size)
        or not isinstance(values, (list, tuple))
        or len(values) != 6
        or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
    ):
        raise PanelFrameError("invalid frame vector page basis")
    _mode(Affine2D(*values))


def transformed_basis(metadata, matrix, page_size):
    result = copy.deepcopy(metadata)
    marker = result[PART_KEY]
    basis = marker.get(BASIS_KEY)
    if basis:
        validate_basis(basis)
        matrix = _multiply(matrix, Affine2D(*basis["matrix"]))
        page_size = basis["page_size"]
    marker[BASIS_KEY] = {
        "version": 1,
        "page_size": list(page_size),
        "matrix": [matrix.a, matrix.b, matrix.c, matrix.d, matrix.tx, matrix.ty],
    }
    validate_basis(marker[BASIS_KEY])
    return result


def _source_layer(layer, basis, preview=None):
    matrix = Affine2D(*basis["matrix"])
    inverse = matrix.inverse()
    if inverse is None:
        raise PanelFrameError("frame vector page basis is singular")
    source = copy.copy(layer)
    source.metadata = copy.deepcopy(layer.metadata)
    source.metadata[PART_KEY].pop(BASIS_KEY)

    def point(p):
        return inverse.apply(*p) if p is not None else None

    def handle(p):
        return (
            (inverse.a * p[0] + inverse.b * p[1], inverse.c * p[0] + inverse.d * p[1])
            if p is not None
            else None
        )

    objects = [
        preview if preview is not None and obj.id == preview.id else obj
        for obj in layer.vector_objects
    ]
    if preview is not None and not any(obj.id == preview.id for obj in objects):
        objects.append(preview)
    source.vector_objects = [
        replace(
            obj,
            paths=[
                replace(
                    path,
                    anchors=[
                        replace(a, pos=point(a.pos), h_in=handle(a.h_in), h_out=handle(a.h_out))
                        for a in path.anchors
                    ],
                )
                for path in obj.paths
            ],
        )
        for obj in objects
    ]
    return source, matrix


def _mode(matrix):
    linear = (matrix.a, matrix.b, matrix.c, matrix.d)
    for mode in ("flip_h", "flip_v", "flip_hv", "rot90", "rot-90"):
        candidate = page_matrix(mode, 1, 1)
        if linear == (candidate.a, candidate.b, candidate.c, candidate.d):
            return mode
    if linear == (1, 0, 0, 1):
        return None
    if linear == (0, 1, 1, 0):
        return "transpose"
    if linear == (0, -1, -1, 0):
        return "anti_transpose"
    raise PanelFrameError("frame vector page basis is not an orthogonal transform")


def rasterize_in_page_basis(canvas, layer, *, scale=1, region=None, for_render=False):
    basis = (getattr(layer, "metadata", None) or {}).get(PART_KEY, {}).get(BASIS_KEY)
    if not basis:
        return None
    validate_basis(basis)
    from loomstroke.core.snapshot_vector_rasterizer import SnapshotVectorRasterizer  # noqa: PLC0415
    from loomstroke.core.vector_text_render_service import VectorRasterResult  # noqa: PLC0415

    from .vector_basis_raster_result import FrameVectorRasterResult  # noqa: PLC0415

    cache = getattr(canvas, "_frame_vector_basis_cache", None)
    if cache is None:
        cache = canvas._frame_vector_basis_cache = OrderedDict()
    signature = (layer.texture_version, scale, region, for_render, repr(basis))
    preview = getattr(canvas, "_vector_preview", None)
    preview_layer = getattr(canvas, "_vector_preview_layer_id", None)
    if preview_layer is not None and preview_layer != layer.id:
        preview = None
    cached = cache.get(layer.id)
    if preview is None and cached and cached[0] == signature:
        cache.move_to_end(layer.id)
        return cached[1]
    source, matrix = _source_layer(layer, basis, preview)
    rasterizer = SnapshotVectorRasterizer(width=basis["page_size"][0], height=basis["page_size"][1])
    rasterizer._vector_raster_cache = {}
    if for_render:
        raw = rasterizer._vector_text_render().rasterize_vector_layer_for_render(
            source, scale=scale
        )
        if isinstance(raw, VectorRasterResult):
            image, actual_scale = raw.image, raw.scale
            source_rect = (
                raw.origin_x,
                raw.origin_y,
                image.width() / actual_scale,
                image.height() / actual_scale,
            )
        else:
            image, actual_scale = raw, scale
            source_rect = (0, 0, image.width() / scale, image.height() / scale)
    else:
        source_rect = (
            mapped_rect(region, matrix.inverse()) if region else (0, 0, *basis["page_size"])
        )
        source_rect = tuple(map(int, source_rect))
        image = rasterizer._vector_text_render().rasterize_vector_layer(
            source, scale=scale, region=source_rect
        )
        actual_scale = scale
    if for_render:
        render_transform = LayerTransform.from_affine(
            _multiply(layer.transform.to_affine(), matrix)
        )
        result = FrameVectorRasterResult(
            image, actual_scale, int(source_rect[0]), int(source_rect[1]), render_transform
        )
    else:
        mode = _mode(matrix)
        image = transform_mask(image, mode) if mode else image
        result = image
    if preview is None:
        _remember(cache, layer.id, signature, result, image)
    return result


def _remember(cache, layer_id, signature, result, image):
    budget = 128 * 1024 * 1024
    if image.sizeInBytes() <= budget:
        cache[layer_id] = signature, result
        cache.move_to_end(layer_id)
    else:
        cache.pop(layer_id, None)

    def bytes_used():
        return sum(getattr(value[1], "image", value[1]).sizeInBytes() for value in cache.values())

    # Bound both work and retained sources, including parts removed from
    # the layer tree. Never traverse the whole tree per vector raster.
    while len(cache) > 32 or bytes_used() > budget:
        cache.popitem(last=False)
