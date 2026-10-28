"""Runtime-only ownership of resident pages during a save/rebind boundary."""

from __future__ import annotations


class PageResidencyGuard:
    """A fresh instance represents one document; old leases cannot release it."""

    def __init__(self) -> None:
        self._leases: set[object] = set()
        self._preserve = False

    @property
    def eviction_allowed(self) -> bool:
        return not self._leases and not self._preserve

    def acquire(self) -> PageResidencyLease:
        token = object()
        self._leases.add(token)
        return PageResidencyLease(self, token)


class PageResidencyLease:
    """Keep captured pages resident until their new source is known to be safe."""

    def __init__(self, guard: PageResidencyGuard, token: object) -> None:
        self.guard = guard
        self._token = token

    def preserve(self) -> None:
        self.guard._preserve = True

    def rebound(self) -> None:
        self.guard._preserve = False

    def release(self) -> None:
        self.guard._leases.discard(self._token)
