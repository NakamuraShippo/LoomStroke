"""ComfyUI adapter for provider-neutral generated 3D assets."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from loomstroke.core.assets_3d.provider_contract import (
    Generated3DProviderOutput,
    ProviderArtifact,
    mask_sensitive_metadata,
)


class ComfyUI3DProviderAdapter:
    """Normalize ComfyUI 3D output payloads into LoomStroke's contract."""

    provider_id = "comfyui.3d"
    requires_external_services = False

    def normalize_response(self, response: Mapping[str, Any]) -> Generated3DProviderOutput:
        job_id = str(response.get("job_id") or response.get("prompt_id") or "unknown")
        artifacts = tuple(self._iter_artifacts(response))
        if not artifacts:
            raise ValueError("ComfyUI 3D response contains no model/map artifacts")
        return Generated3DProviderOutput(
            provider_id=self.provider_id,
            job_id=job_id,
            artifacts=artifacts,
            prompt=response.get("prompt") if isinstance(response.get("prompt"), str) else None,
            seed=self._coerce_seed(response.get("seed")),
            metadata=mask_sensitive_metadata(
                {
                    "workflow_id": response.get("workflow_id"),
                    "node_id": response.get("node_id"),
                    "extra": response.get("metadata") or {},
                }
            ),
        )

    def _iter_artifacts(self, response: Mapping[str, Any]) -> Iterable[ProviderArtifact]:
        single_keys = (
            ("model_file", "mesh", "model/gltf"),
            ("model_path", "mesh", "model/gltf"),
            ("mesh_file", "mesh", "model/gltf"),
            ("glb", "mesh", "model/gltf-binary"),
            ("gltf", "mesh", "model/gltf+json"),
            ("obj", "mesh", "model/obj"),
            ("preview_image", "preview", "image/png"),
            ("depth_map", "depth_map", "image/png"),
            ("normal_map", "normal_map", "image/png"),
            ("segmentation_map", "segmentation_map", "image/png"),
        )
        for key, kind, mime_type in single_keys:
            value = response.get(key)
            artifact = self._artifact_from_value(value, kind=kind, role=key, mime_type=mime_type)
            if artifact is not None:
                yield artifact

        for value in self._as_iterable(response.get("textures")):
            artifact = self._artifact_from_value(
                value,
                kind="texture",
                role="texture",
                mime_type="image/png",
            )
            if artifact is not None:
                yield artifact

        for value in self._as_iterable(response.get("normal_maps")):
            artifact = self._artifact_from_value(
                value,
                kind="normal_map",
                role="normal_map",
                mime_type="image/png",
            )
            if artifact is not None:
                yield artifact

        for value in self._as_iterable(response.get("artifacts") or response.get("files")):
            artifact = self._artifact_from_mapping(value)
            if artifact is not None:
                yield artifact

    def _artifact_from_mapping(self, value: Any) -> ProviderArtifact | None:
        if isinstance(value, Mapping):
            kind = str(value.get("kind") or value.get("type") or "mesh")
            role = value.get("role")
            mime_type = value.get("mime_type") or value.get("mime")
            path_value = value.get("path") or value.get("local_path") or value.get("file")
            uri = value.get("uri") or value.get("url")
            metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
            if path_value:
                return ProviderArtifact(
                    kind=kind,
                    local_path=Path(str(path_value)),
                    role=str(role) if role is not None else None,
                    mime_type=str(mime_type) if mime_type is not None else None,
                    metadata=dict(metadata),
                )
            if uri:
                return ProviderArtifact(
                    kind=kind,
                    uri=str(uri),
                    role=str(role) if role is not None else None,
                    mime_type=str(mime_type) if mime_type is not None else None,
                    metadata=dict(metadata),
                )
        return self._artifact_from_value(value, kind="mesh", role="artifact", mime_type=None)

    @staticmethod
    def _artifact_from_value(
        value: Any,
        *,
        kind: str,
        role: str,
        mime_type: str | None,
    ) -> ProviderArtifact | None:
        if value is None:
            return None
        if isinstance(value, Mapping):
            path_value = value.get("path") or value.get("local_path") or value.get("file")
            uri = value.get("uri") or value.get("url")
            metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
            role = str(value.get("role") or role)
            kind = str(value.get("kind") or value.get("type") or kind)
            mime = value.get("mime_type") or value.get("mime") or mime_type
            if path_value:
                return ProviderArtifact(
                    kind=kind,
                    local_path=Path(str(path_value)),
                    role=role,
                    mime_type=str(mime) if mime is not None else None,
                    metadata=dict(metadata),
                )
            if uri:
                return ProviderArtifact(
                    kind=kind,
                    uri=str(uri),
                    role=role,
                    mime_type=str(mime) if mime is not None else None,
                    metadata=dict(metadata),
                )
            return None
        text = str(value)
        if text.startswith(("http://", "https://")):
            return ProviderArtifact(kind=kind, uri=text, role=role, mime_type=mime_type)
        return ProviderArtifact(kind=kind, local_path=Path(text), role=role, mime_type=mime_type)

    @staticmethod
    def _as_iterable(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes, Mapping)):
            return (value,)
        try:
            return tuple(value)
        except TypeError:
            return (value,)

    @staticmethod
    def _coerce_seed(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
