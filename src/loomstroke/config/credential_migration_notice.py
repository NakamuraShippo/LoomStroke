"""Secret-free, process-local notices for incomplete credential migration."""

from __future__ import annotations

import logging
import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)
_REASONS = frozenset(
    {
        "read_failed",
        "decode_failed",
        "invalid_structure",
        "encryption_unavailable",
        "keyring_unavailable",
        "keyring_lookup_failed",
        "keyring_write_failed",
        "keyring_verify_failed",
        "keyring_conflict",
        "cleanup_failed",
    }
)


@dataclass(frozen=True, slots=True)
class MigrationNotice:
    app_name: str
    reason: str


class MigrationNoticeQueue:
    """Keep a failure until displayed, at most once per app in this process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: dict[str, MigrationNotice] = {}
        self._claimed: set[str] = set()
        self._delivered: set[str] = set()
        self._listeners: list[weakref.ReferenceType] = []

    def report(self, app_name: str, reason: str) -> None:
        notice = MigrationNotice(app_name, reason if reason in _REASONS else "migration_failed")
        with self._lock:
            if app_name in self._delivered:
                return
            self._pending.setdefault(app_name, notice)
            self._listeners = [
                reference for reference in self._listeners if reference() is not None
            ]
            callbacks = [callback for ref in self._listeners if (callback := ref()) is not None]
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logger.warning("Credential migration notice delivery is pending")

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        if getattr(callback, "__self__", None) is not None:
            reference: weakref.ReferenceType = weakref.WeakMethod(callback)
        else:
            reference = weakref.ref(callback)
        with self._lock:
            self._listeners.append(reference)

        def unsubscribe() -> None:
            with self._lock:
                self._listeners = [item for item in self._listeners if item is not reference]

        return unsubscribe

    def claim(self) -> MigrationNotice | None:
        with self._lock:
            for app_name, notice in self._pending.items():
                if app_name not in self._claimed:
                    self._claimed.add(app_name)
                    return notice
        return None

    def finish(self, notice: MigrationNotice, *, delivered: bool) -> None:
        with self._lock:
            if self._pending.get(notice.app_name) is not notice:
                return
            self._claimed.discard(notice.app_name)
            if delivered:
                self._pending.pop(notice.app_name)
                self._delivered.add(notice.app_name)


migration_notices = MigrationNoticeQueue()


def report_migration_failure(app_name: str, reason: str) -> None:
    migration_notices.report(app_name, reason)
