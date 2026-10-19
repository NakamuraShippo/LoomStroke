"""Conservative Windows desktop occlusion checks for the startup visual oracle."""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _visibility_api() -> Any:
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    api.ClientToScreen.restype = wintypes.BOOL
    api.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.GetClientRect.restype = wintypes.BOOL
    api.WindowFromPoint.argtypes = [wintypes.POINT]
    api.WindowFromPoint.restype = wintypes.HWND
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowThreadProcessId.restype = wintypes.DWORD
    return api


def native_surface_obscured(surface: Any) -> bool:
    """True only when foreign windows cover every sampled desktop location.

    An unavailable visibility query is not grounds for bypassing the oracle.
    Use HWND client coordinates so negative monitor origins and physical DPI
    coordinates follow the same Win32 contract as WindowFromPoint.
    """
    if os.name != "nt" or surface is None:
        return False
    try:
        if not surface.isVisible():
            return False
        return _foreign_windows_cover_surface(_visibility_api(), int(surface.winId()), os.getpid())
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        logger.debug("Native probe desktop visibility unavailable", exc_info=True)
        return False


def _foreign_windows_cover_surface(api: Any, hwnd: int, process_id: int) -> bool:
    origin, bounds = wintypes.POINT(), wintypes.RECT()
    if (
        not hwnd
        or not api.ClientToScreen(hwnd, ctypes.byref(origin))
        or not api.GetClientRect(hwnd, ctypes.byref(bounds))
    ):
        return False
    width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
    if width < 16 or height < 16:
        return False
    for numerator in (1, 2, 3):
        point = wintypes.POINT(
            origin.x + width * numerator // 4, origin.y + height * numerator // 4
        )
        covering = api.WindowFromPoint(point)
        if not covering or covering == hwnd:
            return False
        owner = wintypes.DWORD()
        if not api.GetWindowThreadProcessId(covering, ctypes.byref(owner)):
            return False
        if not owner.value or owner.value == process_id:
            return False
    return True
