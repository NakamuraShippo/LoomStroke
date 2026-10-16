"""Spread state for the canvas viewport (CT-06 B4-c4).

Sits between the page/book services and the presenter: it decides *which*
page faces the one being edited and *where* it sits, then hands the canvas
a ready-made :class:`SpreadCompanionFrame`.  The canvas widget keeps no
spread logic of its own.

**The active page stays at the canvas origin.** The facing page is placed
relative to it, so the viewport transform, input mapping, guides and every
Qt overlay behave exactly as they do in single-page mode.  Only the
companion frame knows the drawing is wider than one page.

**Recomputation is event-driven, not per-frame.** ``companion_frame()`` is
called on every presented frame, so resolving spreads, reading page
metadata and building a scene there would put O(pages) work on the 60fps
path.  Instead the result is cached and invalidated from the events that
can actually change it: page list, active page, document reload, and the
Single/Spread toggle.

The binding direction comes from the document's book metadata, which is
also what the Pages panel's binding control writes: both sides read the one
value that is saved and exported.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from loomstroke.core.book_layout_service import BookLayoutService
from loomstroke.core.book_spread_geometry import (
    SpreadGeometry,
    SpreadPageRect,
    compute_spread_geometry,
)
from loomstroke.core.book_spread_layout import BookLayout, SpreadDescriptor, resolve_spreads
from loomstroke.core.events import Events
from loomstroke.ui.canvas.spread_companion_pass import SpreadCompanionFrame
from loomstroke.ui.canvas.spread_page_scene_source import SpreadPageSceneSource

if TYPE_CHECKING:  # pragma: no cover - typing only
    from loomstroke.host_api import CanvasSpec

logger = logging.getLogger(__name__)

__all__ = ["SpreadViewController", "SpreadViewState"]


class SpreadViewState:
    """What the canvas needs to know about the current spread.

    Kept separate from the companion frame because the frame exists only
    when there is a facing *page*: a cover sits next to an empty slot, and
    the active page's rectangle is still needed there (B4-c2's frame
    highlight and input gate).
    """

    __slots__ = ("active_rect", "companion", "companion_rect", "geometry", "spread_page_ids")

    def __init__(
        self,
        *,
        geometry: SpreadGeometry,
        active_rect: SpreadPageRect,
        companion_rect: SpreadPageRect | None,
        companion: SpreadCompanionFrame | None,
        spread_page_ids: tuple[str, ...] = (),
    ) -> None:
        self.geometry = geometry
        self.active_rect = active_rect
        self.companion_rect = companion_rect
        self.companion = companion
        self.spread_page_ids = spread_page_ids

    def anchor(self) -> tuple[tuple[str, ...], float, float]:
        """Which spread this is, and where the active page sits inside it.

        The canvas always draws the active page at its origin, so switching
        pages inside a spread moves the whole drawing by the difference
        between the two slots' positions.  Callers compare two anchors to
        cancel that out (CT-06 §6.3).
        """
        return (self.spread_page_ids, self.active_rect.x, self.active_rect.y)

    def active_canvas_rect(self) -> tuple[float, float, float, float]:
        """The active page as ``(x, y, w, h)`` in canvas space.

        Always anchored at the origin: the canvas *is* the active page.
        """
        return (0.0, 0.0, self.active_rect.width, self.active_rect.height)

    def spread_canvas_rect(self) -> tuple[float, float, float, float]:
        """Both slots as one rectangle in canvas space (``Fit Spread``).

        Includes an empty slot: fitting the cover should still show the
        blank it faces, otherwise the page jumps sideways the moment a
        facing page appears.
        """
        active = self.active_canvas_rect()
        companion = self.companion_canvas_rect()
        if companion is None:
            return active
        left = min(active[0], companion[0])
        top = min(active[1], companion[1])
        right = max(active[0] + active[2], companion[0] + companion[2])
        bottom = max(active[1] + active[3], companion[1] + companion[3])
        return (left, top, right - left, bottom - top)

    def companion_canvas_rect(self) -> tuple[float, float, float, float] | None:
        """The facing slot in the active page's canvas space, if any.

        Present even for an empty slot -- the canvas still has to keep tool
        input out of it (spec §6.3).
        """
        rect = self.companion_rect
        if rect is None:
            return None
        return (
            rect.x - self.active_rect.x,
            rect.y - self.active_rect.y,
            rect.width,
            rect.height,
        )


class SpreadViewController:
    """Derives the spread the active page belongs to, and its facing scene."""

    def __init__(
        self,
        *,
        page_service: Any,
        event_bus: Any | None = None,
        book_layout_provider: Callable[[], BookLayout] | None = None,
        quality_scale_provider: Callable[[], float] | None = None,
    ) -> None:
        self._page_service = page_service
        self._book_layout_provider = book_layout_provider
        self._quality_scale_provider = quality_scale_provider
        self._layout_service = BookLayoutService()
        self._scene_source = SpreadPageSceneSource()
        self._enabled = False
        self._dirty = True
        self._state: SpreadViewState | None = None
        self._last_companion: SpreadCompanionFrame | None = None
        self._revision = 0
        # EventBus holds handlers weakly: without these attributes the
        # subscriptions would be collected and the spread would silently
        # stop following the document.
        self._handlers: list[Callable[[Any], None]] = []
        self._subscribe(event_bus)

    # -- public API --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._dirty = True
            self._state = None
            return
        # Leaving the spread must release the facing page's layer images;
        # the presenter drops its composite target on the same frame.
        self.release()

    def state(self) -> SpreadViewState | None:
        """Current spread, or ``None`` when the canvas shows a single page."""

        if not self._enabled:
            return None
        # A cached state that names a different active page is worse than no
        # cache: callers use it to place the viewport, and the check costs an
        # attribute read.  It also frees them from depending on this
        # controller's ACTIVE_PAGE_CHANGED handler running first.
        if not self._dirty and self._state is not None:
            active_id = self._active_page_id()
            if active_id and self._state.active_rect.page_id != active_id:
                self._dirty = True
        if self._dirty:
            self._state = self._build_state()
            self._dirty = False
        return self._state

    def anchor(self) -> tuple[tuple[str, ...], float, float] | None:
        """Where the active page sits in its spread, or ``None`` outside one."""

        state = self.state()
        return state.anchor() if state is not None else None

    def companion_frame(self) -> SpreadCompanionFrame | None:
        state = self.state()
        return state.companion if state is not None else None

    def invalidate(self) -> None:
        """Rebuild on the next query."""

        self._dirty = True

    def release(self) -> None:
        self._state = None
        self._last_companion = None
        self._dirty = True
        self._scene_source.release()

    # -- event wiring ------------------------------------------------------

    def _subscribe(self, event_bus: Any | None) -> None:
        if event_bus is None:
            return
        subscribe = getattr(event_bus, "subscribe", None)
        if not callable(subscribe):
            return
        bindings = (
            (Events.PAGE_VIEW_MODE_CHANGED, self._on_view_mode_changed),
            (Events.ACTIVE_PAGE_CHANGED, self._on_active_page_changed),
            (Events.PAGES_CHANGED, self._on_pages_changed),
            (Events.DOC_CHANGED, self._on_document_changed),
            (Events.DOC_LOADED, self._on_document_reset),
            (Events.DOC_CLOSED, self._on_document_reset),
        )
        for event, handler in bindings:
            self._handlers.append(handler)
            subscribe(event, handler)

    def _on_view_mode_changed(self, payload: Any) -> None:
        mode = ""
        if isinstance(payload, Mapping):
            mode = str(payload.get("mode", ""))
        elif payload is not None:
            mode = str(getattr(payload, "mode", ""))
        self.set_enabled(mode == "spread")

    def _on_active_page_changed(self, payload: Any) -> None:
        del payload
        # The page just switched away from was persisted before this event,
        # but the scene built from its *previous* snapshot is now stale --
        # and the new active page must stop being drawn as the facing one.
        self._scene_source.invalidate()
        self._dirty = True

    def _on_pages_changed(self, payload: Any) -> None:
        del payload
        self._dirty = True

    def _on_document_changed(self, payload: Any) -> None:
        # DOC_CHANGED carries every content edit, which would put a spread
        # re-derivation on the stroke path.  Only the book settings reshape
        # the spread -- and page attributes count, because a role change can
        # move the first body page and re-pair everything after it.
        change_type = str(getattr(payload, "change_type", ""))
        if change_type in ("book.layout_changed", "pages.book_attributes_changed"):
            self._dirty = True

    def _on_document_reset(self, payload: Any) -> None:
        del payload
        self.release()

    # -- derivation --------------------------------------------------------

    def _build_state(self) -> SpreadViewState | None:
        try:
            summaries = list(self._page_service.list_pages())
            active_id = str(self._page_service.get_active_page_id())
        except Exception:
            logger.warning("spread view: page list unavailable", exc_info=True)
            return None
        if not summaries or not active_id:
            return None

        page_ids = [str(summary.id) for summary in summaries]
        spread = self._spread_for(page_ids, active_id)
        if spread is None:
            return None

        sizes = self._page_sizes(summaries)
        fallback = sizes.get(active_id) or next(iter(sizes.values()), None)
        if fallback is None:
            return None
        geometry = compute_spread_geometry(spread, sizes, fallback_size=fallback)

        active_rect = geometry.rect_for_page(active_id)
        if active_rect is None:
            return None
        companion_rect = next(
            (rect for rect in geometry.rects if rect is not active_rect),
            None,
        )
        return SpreadViewState(
            geometry=geometry,
            active_rect=active_rect,
            companion_rect=companion_rect,
            companion=self._companion_for(active_rect, companion_rect),
            spread_page_ids=tuple(spread.page_ids()),
        )

    def _spread_for(self, page_ids: Sequence[str], active_id: str) -> SpreadDescriptor | None:
        attributes = self._page_attributes(page_ids)
        layout = self._book_layout()
        for spread in resolve_spreads(page_ids, layout, attributes):
            if active_id in spread.page_ids():
                return spread
        return None

    def _book_layout(self) -> BookLayout:
        provider = self._book_layout_provider
        if provider is None:
            return BookLayout()
        try:
            return provider()
        except Exception:
            logger.warning("spread view: book layout unavailable; using defaults", exc_info=True)
            return BookLayout()

    def _page_attributes(self, page_ids: Sequence[str]) -> dict[str, Any]:
        metadata_by_id: dict[str, Mapping[str, Any] | None] = {}
        for page_id in page_ids:
            page = self._page_snapshot(page_id)
            metadata_by_id[page_id] = None if page is None else getattr(page, "metadata", None)
        return self._layout_service.resolve_page_attributes_by_id(metadata_by_id)

    @staticmethod
    def _page_sizes(summaries: Sequence[Any]) -> dict[str, tuple[float, float]]:
        sizes: dict[str, tuple[float, float]] = {}
        for summary in summaries:
            spec = getattr(summary, "canvas", None)
            width = float(getattr(spec, "width", 0) or 0)
            height = float(getattr(spec, "height", 0) or 0)
            if width > 0 and height > 0:
                sizes[str(summary.id)] = (width, height)
        return sizes

    def _companion_for(
        self,
        active_rect: SpreadPageRect,
        companion_rect: SpreadPageRect | None,
    ) -> SpreadCompanionFrame | None:
        if companion_rect is None or companion_rect.page_id is None:
            return None
        page = self._page_snapshot(companion_rect.page_id)
        if page is None:
            return None
        spec = self._fallback_canvas_spec()
        if spec is None:
            return None
        scene = self._scene_source.scene_for_page(page, fallback_canvas_spec=spec)
        if scene is None:
            return None
        offset = (
            companion_rect.x - active_rect.x,
            companion_rect.y - active_rect.y,
        )
        quality_scale = self._quality_scale()
        previous = self._last_companion
        if (
            previous is not None
            and previous.page_id == scene.page_id
            and previous.scene is scene.scene
            and previous.offset == offset
            and previous.quality_scale == quality_scale
        ):
            # A re-derivation that changed nothing must not bump the
            # revision: that would force the presenter to re-present.
            return previous
        self._revision += 1
        companion = SpreadCompanionFrame(
            page_id=scene.page_id,
            scene=scene.scene,
            offset=offset,
            revision=self._revision,
            quality_scale=quality_scale,
        )
        self._last_companion = companion
        return companion

    def _quality_scale(self) -> float:
        """Composite scale for the facing page (CT-06 B4-c5).

        Read per rebuild rather than cached, so a preferences change takes
        effect on the next invalidation instead of the next document load.
        """

        provider = self._quality_scale_provider
        if provider is None:
            return 1.0
        try:
            return float(provider())
        except Exception:
            logger.warning("spread quality unavailable; using full quality", exc_info=True)
            return 1.0

    def _active_page_id(self) -> str | None:
        getter = getattr(self._page_service, "get_active_page_id", None)
        if not callable(getter):
            return None
        try:
            return str(getter()) or None
        except Exception:
            logger.debug("spread view: active page unavailable", exc_info=True)
            return None

    def _page_snapshot(self, page_id: str) -> Any | None:
        getter = getattr(self._page_service, "get_page_snapshot", None)
        if not callable(getter):
            return None
        try:
            return getter(page_id)
        except Exception:
            logger.warning("spread view: no snapshot for page %s", page_id, exc_info=True)
            return None

    def _fallback_canvas_spec(self) -> CanvasSpec | None:
        getter = getattr(self._page_service, "get_active_canvas_spec", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            logger.warning("spread view: active canvas spec unavailable", exc_info=True)
            return None
