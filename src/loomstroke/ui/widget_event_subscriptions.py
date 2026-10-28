"""Keep Python EventBus subscriptions within a Qt widget's lifetime."""

from __future__ import annotations

import weakref
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject
from shiboken6 import isValid


class WidgetEventSubscriptions:
    """Retain delivery callbacks and detach their exact bus tokens on destruction."""

    def __init__(self, owner: QObject) -> None:
        self.handlers: list[Callable[[Any], Any]] = []
        self.tokens: list[Any] = []
        self._subscriptions: list[tuple[Any, str, Any]] = []
        self._owner = weakref.ref(owner)
        self._disposed = False
        owner.destroyed.connect(self.dispose)

    def subscribe(self, bus: Any, event: str, handler: Callable[[Any], Any]) -> None:
        subscribe = getattr(bus, "on", None) or getattr(bus, "subscribe", None)
        if not callable(subscribe) or self._disposed:
            return
        owner_ref = self._owner
        handler_ref = weakref.WeakMethod(handler)
        subscriptions_ref = weakref.ref(self)

        def deliver(payload: Any) -> Any:
            owner = owner_ref()
            subscriptions = subscriptions_ref()
            callback = handler_ref()
            if (
                subscriptions is not None
                and not subscriptions._disposed
                and owner is not None
                and isValid(owner)
                and callback is not None
            ):
                return callback(payload)
            return None

        token = subscribe(event, deliver)
        # EventBus holds weak references. Each bus needs its own retained
        # callback, even when the original bound methods compare equal.
        self.handlers.append(deliver)
        self.tokens.append(token)
        self._subscriptions.append((bus, event, token))

    def dispose(self) -> None:
        self._disposed = True
        subscriptions, self._subscriptions = self._subscriptions, []
        for bus, event, token in subscriptions:
            # Older panel consumers may have explicitly detached and cleared
            # these compatibility lists before Qt destroys the widget.
            if token not in self.tokens:
                continue
            if callable(token):
                token()
            else:
                unsubscribe = getattr(bus, "off", None) or getattr(bus, "unsubscribe", None)
                if callable(unsubscribe):
                    unsubscribe(event, token)
        self.tokens.clear()
        self.handlers.clear()
