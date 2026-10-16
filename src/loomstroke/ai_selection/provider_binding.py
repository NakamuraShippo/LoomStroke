"""Provider/readiness binding helper for AI Select callers.

SAM 3.1 is the only production backend. GUI and MCP both resolve their
provider through this module, so the whole application shares one worker
path: AI Select -> SAM 3.1 provider -> SAM 3.1 worker. A deterministic
fake client remains available behind a developer/testing flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .assets import Sam3AssetStore
from .data_model import BlockedReason, Sam3Readiness
from .provider import Sam3MaskProvider
from .settings_binding import (
    AI_SELECT_RUNTIME_MODE_FAKE,
    AI_SELECT_RUNTIME_MODE_SAM31,
    ai_select_fake_candidate_count,
    ai_select_runtime_mode,
)
from .worker_client import (
    AiSelectionWorkerClient,
    FakeAiSelectionWorkerClient,
    UnavailableAiSelectionWorkerClient,
)

SAM3_PROVIDER_BINDING_SCHEMA_VERSION = "sam3.provider_binding.v1"


@dataclass(frozen=True, slots=True)
class Sam3ProviderBinding:
    """Pair an AI Select provider with the readiness observed for it."""

    provider: Sam3MaskProvider
    readiness: Sam3Readiness
    runtime: str
    schema_version: str = SAM3_PROVIDER_BINDING_SCHEMA_VERSION

    @property
    def ready(self) -> bool:
        return self.readiness.state in {"ready", "partial"} and not self.readiness.blocked_reasons

    @property
    def blocked_reason_codes(self) -> tuple[str, ...]:
        return tuple(reason.code for reason in self.readiness.blocked_reasons)

    @property
    def warning_codes(self) -> tuple[str, ...]:
        return tuple(warning.code for warning in self.readiness.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "ready": self.ready,
            "runtime": self.runtime,
            "blockedReasonCodes": list(self.blocked_reason_codes),
            "warningCodes": list(self.warning_codes),
            "readiness": self.readiness.to_dict(),
        }


async def resolve_sam3_provider_binding(
    *,
    worker_client: AiSelectionWorkerClient | None = None,
    asset_store: Sam3AssetStore | None = None,
    runtime: str | None = None,
) -> Sam3ProviderBinding:
    """Create an AI Select provider and return it with sanitized readiness."""

    resolved_runtime = _runtime_label(runtime, has_worker_client=worker_client is not None)
    selected_worker = (
        worker_client if worker_client is not None else UnavailableAiSelectionWorkerClient()
    )
    provider = Sam3MaskProvider(worker_client=selected_worker, asset_store=asset_store)
    try:
        readiness = await provider.get_readiness()
    except Exception as exc:
        readiness = _blocked_readiness_for_exception(
            error_type=type(exc).__name__,
            runtime=resolved_runtime,
        )
    return Sam3ProviderBinding(
        provider=provider,
        readiness=readiness,
        runtime=resolved_runtime,
    )


async def resolve_configured_sam3_provider_binding(
    settings: object | None,
    *,
    asset_store: Sam3AssetStore | None = None,
) -> Sam3ProviderBinding:
    """Resolve the AI Select provider from application settings."""

    resolved_asset_store = asset_store or Sam3AssetStore()
    worker_client, runtime = create_configured_sam3_worker_client(
        settings,
        asset_store=resolved_asset_store,
    )
    return await resolve_sam3_provider_binding(
        worker_client=worker_client,
        asset_store=resolved_asset_store,
        runtime=runtime,
    )


def create_configured_sam3_worker_client(
    settings: object | None,
    *,
    asset_store: Sam3AssetStore | None = None,
) -> tuple[AiSelectionWorkerClient, str]:
    """Create the configured AI Select worker client.

    The SAM 3.1 backend is the default and only production path; it stays
    fail-closed until the one-click Setup has produced a ready runtime.
    """

    mode = ai_select_runtime_mode(settings)
    if mode == AI_SELECT_RUNTIME_MODE_FAKE:
        return (
            FakeAiSelectionWorkerClient(
                readiness=Sam3Readiness(
                    state="ready",
                    runtime=AI_SELECT_RUNTIME_MODE_FAKE,
                    diagnostics={
                        "provider": "fake",
                        "source": "ai_select_runtime_settings",
                    },
                ),
                candidate_count=ai_select_fake_candidate_count(settings),
            ),
            AI_SELECT_RUNTIME_MODE_FAKE,
        )

    from loomstroke.sam31.ai_select_bridge import (  # noqa: PLC0415 - heavy import
        Sam31BridgeWorkerClient,
    )
    from loomstroke.sam31.runtime_config import (  # noqa: PLC0415
        build_sam31_runtime_config,
    )
    from loomstroke.sam31.server_manager import (  # noqa: PLC0415
        get_sam31_server_manager,
    )

    sam31_config = build_sam31_runtime_config(settings)
    manager = get_sam31_server_manager()
    bridge = Sam31BridgeWorkerClient(manager=manager, config=sam31_config, asset_store=asset_store)
    sam31_readiness = manager.readiness(sam31_config)
    if not sam31_readiness.ready:
        return (
            UnavailableAiSelectionWorkerClient(
                bridge.blocked_readiness(
                    sam31_readiness.blocked_reasons, sam31_readiness.diagnostics
                )
            ),
            AI_SELECT_RUNTIME_MODE_SAM31,
        )
    return bridge, AI_SELECT_RUNTIME_MODE_SAM31


def _runtime_label(runtime: str | None, *, has_worker_client: bool) -> str:
    cleaned = (runtime or "").strip()
    if cleaned:
        return cleaned
    if has_worker_client:
        return "injected"
    return "unavailable"


def _blocked_readiness_for_exception(*, error_type: str, runtime: str) -> Sam3Readiness:
    action = "Check the SAM 3.1 runtime configuration and retry."
    return Sam3Readiness(
        state="blocked",
        runtime=runtime,
        blocked_reasons=(
            BlockedReason(
                code="provider_readiness_unavailable",
                message="AI Select provider readiness could not be checked.",
                action=action,
            ),
        ),
        recommended_actions=(action,),
        diagnostics={
            "provider": "sam3_provider_binding",
            "source": "sam3_provider_binding",
            "errorType": _safe_error_type(error_type),
        },
        credential_values_included=False,
        credential_read_performed=False,
    )


def _safe_error_type(value: str) -> str:
    normalized = "".join(ch for ch in str(value) if ch.isalnum() or ch in {"_", "."})
    return (normalized or "Exception")[:80]


__all__ = [
    "SAM3_PROVIDER_BINDING_SCHEMA_VERSION",
    "Sam3ProviderBinding",
    "create_configured_sam3_worker_client",
    "resolve_configured_sam3_provider_binding",
    "resolve_sam3_provider_binding",
]
