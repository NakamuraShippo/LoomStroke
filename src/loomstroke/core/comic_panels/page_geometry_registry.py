"""Plugin-owned tree geometry, exposed as immutable records to core transforms."""

from dataclasses import dataclass
from typing import ClassVar

from .models import PanelFrameError


@dataclass(frozen=True)
class PanelPageGeometry:
    before_points: tuple
    after_points: tuple
    after_boundaries: tuple


class PanelPageGeometryRegistry:
    _resolvers: ClassVar[dict] = {}

    @classmethod
    def register(cls, resolver, *, owner):
        cls._resolvers.setdefault(resolver, set()).add(owner)

    @classmethod
    def unregister(cls, resolver, *, owner):
        owners = cls._resolvers.get(resolver)
        if owners is not None:
            owners.discard(owner)
            if not owners:
                del cls._resolvers[resolver]

    @classmethod
    def resolve(cls, layout, mode, page_size):
        for resolver in tuple(cls._resolvers):
            result = resolver(layout, mode, page_size)
            if result is not None:
                return result
        raise PanelFrameError("convex frame geometry hook is unavailable")
