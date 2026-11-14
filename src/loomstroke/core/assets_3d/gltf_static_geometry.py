"""Convert a glTF scene's static node placement to the flat mesh geometry LoomStroke renders."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np

from .mesh import MeshAsset, MeshPrimitive
from .scene import Scene3DNode

_IDENTITY = np.eye(4)
_TRS_FIELDS = ("translation", "rotation", "scale")


def bake_static_geometry(
    raw: dict[str, Any], meshes: list[MeshAsset]
) -> tuple[tuple[Scene3DNode, ...], tuple[MeshAsset, ...]]:
    """Apply the selected scene's world transforms without duplicating mesh payloads."""
    raw_nodes = raw.get("nodes", [])
    if not isinstance(raw_nodes, list) or any(not isinstance(node, dict) for node in raw_nodes):
        raise ValueError("invalid glTF nodes")
    if not raw_nodes:
        nodes = tuple(
            Scene3DNode(id=f"node_{index}", name=mesh.name, mesh_id=mesh.id)
            for index, mesh in enumerate(meshes)
        )
        return nodes, tuple(meshes)

    roots = _root_indices(raw, raw_nodes)
    pending = [(index, _IDENTITY) for index in reversed(roots)]
    visited: set[int] = set()
    placements: dict[int, np.ndarray] = {}
    nodes = []
    while pending:
        index, parent = pending.pop()
        if index in visited:
            raise ValueError("glTF node hierarchy contains a cycle or repeated child")
        visited.add(index)
        node = raw_nodes[index]
        with np.errstate(over="ignore", invalid="ignore"):
            world = parent @ _node_transform(node)
        if not np.isfinite(world).all():
            raise ValueError("glTF node world transform is not finite")
        if "mesh" in node:
            nodes.append(_place_mesh_node(node, index, world, meshes, placements))
        children = _indices(node.get("children", []), len(raw_nodes), "child")
        pending.extend((child, world) for child in reversed(children))

    if not raw.get("scenes") and len(visited) != len(raw_nodes):
        raise ValueError("glTF node hierarchy contains a cycle")
    if not nodes:
        raise ValueError("glTF scene has no static mesh")
    placed_meshes = tuple(
        _bake_mesh(mesh, placements[index])
        for index, mesh in enumerate(meshes)
        if index in placements
    )
    return tuple(nodes), placed_meshes


def _root_indices(raw: dict[str, Any], nodes: list[dict[str, Any]]) -> list[int]:
    scenes = raw.get("scenes", [])
    if not isinstance(scenes, list):
        raise ValueError("invalid glTF scenes")
    if scenes or "scene" in raw:
        index = _index(raw.get("scene", 0), len(scenes), "scene")
        if not isinstance(scenes[index], dict):
            raise ValueError("invalid glTF scene")
        return _indices(scenes[index].get("nodes", []), len(nodes), "root node")
    children = {
        child for node in nodes for child in _indices(node.get("children", []), len(nodes), "child")
    }
    return [index for index in range(len(nodes)) if index not in children]


def _index(value: Any, count: int, field: str) -> int:
    if type(value) is not int or not 0 <= value < count:
        raise ValueError(f"invalid glTF {field} index")
    return value


def _indices(values: Any, count: int, field: str) -> list[int]:
    if not isinstance(values, list):
        raise ValueError(f"invalid glTF {field} indices")
    return [_index(value, count, field) for value in values]


def _place_mesh_node(
    node: dict[str, Any],
    index: int,
    world: np.ndarray,
    meshes: list[MeshAsset],
    placements: dict[int, np.ndarray],
) -> Scene3DNode:
    if "skin" in node:
        raise ValueError("glTF skinned nodes are not supported by static mesh import")
    mesh_index = _index(node["mesh"], len(meshes), "mesh")
    previous = placements.setdefault(mesh_index, world)
    if not np.array_equal(previous, world):
        raise ValueError("glTF mesh instances with different transforms are not supported")
    extras = node.get("extras")
    default_id = f"node_{index}"
    node_id = str(extras.get("id") or default_id) if isinstance(extras, dict) else default_id
    return Scene3DNode(
        id=node_id, name=str(node.get("name") or node_id), mesh_id=meshes[mesh_index].id
    )


