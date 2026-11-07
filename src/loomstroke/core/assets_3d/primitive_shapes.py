"""Small built-in meshes for sketching with editable 3D layers."""

from __future__ import annotations

import math

import numpy as np

from .material import MaterialAsset
from .mesh import MeshAsset, MeshPrimitive, create_unit_cube_mesh
from .scene import Scene3DDocument, Scene3DNode

PRIMITIVE_KINDS = ("cube", "sphere", "cylinder")
PRIMITIVE_NAMES = {"cube": "Cube", "sphere": "Sphere", "cylinder": "Cylinder"}


def create_primitive_scene(kind: str) -> Scene3DDocument:
    """Build a centered unit-size scene with no external asset dependency."""
    if kind not in PRIMITIVE_KINDS:
        raise ValueError(f"Unsupported 3D primitive: {kind}")
    material = MaterialAsset(
        id="primitive_material",
        name="Clay",
        base_color=(0.64, 0.72, 0.86, 1.0),
    )
    mesh = {
        "cube": lambda: create_unit_cube_mesh(material_id=material.id),
        "sphere": lambda: _sphere_mesh(material.id),
        "cylinder": lambda: _cylinder_mesh(material.id),
    }[kind]()
    node = Scene3DNode(id="primitive_node", name=PRIMITIVE_NAMES[kind], mesh_id=mesh.id)
    return Scene3DDocument(
        id=f"primitive_{kind}",
        nodes=(node,),
        meshes=(mesh,),
        materials=(material,),
        root_node_ids=(node.id,),
    )


def _sphere_mesh(material_id: str, *, segments: int = 24, stacks: int = 12) -> MeshAsset:
    vertices: list[tuple[float, float, float]] = [(0.0, 0.5, 0.0)]
    for ring in range(1, stacks):
        polar = math.pi * ring / stacks
        y = 0.5 * math.cos(polar)
        radius = 0.5 * math.sin(polar)
        for segment in range(segments):
            angle = 2.0 * math.pi * segment / segments
            vertices.append((radius * math.cos(angle), y, radius * math.sin(angle)))
    bottom = len(vertices)
    vertices.append((0.0, -0.5, 0.0))

    indices: list[tuple[int, int, int]] = []
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        indices.append((0, 1 + next_segment, 1 + segment))
        for ring in range(stacks - 2):
            upper = 1 + ring * segments
            lower = upper + segments
            indices.append((upper + segment, upper + next_segment, lower + segment))
            indices.append((lower + segment, upper + next_segment, lower + next_segment))
        last = 1 + (stacks - 2) * segments
        indices.append((bottom, last + segment, last + next_segment))

    positions = np.asarray(vertices, dtype=np.float32)
    primitive = MeshPrimitive(
        vertices=positions,
        indices=np.asarray(indices, dtype=np.uint32),
        normals=positions * 2.0,
        material_id=material_id,
    )
    return MeshAsset(id="sphere", name="Unit Sphere", primitives=(primitive,))


def _cylinder_mesh(material_id: str, *, segments: int = 32) -> MeshAsset:
    vertices: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    circle = [
        (math.cos(2.0 * math.pi * segment / segments), math.sin(2.0 * math.pi * segment / segments))
        for segment in range(segments)
    ]
    for y in (0.5, -0.5):
        for x, z in circle:
            vertices.append((0.5 * x, y, 0.5 * z))
            normals.append((x, 0.0, z))
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        indices.append((segment, next_segment, segments + segment))
        indices.append((segments + segment, next_segment, segments + next_segment))

    for y, normal in ((0.5, (0.0, 1.0, 0.0)), (-0.5, (0.0, -1.0, 0.0))):
        center = len(vertices)
        vertices.append((0.0, y, 0.0))
        normals.append(normal)
        ring_start = len(vertices)
        for x, z in circle:
            vertices.append((0.5 * x, y, 0.5 * z))
            normals.append(normal)
        for segment in range(segments):
            current = ring_start + segment
            next_vertex = ring_start + (segment + 1) % segments
            indices.append(
                (center, next_vertex, current) if y > 0 else (center, current, next_vertex)
            )

    primitive = MeshPrimitive(
        vertices=np.asarray(vertices, dtype=np.float32),
        indices=np.asarray(indices, dtype=np.uint32),
        normals=np.asarray(normals, dtype=np.float32),
        material_id=material_id,
    )
    return MeshAsset(id="cylinder", name="Unit Cylinder", primitives=(primitive,))
