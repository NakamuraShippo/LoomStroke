"""Centralised, read-on-demand secret provider for AI Gateways.

Phase 8a (NanoBanana / paid Gateway integration) "no app-side key
handling" policy:

- Plugins (UI panels, AI Effect controller, etc.) MUST NOT read
  credentials directly. They call ``AIGateway.submit(...)`` and the
  gateway's submit path uses ``SecretProvider`` internally.
- ``SecretProvider`` decrypts the secret only at the moment of use,
  yields it through a context manager, and the local binding falls
  out of scope at the end of the ``with`` block. The provider never
  caches secrets on its instance attributes.
- All paid Gateways (NanoBanana, OpenAI, Stability, future) share
  this single path so a security audit of the plugin tree just has
  to verify "no plugin imports keyring or SecurityConfig directly".

Implementation note:
    ``SecretProvider`` is a thin orchestrator over ``SecurityConfig``.
    It exists primarily to enforce a *narrow API surface* that the
    rest of the codebase calls. Adding new backends (HSM, hardware
    token, etc.) becomes a single-class change here without touching
    any gateway implementation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loomstroke.config.security_config import SecurityConfig

logger = logging.getLogger(__name__)


class SecretNotConfigured(Exception):
    """Raised when a gateway requests a credential that is not stored.

    Gateway implementations catch this and translate it to
    ``ErrorClass.AUTH`` (or equivalent) so the UI can surface a
    "Set API key in Preferences → AI Effect" toast / banner.
    """

    def __init__(self, service: str, username: str = "api") -> None:
        self.service = service
        self.username = username
        super().__init__(f"Secret not configured for {service!r} ({username!r})")


class SecretProvider:
    """Read-on-demand credential provider.

    A single instance is created at app bootstrap and injected into
    every Gateway constructor. The provider holds NO secret values on
    its attributes — every ``use`` / ``has`` call goes through
    ``SecurityConfig`` which delegates to the OS keyring (Windows
    Credential Manager / macOS Keychain / Linux Secret Service).
    """

    def __init__(self, app_name: str = "LoomStroke") -> None:
        self._app_name = app_name
        # Lazy-construct the SecurityConfig: keyring init does a tiny
        # amount of I/O, no point paying for it until the first
        # access. Once built, the manager itself does not retain the
        # secret value beyond a get_credential call.
        self._security: SecurityConfig | None = None

    def _manager(self) -> SecurityConfig:
        if self._security is None:
            from loomstroke.config.security_config import SecurityConfig as _SC  # noqa: PLC0415

            self._security = _SC(app_name=self._app_name)
        return self._security

    @contextmanager
    def use(self, service: str, username: str = "api") -> Iterator[str]:
        """Context manager: decrypt → yield → invalidate.

        Usage::

            with secrets.use("nanobanana") as api_key:
                client = genai.Client(api_key=api_key)
                response = client.models.generate(...)
            # api_key local has been ``del``-ed; client cleanup
            # releases its own copy on next garbage collection.

        ``service`` is the gateway identifier (mirrors keyring's
        first argument); ``username`` defaults to ``"api"`` for
        single-credential gateways. Multi-credential gateways
        (e.g. OpenAI organisation + project) pass distinct usernames.

        Raises ``SecretNotConfigured`` if no credential is stored
        for the requested ``(service, username)`` pair. The gateway
        is expected to translate this to its UI-friendly AUTH error.
        """
        manager = self._manager()
        # Bool-only existence check first so the secret is not even
        # read when we know it is absent.
        if not manager.has_credential(service, username):
            raise SecretNotConfigured(service, username)
        secret = manager.get_credential(service, username)
        if not secret:
            # Race / edge case: has_credential said True but
            # get_credential returned empty. Treat as missing.
            raise SecretNotConfigured(service, username)
        try:
            yield secret
        finally:
            # Drop the local reference. Python str is immutable so
            # the underlying bytes may linger until GC, but no
            # Python-level reference remains and the next allocation
            # can reuse the slot. The OS keyring holds the canonical
            # value — that's where the security boundary lives.
            del secret

    def has(self, service: str, username: str = "api") -> bool:
        """Bool-only existence check.

        Used by UI status indicators ("API key configured ✓"). Reads
        keyring metadata only — the value is fetched and immediately
        ``del``-ed inside ``SecurityConfig.has_credential`` so it is
        never returned to the caller of this method.
        """
        return self._manager().has_credential(service, username)

    def set(self, service: str, value: str, username: str = "api") -> bool:
        """Persist a secret to keyring.

        Used by Preferences "Save" actions. Returns True on success.
        The value is forwarded straight to ``store_credential`` and
        is not retained on this provider instance. Callers (e.g. the
        APIKeyDialog) should ``del`` their local binding immediately
        after calling.
        """
        return self._manager().store_credential(service, username, value)

    def clear(self, service: str, username: str = "api") -> bool:
        """Delete a stored secret from keyring (DPAPI on Windows).

        Used by Preferences "Clear" actions. Idempotent — succeeds
        whether or not the credential was previously stored.
        """
        return self._manager().delete_credential(service, username)


__all__ = ["SecretNotConfigured", "SecretProvider"]