def _numbers(value: Any, length: int, field: str) -> np.ndarray:
    if (
        not isinstance(value, (tuple, list))
        or len(value) != length
        or any(type(element) not in (int, float) for element in value)
    ):
        raise ValueError(f"invalid glTF node {field}")
    try:
        numbers = np.asarray(value, dtype=np.float64)
    except OverflowError as exc:
        raise ValueError(f"invalid glTF node {field}") from exc
    if not np.isfinite(numbers).all():
        raise ValueError(f"invalid glTF node {field}")
    return numbers


def _node_transform(node: dict[str, Any]) -> np.ndarray:
    if "matrix" in node:
        if any(field in node for field in _TRS_FIELDS):
            raise ValueError("glTF node matrix cannot be combined with TRS")
        matrix = _numbers(node["matrix"], 16, "matrix").reshape((4, 4), order="F")
        if not np.array_equal(matrix[3], _IDENTITY[3]):
            raise ValueError("glTF node matrix is not affine")
        return matrix
    if not any(field in node for field in _TRS_FIELDS):
        return _IDENTITY
    matrix = _IDENTITY.copy()
    matrix[:3, 3] = _numbers(node.get("translation", (0, 0, 0)), 3, "translation")
    scale = _numbers(node.get("scale", (1, 1, 1)), 3, "scale")
    if "rotation" in node:
        matrix[:3, :3] = _rotation_matrix(node["rotation"])
    matrix[:3, :3] *= scale
    return matrix


def _rotation_matrix(value: Any) -> np.ndarray:
    quaternion = _numbers(value, 4, "rotation")
    norm = math.hypot(*quaternion)
    if not math.isclose(norm, 1.0, abs_tol=1e-4):
        raise ValueError("glTF node rotation is not a unit quaternion")
    x, y, z, w = quaternion / norm
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def _bake_mesh(mesh: MeshAsset, world: np.ndarray) -> MeshAsset:
    if np.array_equal(world, _IDENTITY):
        return mesh
    linear = world[:3, :3]
    sign, _log_scale = np.linalg.slogdet(linear)
    if not np.isfinite(sign) or sign == 0:
        raise ValueError("glTF node transform is singular")
    try:
        inverse = np.linalg.inv(linear)
    except np.linalg.LinAlgError as exc:
        raise ValueError("glTF node transform is singular") from exc
    with np.errstate(over="ignore", invalid="ignore"):
        return replace(
            mesh,
            primitives=tuple(
                _bake_primitive(primitive, linear, world[:3, 3], inverse, mirror=sign < 0)
                for primitive in mesh.primitives
            ),
        )


def _bake_primitive(
    primitive: MeshPrimitive,
    linear: np.ndarray,
    translation: np.ndarray,
    inverse: np.ndarray,
    *,
    mirror: bool,
) -> MeshPrimitive:
    vertices = (primitive.vertices @ linear.T + translation).astype(np.float32)
    if not np.isfinite(vertices).all():
        raise ValueError("glTF transformed positions exceed the supported range")
    normals = primitive.normals
    if normals is not None:
        transformed = normals @ inverse
        lengths = np.linalg.norm(transformed, axis=1, keepdims=True)
        if not np.isfinite(lengths).all():
            raise ValueError("glTF transformed normals exceed the supported range")
        # Retain zero normals; the renderer's existing fallback handles them.
        normals = np.divide(transformed, lengths, out=transformed, where=lengths > 0)
    indices = primitive.indices[:, [0, 2, 1]] if mirror else primitive.indices
    return replace(primitive, vertices=vertices, normals=normals, indices=indices)
