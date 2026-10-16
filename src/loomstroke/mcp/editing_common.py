"""Shared validation and serialized mutation boundary for MCP editing tools."""

from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from PySide6.QtCore import QCoreApplication, QThread

from .types import McpStructuredError, ReasonCode


def invalid(message: str, field: str | None = None) -> McpStructuredError:
    return McpStructuredError(
        ReasonCode.INVALID_ARGUMENT,
        message,
        code=-32602,
        details={"argument": field} if field else {},
    )


def finite_number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise invalid(f"'{field}' must be a finite number.", field)
    try:
        number = float(value)
    except OverflowError as exc:
        raise invalid(f"'{field}' must be a finite number.", field) from exc
    if not math.isfinite(number):
        raise invalid(f"'{field}' must be a finite number.", field)
    if minimum is not None and number < minimum:
        raise invalid(f"'{field}' must be at least {minimum}.", field)
    if maximum is not None and number > maximum:
        raise invalid(f"'{field}' must be at most {maximum}.", field)
    return number


def check_gui_thread() -> None:
    app = QCoreApplication.instance()
    if (app is not None and QThread.currentThread() != app.thread()) or (
        app is None and threading.current_thread() is not threading.main_thread()
    ):
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "Canvas editing must run on the application's main thread.",
        )


def current_revision(service: Any) -> str | None:
    if getattr(service, "canvas_service", None) is None:
        return None
    from .editing_readiness import known_document_closed  # noqa: PLC0415

    if known_document_closed(service):
        return None
    context = service.get_edit_context({})
    return str(context["contextDigest"])


def _check_revision(service: Any, expected: str | None) -> None:
    if expected is not None and current_revision(service) != expected:
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "The document changed; obtain a fresh edit context before retrying.",
            details={"expectedRevision": expected},
        )


@asynccontextmanager
async def edit_operation(
    service: Any,
    name: str,
    arguments: Mapping[str, Any],
    *,
    require_canvas: bool = True,
) -> AsyncIterator[Any]:
    """Confirm against a stable document and never join a GUI stroke transaction."""

    check_gui_thread()
    expected = arguments.get("expectedRevision")
    if expected is not None and (not isinstance(expected, str) or not expected):
        raise invalid("'expectedRevision' must be a non-empty string.", "expectedRevision")
    lock = getattr(service, "_editing_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        service._editing_lock = lock
    async with lock:
        canvas = getattr(service, "canvas_service", None)
        if require_canvas and canvas is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE, "An editable canvas is required."
            )
        if require_canvas:
            from .editing_readiness import document_state  # noqa: PLC0415

            if document_state(service).get("present") is not True:
                raise McpStructuredError(
                    ReasonCode.CAPABILITY_UNAVAILABLE, "No editable document is open."
                )
        if canvas is not None and getattr(canvas, "_in_transaction", False):
            raise McpStructuredError(
                ReasonCode.CONFLICT, "Finish the current canvas transaction before editing."
            )
        _check_revision(service, expected)
        before_revision = current_revision(service)
        summary = {
            key: arguments[key]
            for key in (
                "layerId",
                "expectedRevision",
                "toolId",
                "path",
                "discardChanges",
                "action",
                "pageId",
                "templateId",
                "masterId",
                "effectId",
                "index",
                "format",
                "includeRasterArtwork",
                "brushId",
                "paletteId",
                "swatchId",
            )
            if key in arguments
        }
        points = arguments.get("points")
        if isinstance(points, list | tuple):
            summary["pointCount"] = len(points)
        await service.safety.require_confirmation(name, summary)
        check_gui_thread()
        if getattr(service, "canvas_service", None) is not canvas:
            raise McpStructuredError(ReasonCode.CONFLICT, "The editable canvas changed.")
        if require_canvas and document_state(service).get("present") is not True:
            raise McpStructuredError(
                ReasonCode.CONFLICT, "The document was closed during confirmation."
            )
        _check_revision(service, before_revision)
        if canvas is not None and getattr(canvas, "_in_transaction", False):
            raise McpStructuredError(
                ReasonCode.CONFLICT, "Finish the current canvas transaction before editing."
            )
        yield canvas
