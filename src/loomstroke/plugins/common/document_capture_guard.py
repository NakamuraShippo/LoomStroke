"""Duck-typed access to the internal Canvas committed-capture capability."""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class DocumentCaptureLease(Protocol):
    @property
    def is_active(self) -> bool: ...

    def release(self) -> None: ...


class _NoCapturePort:
    def __init__(self) -> None:
        self.is_active = True

    def release(self) -> None:
        self.is_active = False


def begin_document_capture_edit(
    canvas: Any, previous: DocumentCaptureLease | None = None
) -> DocumentCaptureLease:
    """Acquire before live mutation, preserving old custom Canvas test doubles.

    An advertised but broken capability rejects the edit. Losing the returned
    lease never releases its token: failure is not proof of original recovery.
    """
    if previous is not None and getattr(previous, "is_active", True) is not False:
        raise RuntimeError("The previous transient document edit is unresolved.")
    begin = getattr(canvas, "_begin_transient_document_edit", None)
    if begin is None:
        return _NoCapturePort()
    if not callable(begin):
        raise RuntimeError("The document capture capability is not callable.")
    lease = begin()
    if not callable(getattr(lease, "release", None)):
        raise RuntimeError("The document capture capability returned an invalid lease.")
    return lease


def release_document_capture_edit(lease: DocumentCaptureLease | None) -> bool:
    """Release only after the caller has proved restore or final commit."""
    if lease is None:
        return True
    try:
        lease.release()
    except Exception:
        logger.exception("The transient document edit could not release its capture guard")
        return False
    return True
