"""Prepare a single existing vector-create Command without editing the source."""

from __future__ import annotations

import math
from dataclasses import replace

from ..plugins.vector_tools.common import VectorCreateCommand, make_vector_object
from ..plugins.vector_tools.parametric_shapes import create_basic_shape_object
from .command_layer_access import _layer_transform
from .command_types import LayerTransform
from .document_capture_guard import is_document_capture_blocked
from .layer_command_commit_context import _LayerCommandContext
from .path_pattern_geometry import pattern_poses


class PathPatternService:
    def __init__(self, canvas, layer_id, object_id):
        self.canvas, self.layer_id, self.object_id = canvas, layer_id, object_id
        self.layer = canvas._get_layer_by_id(layer_id)
        if self.layer is None:
            raise ValueError("Missing source layer")
        self.scope = _LayerCommandContext(canvas, (self.layer,))
        self.source = next((o for o in self.layer.vector_objects if o.id == object_id), None)
        if self.source is None or len(self.source.paths) != 1:
            raise ValueError("Select one vector object with one path")
        self.source_geometry = self._geometry()
        self.check()

    def _geometry(self):
        return tuple(
            (p.closed, tuple((a.pos, a.h_in, a.h_out) for a in p.anchors))
            for p in self.source.paths
        )

    def check(self):
        if (
            not self.scope.current()
            or self.layer.locked
            or self.layer.layer_type != "vector"
            or _layer_transform(self.layer) != LayerTransform.identity()
            or is_document_capture_blocked(self.canvas)
            or getattr(self.canvas, "_in_transaction", False)
            or not any(o is self.source for o in self.layer.vector_objects)
            or self._geometry() != self.source_geometry
        ):
            raise ValueError("Source changed or cannot be edited")

    def prepare(
        self, *, shape="ellipse", size=12.0, spacing=32.0, follow=True, variation=0.0, seed=20261028
    ):
        self.check()
        if (
            shape not in ("ellipse", "rounded_rect", "polygon_3")
            or not math.isfinite(size)
            or size <= 0
        ):
            raise ValueError("Invalid pattern shape")
        path = self.source.paths[0]
        poses = pattern_poses(
            path.anchors,
            closed=path.closed,
            spacing=spacing,
            follow=follow,
            variation=variation,
            seed=seed,
        )
        stamp = create_basic_shape_object(shape, (0, 0), default_size_px=32)
        paths = []
        scale = size / 32
        for pose in poses:
            c, s = math.cos(pose.angle) * scale, math.sin(pose.angle) * scale

            def vector(point, c=c, s=s):
                return (c * point[0] - s * point[1], s * point[0] + c * point[1])

            for original in stamp.paths:
                anchors = []
                for anchor in original.anchors:
                    x, y = vector(anchor.pos)
                    anchors.append(
                        replace(
                            anchor,
                            pos=(pose.x + x, pose.y + y),
                            h_in=vector(anchor.h_in) if anchor.h_in else None,
                            h_out=vector(anchor.h_out) if anchor.h_out else None,
                        )
                    )
                paths.append(replace(original, anchors=anchors))
        result = make_vector_object(
            paths,
            self.source.style,
            meta={
                "path_pattern": {
                    "source_id": self.object_id,
                    "shape": shape,
                    "size": size,
                    "spacing": spacing,
                    "follow": follow,
                    "variation": variation,
                    "seed": seed,
                }
            },
        )
        self.check()
        return VectorCreateCommand(result, layer_id=self.layer_id)
