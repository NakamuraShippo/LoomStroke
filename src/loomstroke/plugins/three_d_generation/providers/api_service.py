"""Future external API adapter for provider-neutral generated 3D assets."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loomstroke.config.secret_provider import SecretProvider
from loomstroke.core.assets_3d.provider_contract import (
    Generated3DProviderOutput,
    ProviderArtifact,
    mask_sensitive_metadata,
)
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent


class ApiService3DProviderAdapter:
    """Adapter skeleton for non-ComfyUI 3D generation APIs.

    This keeps the output contract identical to the ComfyUI adapter while
    requiring credentials to flow only through ``SecretProvider.use``.
    """

    requires_external_services = True

    def __init__(
        self,
        *,
        provider_id: str,
        credential_service_name: str,
        settings: Any = None,
        secret_provider: SecretProvider | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.credential_service_name = credential_service_name
        self._settings = settings
        self._secret_provider = secret_provider or SecretProvider()

    def ensure_submission_allowed(self) -> bool:
        return ensure_network_services_consent(self._settings, service_name=self.provider_id)

    @contextmanager
    def authorization_headers(self) -> Iterator[dict[str, str]]:
        if not self.ensure_submission_allowed():
            yield {}
            return
        with self._secret_provider.use(self.credential_service_name) as token:
            yield {"Authorization": f"Bearer {token}"}

    def normalize_response(self, response: Mapping[str, Any]) -> Generated3DProviderOutput:
        job_id = str(response.get("job_id") or response.get("id") or "unknown")
        artifacts = []
        for item in self._artifact_items(response):
            artifact = self._artifact_from_item(item)
            if artifact is not None:
                artifacts.append(artifact)
        if not artifacts:
            raise ValueError(f"{self.provider_id} response contains no 3D artifacts")
        return Generated3DProviderOutput(
            provider_id=self.provider_id,
            job_id=job_id,
            artifacts=tuple(artifacts),
            prompt=response.get("prompt") if isinstance(response.get("prompt"), str) else None,
            seed=self._coerce_seed(response.get("seed")),
            metadata=mask_sensitive_metadata(response.get("metadata") or {}),
        )

    def _artifact_items(self, response: Mapping[str, Any]) -> tuple[Any, ...]:
        value = response.get("artifacts") or response.get("outputs") or response.get("files")
        if value is None:
            value = response.get("model") or response.get("model_file") or response.get("url")
        if value is None:
            return ()
        if isinstance(value, (str, bytes, Mapping)):
            return (value,)
        try:
            return tuple(value)
        except TypeError:
            return (value,)

    @staticmethod
    def _artifact_from_item(value: Any) -> ProviderArtifact | None:
        if isinstance(value, Mapping):
            kind = str(value.get("kind") or value.get("type") or "mesh")
            role = value.get("role")
            mime_type = value.get("mime_type") or value.get("mime")
            metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
            local = value.get("path") or value.get("local_path") or value.get("file")
            uri = value.get("uri") or value.get("url")
            if local:
                return ProviderArtifact(
                    kind=kind,
                    local_path=Path(str(local)),
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
            return None
        text = str(value)
        if text.startswith(("http://", "https://")):
            return ProviderArtifact(kind="mesh", uri=text)
        return ProviderArtifact(kind="mesh", local_path=Path(text))

    @staticmethod
    def _coerce_seed(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
