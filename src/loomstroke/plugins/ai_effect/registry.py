"""GatewayRegistry — gateway_id → AIGateway instance resolution.

Spec reference: ``AIeffect_design/04_python_design/gateway_protocol.md`` §6.

One instance per process, populated at startup by the AI Effect plugin
subsystem (Phase 5 for ComfyUI, Phase 8a for NanoBanana). Phase 1 only
provides the registry itself — it holds no gateways.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loomstroke.plugins.ai_effect.gateway_protocol import AIGateway


class UnknownGatewayError(LookupError):
    """Raised by ``GatewayRegistry.get`` when an unregistered gateway_id is requested."""


class DuplicateGatewayError(ValueError):
    """Raised when registering a gateway whose ``gateway_id`` already exists AND
    the caller did not opt into overwrite.

    Mirrors ``DuplicatePresetError`` (preset_library.py) naming style.
    """


class GatewayRegistry:
    """Process-wide registry of AI Effect Gateway implementations.

    Contract:
    - ``register(gateway, overwrite=False)`` inserts by ``gateway.gateway_id``.
      Raises ``DuplicateGatewayError`` if the id already exists and
      ``overwrite`` is False (default). With ``overwrite=True`` the existing
      entry is silently replaced — the caller is responsible for calling
      ``dispose()`` on the previous gateway (e.g. to close aiohttp sessions).
    - ``get(gateway_id)`` raises ``UnknownGatewayError`` on miss
    - ``list_registered()`` returns gateway_ids in insertion order
    """

    def __init__(self) -> None:
        self._gateways: dict[str, AIGateway] = {}

    def register(self, gateway: AIGateway, *, overwrite: bool = False) -> None:
        """Register a gateway.

        Args:
            gateway: Gateway instance to register under ``gateway.gateway_id``.
            overwrite: When False (default), raise ``DuplicateGatewayError``
                if the id is already registered. When True, silently replace
                the existing entry — the caller MUST call ``dispose()`` on the
                previous gateway beforehand to release resources
                (e.g. aiohttp.ClientSession).

        Raises:
            DuplicateGatewayError: If ``gateway.gateway_id`` is already
                registered and ``overwrite`` is False.
        """
        if not overwrite and gateway.gateway_id in self._gateways:
            raise DuplicateGatewayError(
                f"Gateway {gateway.gateway_id!r} is already registered. "
                f"Pass overwrite=True to replace (caller must dispose the previous "
                f"gateway first to avoid resource leaks)."
            )
        self._gateways[gateway.gateway_id] = gateway

    def unregister(self, gateway_id: str) -> bool:
        """Remove a gateway if present. Returns True if a gateway was removed."""
        return self._gateways.pop(gateway_id, None) is not None

    def get(self, gateway_id: str) -> AIGateway:
        """Resolve gateway_id to instance. Raises ``UnknownGatewayError`` on miss."""
        try:
            return self._gateways[gateway_id]
        except KeyError as exc:
            raise UnknownGatewayError(
                f"Gateway {gateway_id!r} is not registered. " f"Known: {sorted(self._gateways)}"
            ) from exc

    def list_registered(self) -> list[str]:
        """Return registered gateway_ids in insertion order."""
        return list(self._gateways.keys())

    def clear(self) -> None:
        """Remove all registered gateways. Primarily for test teardown."""
        self._gateways.clear()

    def __contains__(self, gateway_id: object) -> bool:
        return gateway_id in self._gateways

    def __len__(self) -> int:
        return len(self._gateways)
