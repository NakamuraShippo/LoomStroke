"""Per-page zoom and pan (CT-06 B4-c6).

A comic page is worked on at a zoom that suits *that* page, so switching
pages should not drop the reader where the previous page happened to be
(spec §6.3).  ``PageService`` already stores a per-page ``view_state``;
this controller is what finally puts the viewport into it.

**The outgoing page is captured on the incoming page's event.**
``ACTIVE_PAGE_CHANGED`` fires after the switch has settled, and the
viewport still holds the view the user is leaving -- so the controller
remembers which page that was and stores it before restoring the new one.
The pre-switch notification closes transient edits; viewport persistence
continues to use the settled event so this controller owns the view state.

**Nothing is restored for a page that has never been visited.** Its stored
state is absent, and the current view is a better guess than any default:
resetting the zoom on first visit is exactly the jump this feature exists
to avoid.

Writes are silent (``mark_dirty=False``): where the user left the zoom is
navigation, not an edit.  The value still travels into the document the
next time it is saved.

**Off by default** (``view/per_page_zoom_enabled``).  Carrying one view
across pages is what every existing document was drawn with, so this only
changes behaviour for users who ask for it.  The switch is read per event,
so turning it on or off takes effect on the next page switch without
re-wiring anything.

The same handler also keeps a *spread* still while the edit target moves
inside it.  The canvas draws whichever page is active at its origin, so
switching from one page of a spread to the other slides the whole drawing
by a page width; the viewport is panned by the same amount in the opposite
direction to cancel it (CT-06 §6.3).  Only within one spread -- moving to
the next spread is a page turn, and the reader expects to arrive at it.
A restored per-page view wins: it is what the user explicitly left behind.

**Whatever else happens, the page being edited stays in view.** Holding the
spread still only works while both pages fit on screen: zoomed in on one
page, cancelling the swap would leave the reader looking at the page they
just left, with the edit target itself off screen.  Right binding hits this
constantly, because the page after a left (recto) page starts the next
spread.  So the cancelling is dropped whenever it would push the active
page out of view, which lands the reader on the same spot of the new page
instead.  Zoomed out nothing moves; zoomed in you keep your place.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from loomstroke.core.events import Events

logger = logging.getLogger(__name__)

__all__ = ["VIEWPORT_VIEW_STATE_KEY", "PageViewStateController"]

# One key inside the page's shared ``view_state``; guides and rulers own
# their own keys beside it.
VIEWPORT_VIEW_STATE_KEY = "viewport"

_VIEWPORT_FIELDS = ("center_x", "center_y", "zoom", "rotation")


class PageViewStateController:
    """Saves the viewport when leaving a page and restores it on return."""

    def __init__(
        self,
        *,
        page_service: Any,
        viewport_provider: Callable[[], Any],
        event_bus: Any | None = None,
        on_restored: Callable[[], None] | None = None,
        enabled_provider: Callable[[], bool] | None = None,
        spread_anchor_provider: (
            Callable[[], tuple[tuple[str, ...], float, float] | None] | None
        ) = None,
        active_page_rect_provider: (
            Callable[[], tuple[float, float, float, float] | None] | None
        ) = None,
    ) -> None:
        self._page_service = page_service
        self._viewport_provider = viewport_provider
        self._on_restored = on_restored
        self._enabled_provider = enabled_provider
        self._spread_anchor_provider = spread_anchor_provider
        self._active_page_rect_provider = active_page_rect_provider
        self._current_page_id: str | None = self._active_page_id()
        self._spread_anchor = self._read_spread_anchor()
        # EventBus holds handlers weakly; without these references the
        # subscriptions would be collected and page switches would stop
        # carrying their view.
        self._handlers: list[Callable[[Any], None]] = []
        self._subscribe(event_bus)

    # -- public API --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        provider = self._enabled_provider
        if provider is None:
            return False
        try:
            return bool(provider())
        except Exception:
            logger.debug("per-page zoom setting unavailable", exc_info=True)
            return False

    def save_current_page(self) -> None:
        """Store the viewport under the page it belongs to.

        Called on page switch, and worth calling before a save so the
        document carries an up-to-date view.
        """

        if not self.enabled:
            return
        page_id = self._current_page_id
        if not page_id:
            return
        state = self._viewport_state()
        if state is None:
            return
        merge = getattr(self._page_service, "update_page_view_state", None)
        if not callable(merge):
            return
        try:
            merge(page_id, {VIEWPORT_VIEW_STATE_KEY: state}, mark_dirty=False)
        except Exception:
            logger.warning("could not store the view for page %s", page_id, exc_info=True)

    def restore(self, page_id: str) -> bool:
        """Apply a page's stored viewport; ``False`` if it has none."""

        if not self.enabled:
            return False
        stored = self._stored_viewport(page_id)
        if stored is None:
            return False
        viewport = self._viewport()
        if viewport is None:
            return False
        try:
            viewport.set_state(stored)
        except Exception:
            logger.warning("could not restore the view for page %s", page_id, exc_info=True)
            return False
        if self._on_restored is not None:
            self._on_restored()
        return True

    # -- event wiring ------------------------------------------------------

    def _subscribe(self, event_bus: Any | None) -> None:
        if event_bus is None:
            return
        subscribe = getattr(event_bus, "subscribe", None) or getattr(event_bus, "on", None)
        if not callable(subscribe):
            return
        bindings = (
            (Events.ACTIVE_PAGE_CHANGED, self._on_active_page_changed),
            (Events.DOC_LOADED, self._on_document_reset),
            (Events.DOC_CLOSED, self._on_document_reset),
        )
        for event, handler in bindings:
            self._handlers.append(handler)
            subscribe(event, handler)

    def _on_active_page_changed(self, payload: Any) -> None:
        page_id = ""
        if isinstance(payload, Mapping):
            page_id = str(payload.get("page_id", ""))
        if not page_id:
            page_id = self._active_page_id() or ""
        if not page_id or page_id == self._current_page_id:
            return
        # The page id is tracked even while the feature is off, so switching
        # it on mid-session stores against the page the user is actually on.
        self.save_current_page()
        previous_anchor = self._spread_anchor
        self._current_page_id = page_id
        restored = self.restore(page_id)
        self._spread_anchor = self._read_spread_anchor()
        if not restored:
            self._keep_the_spread_still(previous_anchor)

    def _keep_the_spread_still(
        self,
        previous: tuple[tuple[str, ...], float, float] | None,
    ) -> None:
        """Place the view after a switch, in order of what matters.

        1. Inside one spread, cancel the swap so the drawing does not move.
        2. If that would push the page being edited off screen, drop the
           cancelling and stay where we were relative to the page: the same
           spot, now on the new page.
        3. If the page is *still* off screen (the reader had already panned
           away from it), pull the centre onto it.
        """

        viewport = self._viewport()
        if viewport is None:
            return
        origin = self._center(viewport)
        if origin is None:
            return

        delta = self._swap_delta(previous)
        if delta != (0.0, 0.0):
            self._set_center(viewport, (origin[0] - delta[0], origin[1] - delta[1]))
            if not self._active_page_visible(viewport):
                self._set_center(viewport, origin)

        if not self._active_page_visible(viewport):
            self._set_center(viewport, self._clamped_to_active_page(viewport, origin))

        if self._center(viewport) != origin and self._on_restored is not None:
            self._on_restored()

    def _swap_delta(
        self,
        previous: tuple[tuple[str, ...], float, float] | None,
    ) -> tuple[float, float]:
        """How far the active page moved inside its spread, if at all.

        Zero across a page turn: arriving at the next spread is the point of
        turning, so there is nothing to cancel there.
        """

        current = self._spread_anchor
        if previous is None or current is None:
            return (0.0, 0.0)
        previous_pages, previous_x, previous_y = previous
        current_pages, current_x, current_y = current
        if not current_pages or previous_pages != current_pages:
            return (0.0, 0.0)
        return (current_x - previous_x, current_y - previous_y)

    def _active_page_visible(self, viewport: Any) -> bool:
        """Whether any of the page being edited is on screen.

        ``True`` when it cannot be told: a guess is not worth moving the
        user's view over.
        """

        rect = self._active_page_rect()
        if rect is None or rect[2] <= 0 or rect[3] <= 0:
            return True
        visible = self._visible_rect(viewport)
        if visible is None:
            return True
        ax, ay, aw, ah = visible
        bx, by, bw, bh = rect
        return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

    def _clamped_to_active_page(
        self,
        viewport: Any,
        fallback: tuple[float, float],
    ) -> tuple[float, float]:
        del viewport
        rect = self._active_page_rect()
        if rect is None or rect[2] <= 0 or rect[3] <= 0:
            return fallback
        left, top, width, height = rect
        return (
            min(max(fallback[0], left), left + width),
            min(max(fallback[1], top), top + height),
        )

    @staticmethod
    def _center(viewport: Any) -> tuple[float, float] | None:
        try:
            state = viewport.get_state()
            return (float(state["center_x"]), float(state["center_y"]))
        except Exception:
            logger.debug("viewport state unavailable", exc_info=True)
            return None

    @staticmethod
    def _set_center(viewport: Any, center: tuple[float, float]) -> None:
        try:
            viewport.set_state({"center_x": center[0], "center_y": center[1]})
        except Exception:
            logger.warning("could not move the view", exc_info=True)

    def _active_page_rect(self) -> tuple[float, float, float, float] | None:
        provider = self._active_page_rect_provider
        if provider is None:
            return None
        try:
            return provider()
        except Exception:
            logger.debug("active page rect unavailable", exc_info=True)
            return None

    @staticmethod
    def _visible_rect(viewport: Any) -> tuple[float, float, float, float] | None:
        getter = getattr(viewport, "get_visible_rect", None)
        if not callable(getter):
            return None
        try:
            rect = getter()
            return (
                float(rect.x()),
                float(rect.y()),
                float(rect.width()),
                float(rect.height()),
            )
        except Exception:
            logger.debug("visible rect unavailable", exc_info=True)
            return None

    def _read_spread_anchor(self) -> tuple[tuple[str, ...], float, float] | None:
        provider = self._spread_anchor_provider
        if provider is None:
            return None
        try:
            return provider()
        except Exception:
            logger.debug("spread anchor unavailable", exc_info=True)
            return None

    def _on_document_reset(self, payload: Any) -> None:
        del payload
        # The pages of the previous document are gone; storing the viewport
        # against them now would write into whatever reused those ids.
        self._current_page_id = self._active_page_id()
        self._spread_anchor = self._read_spread_anchor()

    # -- internals ---------------------------------------------------------

    def _active_page_id(self) -> str | None:
        getter = getattr(self._page_service, "get_active_page_id", None)
        if not callable(getter):
            return None
        try:
            return str(getter()) or None
        except Exception:
            logger.debug("active page unavailable", exc_info=True)
            return None

    def _viewport(self) -> Any | None:
        try:
            return self._viewport_provider()
        except Exception:
            logger.debug("viewport unavailable", exc_info=True)
            return None

    def _viewport_state(self) -> dict[str, float] | None:
        viewport = self._viewport()
        if viewport is None:
            return None
        try:
            state = viewport.get_state()
        except Exception:
            logger.debug("viewport state unavailable", exc_info=True)
            return None
        if not isinstance(state, Mapping):
            return None
        # Widget size is the widget's, not the page's: restoring it would
        # describe a window the user may no longer have.
        return {
            field: float(state[field])
            for field in _VIEWPORT_FIELDS
            if isinstance(state.get(field), (int, float))
        }

    def _stored_viewport(self, page_id: str) -> dict[str, float] | None:
        getter = getattr(self._page_service, "get_page_view_state", None)
        if not callable(getter):
            return None
        try:
            view_state = getter(page_id)
        except Exception:
            logger.debug("view state unavailable for page %s", page_id, exc_info=True)
            return None
        if not isinstance(view_state, Mapping):
            return None
        stored = view_state.get(VIEWPORT_VIEW_STATE_KEY)
        if not isinstance(stored, Mapping):
            return None
        values = {
            field: float(stored[field])
            for field in _VIEWPORT_FIELDS
            if isinstance(stored.get(field), (int, float))
        }
        # A zoom of zero would collapse the transform; treat a malformed
        # entry as "never visited" rather than applying half of it.
        if not values or float(values.get("zoom", 0.0)) <= 0.0:
            return None
        return values
