"""glTF/GLB/OBJ import and export for the neutral 3D scene model."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any, cast

import numpy as np

from .material import MaterialAsset
from .mesh import MeshAsset, MeshPrimitive
from .scene import Scene3DDocument, Scene3DNode
from .texture import TextureAsset

_COMPONENT_FLOAT = 5126
_COMPONENT_UBYTE = 5121
_COMPONENT_USHORT = 5123
_COMPONENT_UINT = 5125
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
_TYPE_COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}
_COMPONENT_DTYPES = {
    _COMPONENT_UBYTE: np.uint8,
    _COMPONENT_USHORT: np.uint16,
    _COMPONENT_UINT: np.uint32,
    _COMPONENT_FLOAT: np.float32,
}


class UsdzConverterUnavailable(RuntimeError):
    """Raised when USDZ import/export is requested without a configured converter."""


def export_gltf(scene: Scene3DDocument, path: Path) -> None:
    path.write_text(json.dumps(_scene_to_gltf(scene), separators=(",", ":")), encoding="utf-8")


def import_gltf(path: Path) -> Scene3DDocument:
    return _scene_from_gltf(json.loads(path.read_text(encoding="utf-8")), base_dir=path.parent)


def export_glb(scene: Scene3DDocument, path: Path) -> None:
    raw_json = json.dumps(_scene_to_gltf(scene), separators=(",", ":")).encode("utf-8")
    json_padding = (4 - (len(raw_json) % 4)) % 4
    json_chunk = raw_json + (b" " * json_padding)
    total_length = 12 + 8 + len(json_chunk)
    path.write_bytes(
        b"glTF"
        + struct.pack("<II", 2, total_length)
        + struct.pack("<I4s", len(json_chunk), b"JSON")
        + json_chunk
    )


def import_glb(path: Path) -> Scene3DDocument:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("not a GLB file")
    version, total_length = struct.unpack("<II", data[4:12])
    if version != 2:
        raise ValueError(f"unsupported GLB version: {version}")
    if total_length > len(data):
        raise ValueError("truncated GLB file")
    offset = 12
    json_chunk: bytes | None = None
    binary_chunks: list[bytes] = []
    while offset + 8 <= total_length:
        chunk_length, chunk_type = struct.unpack("<I4s", data[offset : offset + 8])
        offset += 8
        if offset + chunk_length > total_length:
            raise ValueError("truncated GLB chunk")
        chunk = data[offset : offset + chunk_length]
        offset += chunk_length
        if chunk_type == b"JSON":
            json_chunk = chunk
        elif chunk_type == b"BIN\x00":
            binary_chunks.append(chunk)
    if json_chunk is not None:
        return _scene_from_gltf(
            json.loads(json_chunk.decode("utf-8").rstrip(" \x00")),
            binary_buffers=binary_chunks,
        )
    raise ValueError("GLB JSON chunk missing")


def export_obj(scene: Scene3DDocument, path: Path) -> None:  # noqa: PLR0912
    lines: list[str] = ["# LoomStroke OBJ export"]
    vertex_offset = 1
    normal_offset = 1
    uv_offset = 1
    for mesh in scene.meshes:
        lines.append(f"o {mesh.name}")
        for primitive in mesh.primitives:
            for vertex in primitive.vertices:
                lines.append(f"v {vertex[0]:.7g} {vertex[1]:.7g} {vertex[2]:.7g}")
            if primitive.uvs is not None:
                for uv in primitive.uvs:
                    lines.append(f"vt {uv[0]:.7g} {uv[1]:.7g}")
            if primitive.normals is not None:
                for normal in primitive.normals:
                    lines.append(f"vn {normal[0]:.7g} {normal[1]:.7g} {normal[2]:.7g}")
            for face in primitive.indices.reshape((-1, 3)):
                refs: list[str] = []
                for raw_index in face:
                    vi = int(raw_index) + vertex_offset
                    vt = int(raw_index) + uv_offset if primitive.uvs is not None else None
                    vn = int(raw_index) + normal_offset if primitive.normals is not None else None
                    if vt is not None and vn is not None:
                        refs.append(f"{vi}/{vt}/{vn}")
                    elif vt is not None:
                        refs.append(f"{vi}/{vt}")
                    elif vn is not None:
                        refs.append(f"{vi}//{vn}")
                    else:
                        refs.append(str(vi))
                lines.append("f " + " ".join(refs))
            vertex_offset += len(primitive.vertices)
            if primitive.uvs is not None:
                uv_offset += len(primitive.uvs)
            if primitive.normals is not None:
                normal_offset += len(primitive.normals)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def import_obj(path: Path) -> Scene3DDocument:
    vertices: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    out_vertices: list[tuple[float, float, float]] = []
    out_normals: list[tuple[float, float, float]] = []
    out_uvs: list[tuple[float, float]] = []
    indices: list[tuple[int, int, int]] = []
    index_map: dict[tuple[int, int | None, int | None], int] = {}

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "v" and len(parts) >= 4:
            vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif tag == "vn" and len(parts) >= 4:
            normals.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif tag == "vt" and len(parts) >= 3:
            uvs.append((float(parts[1]), float(parts[2])))
        elif tag == "f" and len(parts) >= 4:
            face_indices = [
                _obj_vertex_index(
                    ref,
                    vertices,
                    uvs,
                    normals,
                    out_vertices,
                    out_uvs,
                    out_normals,
                    index_map,
                )
                for ref in parts[1:]
            ]
            for i in range(1, len(face_indices) - 1):
                indices.append((face_indices[0], face_indices[i], face_indices[i + 1]))

    primitive = MeshPrimitive(
        vertices=np.asarray(out_vertices, dtype=np.float32),
        indices=np.asarray(indices, dtype=np.uint32),
        normals=np.asarray(out_normals, dtype=np.float32) if out_normals else None,
        uvs=np.asarray(out_uvs, dtype=np.float32) if out_uvs else None,
    )
    mesh = MeshAsset(id="obj_mesh", name=path.stem or "OBJ Mesh", primitives=(primitive,))
    node = Scene3DNode(id="obj_node", name=mesh.name, mesh_id=mesh.id)
    return Scene3DDocument(
        id=path.stem or "obj_scene",
        nodes=(node,),
        meshes=(mesh,),
        root_node_ids=(node.id,),
    )


def import_usdz(_path: Path) -> Scene3DDocument:
    raise UsdzConverterUnavailable("USDZ import requires an optional external converter")


def export_usdz(_scene: Scene3DDocument, _path: Path) -> None:
    raise UsdzConverterUnavailable("USDZ export requires an optional external converter")


def load_scene(path: Path) -> Scene3DDocument:
    suffix = path.suffix.lower()
    if suffix in {".gltf", ".json"}:
        return import_gltf(path)
    if suffix == ".glb":
        return import_glb(path)
    if suffix == ".obj":
        return import_obj(path)
    if suffix == ".usdz":
        return import_usdz(path)
    raise ValueError(f"unsupported 3D asset format: {suffix}")


def save_scene(scene: Scene3DDocument, path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix in {".gltf", ".json"}:
        export_gltf(scene, path)
    elif suffix == ".glb":
        export_glb(scene, path)
    elif suffix == ".obj":
        export_obj(scene, path)
    elif suffix == ".usdz":
        export_usdz(scene, path)
    else:
        raise ValueError(f"unsupported 3D asset format: {suffix}")


def _scene_to_gltf(scene: Scene3DDocument) -> dict[str, Any]:
    buffer = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []

    def add_array(array: np.ndarray, *, component_type: int, type_name: str, target: int) -> int:
        raw = np.ascontiguousarray(array).tobytes()
        padding = (4 - (len(buffer) % 4)) % 4
        if padding:
            buffer.extend(b"\x00" * padding)
        offset = len(buffer)
        buffer.extend(raw)
        view_index = len(buffer_views)
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(raw),
                "target": target,
            }
        )
        accessor: dict[str, Any] = {
            "bufferView": view_index,
            "componentType": component_type,
            "count": len(array),
            "type": type_name,
        }
        if type_name == "VEC3" and component_type == _COMPONENT_FLOAT and len(array):
            accessor["min"] = np.asarray(array).min(axis=0).astype(float).tolist()
            accessor["max"] = np.asarray(array).max(axis=0).astype(float).tolist()
        accessors.append(accessor)
        return len(accessors) - 1

    material_indices = {material.id: idx for idx, material in enumerate(scene.materials)}
    texture_indices = {texture.id: idx for idx, texture in enumerate(scene.textures)}
    for mesh in scene.meshes:
        primitives: list[dict[str, Any]] = []
        for primitive in mesh.primitives:
            attributes = {
                "POSITION": add_array(
                    primitive.vertices.astype(np.float32),
                    component_type=_COMPONENT_FLOAT,
                    type_name="VEC3",
                    target=_ARRAY_BUFFER,
                )
            }
            if primitive.normals is not None:
                attributes["NORMAL"] = add_array(
                    primitive.normals.astype(np.float32),
                    component_type=_COMPONENT_FLOAT,
                    type_name="VEC3",
                    target=_ARRAY_BUFFER,
                )
            if primitive.uvs is not None:
                attributes["TEXCOORD_0"] = add_array(
                    primitive.uvs.astype(np.float32),
                    component_type=_COMPONENT_FLOAT,
                    type_name="VEC2",
                    target=_ARRAY_BUFFER,
                )
            entry: dict[str, Any] = {
                "attributes": attributes,
                "indices": add_array(
                    primitive.indices.reshape((-1,)).astype(np.uint32),
                    component_type=_COMPONENT_UINT,
                    type_name="SCALAR",
                    target=_ELEMENT_ARRAY_BUFFER,
                ),
            }
            if primitive.material_id in material_indices:
                entry["material"] = material_indices[str(primitive.material_id)]
            primitives.append(entry)
        meshes.append({"name": mesh.name, "primitives": primitives, "extras": {"id": mesh.id}})

    data_uri = "data:application/octet-stream;base64," + base64.b64encode(buffer).decode("ascii")
    nodes = [
        {"name": node.name, "mesh": _mesh_index(scene, node.mesh_id), "extras": {"id": node.id}}
        for node in scene.nodes
        if node.mesh_id is not None
    ]
    return {
        "asset": {"version": "2.0", "generator": "LoomStroke"},
        "buffers": [{"byteLength": len(buffer), "uri": data_uri}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "materials": [
            _material_to_gltf(material, texture_indices=texture_indices)
            for material in scene.materials
        ],
        "images": [_texture_to_gltf_image(texture) for texture in scene.textures],
        "textures": [
            {"source": index, "extras": {"id": texture.id}}
            for index, texture in enumerate(scene.textures)
        ],
        "meshes": meshes,
        "nodes": nodes,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "scene": 0,
        "extras": {"loomstroke_scene_id": scene.id},
    }


def _scene_from_gltf(
    raw: dict[str, Any],
    *,
    binary_buffers: list[bytes] | None = None,
    base_dir: Path | None = None,
) -> Scene3DDocument:
    buffers = [
        _decode_buffer(buffer, binary_buffers=binary_buffers, buffer_index=index)
        for index, buffer in enumerate(raw.get("buffers", []))
    ]
    buffer_views = raw.get("bufferViews", [])
    accessors = raw.get("accessors", [])
    textures = _textures_from_gltf(raw, buffer_views, buffers, base_dir=base_dir)
    texture_ids = {
        int(texture.metadata.get("source_texture_index", index)): texture.id
        for index, texture in enumerate(textures)
    }
    materials = tuple(
        _material_from_gltf(index, material, texture_ids=texture_ids)
        for index, material in enumerate(raw.get("materials", []))
    )
    meshes: list[MeshAsset] = []
    for mesh_index, raw_mesh in enumerate(raw.get("meshes", [])):
        primitives = []
        for raw_primitive in raw_mesh.get("primitives", []):
            attributes = raw_primitive.get("attributes", {})
            vertices = _read_accessor("POSITION", attributes, accessors, buffer_views, buffers)
            normals = _read_accessor("NORMAL", attributes, accessors, buffer_views, buffers)
            uvs = _read_accessor("TEXCOORD_0", attributes, accessors, buffer_views, buffers)
            indices = _read_indices(raw_primitive.get("indices"), accessors, buffer_views, buffers)
            material_id: str | None = None
            material_index = raw_primitive.get("material")
            if isinstance(material_index, int) and 0 <= material_index < len(materials):
                material_id = materials[material_index].id
            primitives.append(
                MeshPrimitive(
                    vertices=cast(np.ndarray, vertices),
                    indices=indices.reshape((-1, 3)),
                    normals=normals,
                    uvs=uvs,
                    material_id=material_id,
                )
            )
        mesh_extras: dict[str, Any] = (
            raw_mesh.get("extras") if isinstance(raw_mesh.get("extras"), dict) else {}
        )
        mesh_asset_id = str(mesh_extras.get("id") or f"mesh_{mesh_index}")
        meshes.append(
            MeshAsset(
                id=mesh_asset_id,
                name=str(raw_mesh.get("name") or mesh_asset_id),
                primitives=tuple(primitives),
            )
        )

    nodes = []
    for node_index, raw_node in enumerate(raw.get("nodes", [])):
        mesh_index = raw_node.get("mesh")
        node_mesh_id = (
            meshes[mesh_index].id
            if isinstance(mesh_index, int) and 0 <= mesh_index < len(meshes)
            else None
        )
        node_extras: dict[str, Any] = (
            raw_node.get("extras") if isinstance(raw_node.get("extras"), dict) else {}
        )
        node_id = str(node_extras.get("id") or f"node_{node_index}")
        nodes.append(
            Scene3DNode(
                id=node_id,
                name=str(raw_node.get("name") or node_id),
                mesh_id=node_mesh_id,
            )
        )
    if not nodes:
        nodes = [
            Scene3DNode(id=f"node_{index}", name=mesh.name, mesh_id=mesh.id)
            for index, mesh in enumerate(meshes)
        ]
    raw_extras = raw.get("extras")
    scene_extras: dict[str, Any] = raw_extras if isinstance(raw_extras, dict) else {}
    scene_id = str(scene_extras.get("loomstroke_scene_id") or "gltf_scene")
    return Scene3DDocument(
        id=scene_id,
        nodes=tuple(nodes),
        meshes=tuple(meshes),
        materials=materials,
        textures=textures,
        root_node_ids=tuple(node.id for node in nodes),
    )


def _obj_vertex_index(
    ref: str,
    vertices: list[tuple[float, float, float]],
    uvs: list[tuple[float, float]],
    normals: list[tuple[float, float, float]],
    out_vertices: list[tuple[float, float, float]],
    out_uvs: list[tuple[float, float]],
    out_normals: list[tuple[float, float, float]],
    index_map: dict[tuple[int, int | None, int | None], int],
) -> int:
    parts = ref.split("/")
    vi = _resolve_obj_index(int(parts[0]), len(vertices))
    vt = _resolve_obj_index(int(parts[1]), len(uvs)) if len(parts) > 1 and parts[1] else None
    vn = _resolve_obj_index(int(parts[2]), len(normals)) if len(parts) > 2 and parts[2] else None
    key = (vi, vt, vn)
    mapped = index_map.get(key)
    if mapped is not None:
        return mapped
    index_map[key] = len(out_vertices)
    out_vertices.append(vertices[vi])
    if vt is not None:
        out_uvs.append(uvs[vt])
    if vn is not None:
        out_normals.append(normals[vn])
    return index_map[key]


def _resolve_obj_index(index: int, length: int) -> int:
    return index - 1 if index > 0 else length + index


def _mesh_index(scene: Scene3DDocument, mesh_id: str | None) -> int | None:
    if mesh_id is None:
        return None
    for index, mesh in enumerate(scene.meshes):
        if mesh.id == mesh_id:
            return index
    return None


def _material_to_gltf(
    material: MaterialAsset,
    *,
    texture_indices: dict[str, int],
) -> dict[str, Any]:
    pbr: dict[str, Any] = {
        "baseColorFactor": list(material.base_color),
        "metallicFactor": material.metallic,
        "roughnessFactor": material.roughness,
    }
    if material.base_color_texture_id in texture_indices:
        pbr["baseColorTexture"] = {"index": texture_indices[str(material.base_color_texture_id)]}
    material_entry = {
        "name": material.name,
        "pbrMetallicRoughness": pbr,
        "alphaMode": material.alpha_mode.upper(),
        "extras": {"id": material.id},
    }
    if material.normal_texture_id in texture_indices:
        material_entry["normalTexture"] = {
            "index": texture_indices[str(material.normal_texture_id)]
        }
    return material_entry


def _material_from_gltf(
    index: int,
    raw: dict[str, Any],
    *,
    texture_ids: dict[int, str],
) -> MaterialAsset:
    raw_extras = raw.get("extras")
    extras: dict[str, Any] = raw_extras if isinstance(raw_extras, dict) else {}
    raw_pbr = raw.get("pbrMetallicRoughness")
    pbr: dict[str, Any] = raw_pbr if isinstance(raw_pbr, dict) else {}
    color = pbr.get("baseColorFactor") or (0.72, 0.72, 0.72, 1.0)
    base_color_texture_id = _texture_id_from_info(pbr.get("baseColorTexture"), texture_ids)
    normal_texture_id = _texture_id_from_info(raw.get("normalTexture"), texture_ids)
    return MaterialAsset(
        id=str(extras.get("id") or f"material_{index}"),
        name=str(raw.get("name") or f"Material {index}"),
        base_color=tuple(float(v) for v in color[:4]),  # type: ignore[arg-type]
        base_color_texture_id=base_color_texture_id,
        normal_texture_id=normal_texture_id,
        roughness=float(pbr.get("roughnessFactor", 0.5)),
        metallic=float(pbr.get("metallicFactor", 0.0)),
        alpha_mode=str(raw.get("alphaMode", "OPAQUE")).lower(),
    )


def _texture_to_gltf_image(texture: TextureAsset) -> dict[str, Any]:
    data = texture.metadata.get("data") if isinstance(texture.metadata, dict) else None
    mime_type = str(texture.metadata.get("mime_type") or "image/png")
    if isinstance(data, bytes):
        encoded = base64.b64encode(data).decode("ascii")
        return {
            "uri": f"data:{mime_type};base64,{encoded}",
            "name": texture.id,
            "extras": {"id": texture.id, "usage": texture.usage},
        }
    if texture.path is not None and texture.path.exists():
        raw = texture.path.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        mime_type = _image_mime_type(texture.path)
        return {
            "uri": f"data:{mime_type};base64,{encoded}",
            "name": texture.path.name,
            "extras": {"id": texture.id, "usage": texture.usage},
        }
    return {"name": texture.id, "extras": {"id": texture.id, "usage": texture.usage}}


def _textures_from_gltf(
    raw: dict[str, Any],
    buffer_views: list[dict[str, Any]],
    buffers: list[bytes],
    *,
    base_dir: Path | None,
) -> tuple[TextureAsset, ...]:
    raw_textures = raw.get("textures", [])
    raw_images = raw.get("images", [])
    textures: list[TextureAsset] = []
    for texture_index, raw_texture in enumerate(raw_textures):
        if not isinstance(raw_texture, dict):
            continue
        source_index = raw_texture.get("source")
        if not isinstance(source_index, int) or not (0 <= source_index < len(raw_images)):
            continue
        raw_image = raw_images[source_index]
        if not isinstance(raw_image, dict):
            continue
        texture_extras = raw_texture.get("extras")
        image_extras = raw_image.get("extras")
        extras: dict[str, Any] = {}
        if isinstance(image_extras, dict):
            extras.update(image_extras)
        if isinstance(texture_extras, dict):
            extras.update(texture_extras)
        texture_id = str(extras.get("id") or f"texture_{texture_index}")
        decoded = _decode_gltf_image(raw_image, buffer_views, buffers, base_dir=base_dir)
        if decoded is None:
            continue
        path, data, mime_type = decoded
        metadata = {
            "mime_type": mime_type,
            "source_index": source_index,
            "source_texture_index": texture_index,
        }
        if data is not None:
            metadata["data"] = data
        textures.append(
            TextureAsset(
                id=texture_id,
                path=path,
                usage=str(extras.get("usage") or "base_color"),
                metadata=metadata,
            )
        )
    return tuple(textures)


def _decode_gltf_image(
    raw_image: dict[str, Any],
    buffer_views: list[dict[str, Any]],
    buffers: list[bytes],
    *,
    base_dir: Path | None,
) -> tuple[Path | None, bytes | None, str] | None:
    mime_type = str(raw_image.get("mimeType") or "image/png")
    uri = raw_image.get("uri")
    if isinstance(uri, str) and uri.startswith("data:"):
        header, encoded = uri.split(",", 1)
        mime_type = header.split(";", 1)[0].removeprefix("data:") or mime_type
        return None, base64.b64decode(encoded), mime_type
    if isinstance(uri, str) and base_dir is not None:
        path = _resolve_external_asset_path(base_dir, uri)
        return path, None, _image_mime_type(path)
    buffer_view_index = raw_image.get("bufferView")
    if isinstance(buffer_view_index, int) and 0 <= buffer_view_index < len(buffer_views):
        view = buffer_views[buffer_view_index]
        buffer = buffers[int(view.get("buffer", 0))]
        start = int(view.get("byteOffset", 0))
        end = start + int(view.get("byteLength", 0))
        return None, bytes(buffer[start:end]), mime_type
    return None


def _texture_id_from_info(raw_info: Any, texture_ids: dict[int, str]) -> str | None:
    if not isinstance(raw_info, dict):
        return None
    texture_index = raw_info.get("index")
    if not isinstance(texture_index, int):
        return None
    return texture_ids.get(texture_index)


def _resolve_external_asset_path(base_dir: Path, uri: str) -> Path:
    root = base_dir.resolve()
    path = (base_dir / uri).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"external glTF asset path escapes source folder: {uri}")
    return path


def _image_mime_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".ktx2": "image/ktx2",
    }.get(path.suffix.lower(), "image/png")


def _decode_buffer(
    raw: dict[str, Any],
    *,
    binary_buffers: list[bytes] | None = None,
    buffer_index: int = 0,
) -> bytes:
    uri = raw.get("uri")
    if isinstance(uri, str) and uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    if not uri and binary_buffers and buffer_index < len(binary_buffers):
        data = binary_buffers[buffer_index]
        byte_length = raw.get("byteLength")
        if isinstance(byte_length, int) and byte_length >= 0:
            return data[:byte_length]
        return data
    raise ValueError("only embedded glTF buffers or GLB BIN chunks are supported")


def _read_accessor(
    name: str,
    attributes: dict[str, int],
    accessors: list[dict[str, Any]],
    buffer_views: list[dict[str, Any]],
    buffers: list[bytes],
) -> np.ndarray | None:
    accessor_index = attributes.get(name)
    if accessor_index is None:
        return None
    return _read_accessor_by_index(
        accessor_index,
        accessors,
        buffer_views,
        buffers,
    ).astype(np.float32)


def _read_indices(
    accessor_index: int | None,
    accessors: list[dict[str, Any]],
    buffer_views: list[dict[str, Any]],
    buffers: list[bytes],
) -> np.ndarray:
    if accessor_index is None:
        raise ValueError("glTF primitive indices are required")
    return _read_accessor_by_index(
        accessor_index,
        accessors,
        buffer_views,
        buffers,
    ).astype(np.uint32)


def _read_accessor_by_index(
    accessor_index: int,
    accessors: list[dict[str, Any]],
    buffer_views: list[dict[str, Any]],
    buffers: list[bytes],
) -> np.ndarray:
    accessor = accessors[accessor_index]
    view = buffer_views[int(accessor["bufferView"])]
    buffer = buffers[int(view.get("buffer", 0))]
    component_type = int(accessor["componentType"])
    try:
        dtype = _COMPONENT_DTYPES[component_type]
    except KeyError as exc:
        raise ValueError(f"unsupported glTF component type: {component_type}") from exc
    count = int(accessor["count"])
    type_count = _TYPE_COUNTS[str(accessor["type"])]
    byte_offset = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    byte_length = count * type_count * np.dtype(dtype).itemsize
    array = np.frombuffer(buffer[byte_offset : byte_offset + byte_length], dtype=dtype).copy()
    return array.reshape((count, type_count))
