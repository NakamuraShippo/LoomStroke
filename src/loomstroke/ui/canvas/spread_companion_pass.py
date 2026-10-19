"""Compositing of the spread's facing page for the second blit (CT-06 B4-c4).

The spread viewport draws two pages in one composite viewport (spec §6.3.1).
The active page keeps the existing path untouched; this module owns the
*other* page: it composites it to its own target and hands that target to
the presenter, which blits it in the same render pass.

Two things make the second blit non-obvious:

**The offset lives in two places.** ``WgpuViewParams.translated_source``
moves the companion page inside a view-space target, so the composite lands
on the right target pixels.  The blit quad still has to cover only the
companion page's on-screen rectangle -- ``WgpuCompositor.render`` clears the
*whole* target with the scene background, so blitting a view-space target
full-screen would paint the entire viewport in the companion page's
background colour.  That is also why the active page is blitted as a canvas
quad rather than full-screen.

**The companion must not recomposite every frame.** A page is only edited
while it is active, so the companion's composite depends solely on the view
transform.  Re-running it per frame during a stroke would put a second full
composite on the 60fps path.  Caching is delegated to
:class:`WgpuProjectionCache`, which already implements exactly this policy
(rebuild on scene-signature or view change, reuse otherwise) and owns the
target's lifetime.  The compositor is *shared* with the active page, not
duplicated -- the same ownership contract as
``wgpu_native_preview_renderer``.

Failures return ``None`` rather than raising: losing the facing page must
never stop the user editing the active one (spec §13).
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .wgpu_backend.projection_cache import WgpuProjectionCache

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .render_scene import RenderSceneSnapshot
    from .wgpu_backend.types import WgpuRenderTargetHandle, WgpuViewParams

logger = logging.getLogger(__name__)

__all__ = [
    "SPREAD_QUALITY_SCALES",
    "SpreadCompanionFrame",
    "SpreadCompanionPass",
    "spread_quality_scale",
]

# ``performance/spread_inactive_page_quality`` -> composite scale factor
# (CT-06 B4-c5, spec §6.3.1).  Full is the default: a spread is read as one
# picture, so degrading the facing page by default would defeat the point.
# The scale drives both the composite resolution and the texture demand.
SPREAD_QUALITY_SCALES: dict[str, float] = {
    "full": 1.0,
    "balanced": 0.5,
    "low": 0.25,
}


def spread_quality_scale(value: object) -> float:
    """Scale factor for a stored quality setting; unknown values are full."""

    scale = SPREAD_QUALITY_SCALES.get(str(value))
    if scale is None:
        logger.warning("unknown spread quality %r; using full quality", value)
        return 1.0
    return scale


@dataclass(frozen=True, slots=True)
class SpreadCompanionFrame:
    """The facing page of a spread, ready to be composited.

    ``offset`` is where the companion page's canvas origin sits relative to
    the active page's origin, in canvas pixels.  Keeping the active page at
    the origin means the viewport, input mapping and Qt overlays need no
    change at all: only this frame knows the spread is wider than one page.

    ``revision`` distinguishes two scenes built for the *same* page, which
    happens when the caller invalidates and rebuilds after an edit.  Without
    it a rebuilt scene would look identical to the presenter's frame-reuse
    check and the new content would never reach the surface.
    """

    page_id: str
    scene: RenderSceneSnapshot
    offset: tuple[float, float]
    revision: int = 0
    quality_scale: float = 1.0

    def signature(self) -> tuple[Any, ...]:
        return (
            self.page_id,
            int(self.revision),
            round(float(self.offset[0]), 6),
            round(float(self.offset[1]), 6),
            round(float(self.quality_scale), 6),
        )


class SpreadCompanionPass:
    """Owns the companion page's composite target across frames."""

    def __init__(self, *, repaint: Any = None) -> None:
        self._repaint = repaint
        self._cache: WgpuProjectionCache | None = None
        self._compositor: Any | None = None
        self._target: WgpuRenderTargetHandle | None = None
        self._last_warnings: tuple[str, ...] = ()

    @property
    def last_warnings(self) -> tuple[str, ...]:
        return self._last_warnings

    @property
    def cached_target(self) -> WgpuRenderTargetHandle | None:
        """The composite target currently held (diagnostics and tests)."""
        return self._target

    def prepare(
        self,
        frame: SpreadCompanionFrame,
        *,
        bundle: Any,
        compositor: Any,
        view: WgpuViewParams | None,
    ) -> WgpuRenderTargetHandle | None:
        """Composite ``frame`` and return its target, reusing when unchanged.

        ``view`` is the *active* page's view: the companion's own view is
        derived from it here so callers never have to know that the offset
        has to run through the view affine.

        The quality factor shrinks that view's target.  It has no effect
        when view compositing is off -- there is no view to shrink, and the
        composite is already at canvas resolution.
        """

        if compositor is None:
            return None
        self._adopt_compositor(bundle, compositor)
        cache = self._cache
        if cache is None:
            return None

        companion_view = (
            view.translated_source(float(frame.offset[0]), float(frame.offset[1])).scaled_target(
                float(frame.quality_scale)
            )
            if view is not None
            else None
        )
        try:
            # invalidation=None: the companion page is not being edited, so
            # the cache's own signature check is the only rebuild trigger.
            result = cache.render(frame.scene, invalidation=None, view=companion_view)
        except Exception as exc:  # noqa: BLE001 - the active page must keep rendering
            logger.warning("spread companion composite failed for %s: %s", frame.page_id, exc)
            self.release()
            self._last_warnings = (f"spread companion composite failed: {exc}",)
            return None
        self._last_warnings = result.warnings
        self._target = result.target
        return result.target

    def release(self) -> None:
        """Drop the composite target.

        Safe to call repeatedly.  The shared compositor is never released
        here -- ``WgpuProjectionCache`` only releases a compositor it created
        itself, and this one is always injected.
        """

        cache = self._cache
        self._cache = None
        self._compositor = None
        self._target = None
        self._last_warnings = ()
        if cache is not None:
            with suppress(Exception):
                cache.release()

    # -- internals ---------------------------------------------------------

    def _adopt_compositor(self, bundle: Any, compositor: Any) -> None:
        if self._cache is not None and self._compositor is compositor:
            return
        # A new compositor means a new device: the cached target belongs to
        # the old one and must not be blitted into the new surface.
        self.release()
        try:
            options = {"repaint": self._repaint} if self._repaint is not None else {}
            self._cache = WgpuProjectionCache(bundle, compositor=compositor, **options)
        except Exception as exc:  # noqa: BLE001 - preview must never break editing
            logger.warning("spread companion cache unavailable: %s", exc)
            self._cache = None
            return
        self._compositor = compositor
