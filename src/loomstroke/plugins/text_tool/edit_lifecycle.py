"""Text layer locks and cleanup at the owning page's edit boundary."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from loomstroke.core.events import Events

logger = logging.getLogger(__name__)


class TextEditLifecycle:
    """Keep text edits attached to their page and honor ancestor locks."""

    def __init__(self, tool: Any) -> None:
        self._tool = tool
        self._edit_page_id: str | None = None
        self._events: Any = None
        self._tokens: list[tuple[str, Any]] = []
        # EventBus retains callbacks weakly, so keep the bound method alive.
        self._page_changing_handler = self._on_active_page_changing
        self._page_handler = self._on_active_page_changed

    def connect(self) -> None:
        self.disconnect()
        events = getattr(self._tool.host_api, "events", None)
        subscribe = getattr(events, "on", None)
        if callable(subscribe):
            self._events = events
            for event, handler in (
                (Events.ACTIVE_PAGE_CHANGING, self._page_changing_handler),
                (Events.ACTIVE_PAGE_CHANGED, self._page_handler),
            ):
                self._tokens.append((event, subscribe(event, handler)))

    def disconnect(self) -> None:
        unsubscribe = getattr(self._events, "off", None)
        if callable(unsubscribe):
            for event, token in self._tokens:
                unsubscribe(event, token)
        self._events = None
        self._tokens.clear()

    def begin(self) -> None:
        self._edit_page_id = self._current_page_id()

    def clear(self) -> None:
        self._edit_page_id = None

    def same_page(self) -> bool:
        return self._edit_page_id is None or self._edit_page_id == self._current_page_id()

    def _current_page_id(self) -> str | None:
        pages = getattr(self._tool.host_api, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        if not callable(getter):
            return None
        try:
            page_id = getter()
        except Exception:
            logger.debug("Could not verify the text edit page", exc_info=True)
            return None
        return str(page_id) if page_id is not None else None

    def _on_active_page_changing(self, payload: Any) -> None:
        if not isinstance(payload, Mapping):
            return
        target_id = payload.get("page_id")
        if target_id and str(target_id) != self._current_page_id():
            self._cancel_page_edit()

    def _on_active_page_changed(self, _payload: Any) -> None:
        # Backstop for hosts that only announce a completed page switch.
        if not self.same_page():
            self._cancel_page_edit()

    def _cancel_page_edit(self) -> None:
        tool = self._tool
        tool._flush_prop_transaction()
        tool._transform_controller.discard()
        if tool._inline_state is not None:
            tool._finish_inline_edit(commit=False)
        tool._clear_inline_selection_if_owned()
        tool._reset_state()
        input_service = getattr(tool.host_api, "input", None)
        release = getattr(input_service, "release_capture", None)
        if callable(release):
            try:
                release(tool._tool_id)
            except Exception:
                logger.debug("Could not release the text edit pointer capture", exc_info=True)
        self.clear()

    def can_edit_layer(self, layer_id: str | None) -> bool:
        from .frame_flow_controller import story_allows_edit  # noqa: PLC0415

        if not story_allows_edit(self._tool):
            return False
        if not layer_id:
            return True
        canvas = getattr(self._tool.host_api, "canvas", None)
        getter = getattr(canvas, "get_layer_by_id", None)
        if not callable(getter):
            # Existing SimpleCanvasService/MoveTool compatibility accessor; no
            # pixel snapshots or mutation are needed to inspect a parent chain.
            getter = getattr(canvas, "_get_layer_by_id", None)
        if not callable(getter):
            info_getter = getattr(canvas, "get_layer_info", None)
            if not callable(info_getter):
                return True
            try:
                info = info_getter(layer_id)
            except Exception:
                logger.debug("Could not verify the text layer lock", exc_info=True)
                return False
            return (
                isinstance(info, Mapping)
                and not bool(info.get("locked", False))
                and bool(info.get("visible", True))
            )
        return self._ancestors_allow_edit(getter, layer_id)

    @staticmethod
    def _ancestors_allow_edit(getter: Any, layer_id: str) -> bool:
        seen: set[str] = set()
        current_id: str | None = layer_id
        while current_id:
            if current_id in seen:
                return False
            seen.add(current_id)
            try:
                layer = getter(current_id)
            except Exception:
                logger.debug("Could not verify the text layer lock", exc_info=True)
                return False
            if (
                layer is None
                or bool(getattr(layer, "locked", False))
                or not bool(getattr(layer, "visible", True))
            ):
                return False
            parent_id = getattr(layer, "parent_id", None)
            current_id = str(parent_id) if parent_id else None
        return True
