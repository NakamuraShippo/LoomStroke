"""Software 3D preview renderer used by the dock viewer and projection import."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import cast

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF

from loomstroke.core.assets_3d.material import MaterialAsset
from loomstroke.core.assets_3d.mesh import MeshPrimitive
from loomstroke.core.assets_3d.scene import Scene3DDocument
from loomstroke.gpu.runtime import GpuRuntime

from .cameras import OrbitCameraState
from .display_modes import PreviewDisplayMode
from .lighting import normalized_light_direction
from .maps import MapOutputBundle

DEFAULT_MAX_PREVIEW_FACES = 25_000


@dataclass(frozen=True, slots=True)
class PreviewRenderResult:
    """Rendered preview plus diagnostic/fallback information."""

    image: QImage
    maps: MapOutputBundle
    display_mode: PreviewDisplayMode
    backend_name: str
    fallbacks: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _DrawStats:
    draw_calls: int
    total_faces: int
    face_sample_stride: int


@dataclass(frozen=True, slots=True)
class _FaceCollection:
    faces: list[tuple[float, int, MeshPrimitive, np.ndarray, np.ndarray, np.ndarray]]
    total_faces: int
    face_sample_stride: int


class SoftwarePreviewRenderer:
    """Deterministic preview renderer with optional common runtime awareness."""

    backend_name = "software-3d-preview"

    def __init__(self, *, runtime: GpuRuntime | None = None) -> None:
        self.runtime = runtime

    def render(
        self,
        scene: Scene3DDocument,
        *,
        width: int = 512,
        height: int = 512,
        camera: OrbitCameraState | None = None,
        display_mode: PreviewDisplayMode = PreviewDisplayMode.TEXTURED,
        max_faces: int | None = DEFAULT_MAX_PREVIEW_FACES,
    ) -> PreviewRenderResult:
        width = max(1, int(width))
        height = max(1, int(height))
        max_faces = _normalize_max_faces(max_faces)
        camera = camera or OrbitCameraState.fit_to_scene(scene)
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        depth = QImage(width, height, QImage.Format.Format_RGBA8888)
        normal = QImage(width, height, QImage.Format.Format_RGBA8888)
        segmentation = QImage(width, height, QImage.Format.Format_RGBA8888)
        outline = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(QColor(28, 30, 34, 255))
        depth.fill(QColor(0, 0, 0, 255))
        normal.fill(QColor(128, 128, 255, 255))
        segmentation.fill(QColor(0, 0, 0, 255))
        outline.fill(QColor(0, 0, 0, 255))

        painter = QPainter(image)
        depth_painter = QPainter(depth)
        normal_painter = QPainter(normal)
        segmentation_painter = QPainter(segmentation)
        outline_painter = QPainter(outline)
        try:
            fallbacks: list[str] = []
            draw_stats = self._draw_scene(
                scene,
                painters=(
                    painter,
                    depth_painter,
                    normal_painter,
                    segmentation_painter,
                    outline_painter,
                ),
                width=width,
                height=height,
                camera=camera,
                display_mode=display_mode,
                max_faces=max_faces,
                fallbacks=fallbacks,
            )
        finally:
            painter.end()
            depth_painter.end()
            normal_painter.end()
            segmentation_painter.end()
            outline_painter.end()

        return PreviewRenderResult(
            image=image,
            maps=MapOutputBundle(
                depth=depth,
                normal=normal,
                segmentation=segmentation,
                outline=outline,
            ),
            display_mode=display_mode,
            backend_name=self.backend_name,
            fallbacks=tuple(dict.fromkeys(fallbacks)),
            metadata={
                "draw_calls": draw_stats.draw_calls,
                "total_faces": draw_stats.total_faces,
                "face_sample_stride": draw_stats.face_sample_stride,
                "max_faces": max_faces,
                "width": width,
                "height": height,
            },
        )

    def _draw_scene(
        self,
        scene: Scene3DDocument,
        *,
        painters: tuple[QPainter, QPainter, QPainter, QPainter, QPainter],
        width: int,
        height: int,
        camera: OrbitCameraState,
        display_mode: PreviewDisplayMode,
        max_faces: int | None,
        fallbacks: list[str],
    ) -> _DrawStats:
        color_painter, depth_painter, normal_painter, segmentation_painter, outline_painter = (
            painters
        )
        rotation = camera.rotation_matrix()
        target = np.asarray(camera.target, dtype=np.float32)
        low, high = scene.bounds()
        radius = float(np.linalg.norm(high - low) * 0.5)
        if radius <= 1e-6:
            radius = 1.0
        scale = min(width, height) * 0.42 * camera.zoom / radius
        collection = self._collect_projected_faces(
            scene,
            rotation=rotation,
            target=target,
            scale=scale,
            width=width,
            height=height,
            max_faces=max_faces,
            fallbacks=fallbacks,
        )

        light_dir = normalized_light_direction((-0.4, -0.7, -1.0))
        draw_calls = 0
        sorted_faces = sorted(collection.faces, key=lambda item: item[0])
        for z, face_index, primitive, projected, camera_vertices, triangles in sorted_faces:
            face = triangles[face_index]
            points = [QPointF(float(projected[i, 0]), float(projected[i, 1])) for i in face]
            polygon = QPolygonF(points)
            normal_vector = self._face_normal(camera_vertices[face])
            material = scene.material_by_id(primitive.material_id)
            fill = self._face_color(
                material,
                normal_vector=normal_vector,
                light_dir=light_dir,
                display_mode=display_mode,
                fallbacks=fallbacks,
            )
            if display_mode == PreviewDisplayMode.WIREFRAME:
                color_painter.setBrush(Qt.BrushStyle.NoBrush)
                color_painter.setPen(QPen(QColor(230, 235, 245, 255), 1.0))
                color_painter.drawPolygon(polygon)
            else:
                color_painter.setPen(QPen(QColor(20, 22, 26, 180), 1.0))
                color_painter.setBrush(fill)
                color_painter.drawPolygon(polygon)

            normalized_depth = 255 - ((z + camera.distance) / (camera.distance * 2.0)) * 255
            depth_value = int(max(0, min(255, normalized_depth)))
            depth_painter.setPen(Qt.PenStyle.NoPen)
            depth_painter.setBrush(QColor(depth_value, depth_value, depth_value, 255))
            depth_painter.drawPolygon(polygon)

            normal_color = self._normal_color(normal_vector)
            normal_painter.setPen(Qt.PenStyle.NoPen)
            normal_painter.setBrush(normal_color)
            normal_painter.drawPolygon(polygon)

            segment = int(max(1, min(255, primitive.segment_id)))
            segmentation_painter.setPen(Qt.PenStyle.NoPen)
            segmentation_painter.setBrush(QColor(segment, 0, 255 - segment, 255))
            segmentation_painter.drawPolygon(polygon)

            outline_painter.setBrush(Qt.BrushStyle.NoBrush)
            outline_painter.setPen(QPen(QColor(255, 255, 255, 255), 1.0))
            outline_painter.drawPolygon(polygon)
            draw_calls += 1
        return _DrawStats(
            draw_calls=draw_calls,
            total_faces=collection.total_faces,
            face_sample_stride=collection.face_sample_stride,
        )

    def _collect_projected_faces(
        self,
        scene: Scene3DDocument,
        *,
        rotation: np.ndarray,
        target: np.ndarray,
        scale: float,
        width: int,
        height: int,
        max_faces: int | None,
        fallbacks: list[str],
    ) -> _FaceCollection:
        primitives = [
            primitive
            for mesh in scene.meshes
            for primitive in mesh.primitives
            if primitive.indices.size >= 3
        ]
        total_faces = sum(int(primitive.indices.size // 3) for primitive in primitives)
        face_sample_stride = 1
        if max_faces is not None and total_faces > max_faces:
            face_sample_stride = max(1, math.ceil(total_faces / max_faces))
            fallbacks.append("preview_face_sampling")

        faces: list[tuple[float, int, MeshPrimitive, np.ndarray, np.ndarray, np.ndarray]] = []
        global_face_offset = 0
        for primitive in primitives:
            projected, camera_vertices = self._project_vertices(
                primitive.vertices,
                rotation=rotation,
                target=target,
                scale=scale,
                width=width,
                height=height,
            )
            triangles = primitive.indices.reshape((-1, 3))
            first_face = (-global_face_offset) % face_sample_stride
            for face_index in range(first_face, len(triangles), face_sample_stride):
                face = triangles[face_index]
                z = float(camera_vertices[face, 2].mean())
                faces.append((z, face_index, primitive, projected, camera_vertices, triangles))
            global_face_offset += len(triangles)
        return _FaceCollection(
            faces=faces,
            total_faces=total_faces,
            face_sample_stride=face_sample_stride,
        )

    @staticmethod
    def _project_vertices(
        vertices: np.ndarray,
        *,
        rotation: np.ndarray,
        target: np.ndarray,
        scale: float,
        width: int,
        height: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        camera_vertices = (vertices - target) @ rotation.T
        projected = np.empty((len(vertices), 2), dtype=np.float32)
        projected[:, 0] = width * 0.5 + camera_vertices[:, 0] * scale
        projected[:, 1] = height * 0.5 - camera_vertices[:, 1] * scale
        return projected, camera_vertices

    @staticmethod
    def _face_normal(points: np.ndarray) -> np.ndarray:
        edge_a = points[1] - points[0]
        edge_b = points[2] - points[0]
        normal = np.cross(edge_a, edge_b)
        length = float(np.linalg.norm(normal))
        if length <= 1e-6:
            return np.asarray((0.0, 0.0, 1.0), dtype=np.float32)
        return cast(np.ndarray, normal / length)

    def _face_color(
        self,
        material: MaterialAsset | None,
        *,
        normal_vector: np.ndarray,
        light_dir: np.ndarray,
        display_mode: PreviewDisplayMode,
        fallbacks: list[str],
    ) -> QColor:
        if display_mode == PreviewDisplayMode.NORMAL_DEBUG:
            if material is None or material.normal_texture_id is None:
                fallbacks.append("missing_normal_map")
            return self._normal_color(normal_vector)

        if material is None:
            fallbacks.append("missing_material")
            base = (0.72, 0.72, 0.72, 1.0)
        else:
            base = material.base_color
            if (
                display_mode == PreviewDisplayMode.TEXTURED
                and material.base_color_texture_id is None
            ):
                fallbacks.append("missing_texture")

        shade = max(0.28, min(1.0, float(np.dot(normal_vector, -light_dir)) * 0.75 + 0.35))
        return QColor(
            int(max(0, min(255, base[0] * 255 * shade))),
            int(max(0, min(255, base[1] * 255 * shade))),
            int(max(0, min(255, base[2] * 255 * shade))),
            int(max(0, min(255, base[3] * 255))),
        )

    @staticmethod
    def _normal_color(normal_vector: np.ndarray) -> QColor:
        encoded = ((normal_vector + 1.0) * 0.5 * 255.0).clip(0, 255)
        return QColor(int(encoded[0]), int(encoded[1]), int(encoded[2]), 255)


def _normalize_max_faces(value: int | None) -> int | None:
    if value is None:
        return None
    max_faces = int(value)
    if max_faces <= 0:
        return None
    return max_faces
