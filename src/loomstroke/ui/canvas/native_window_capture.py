"""Owned, bounded Win32 capture of the native surface's composed client pixels."""

from __future__ import annotations

import ctypes
import logging
import os
from contextlib import ExitStack
from ctypes import wintypes
from functools import lru_cache
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.ui._dock_safe import is_valid

logger = logging.getLogger(__name__)
_MAX_CAPTURE_BYTES = 128 * 1024 * 1024
# WinUser.h: PW_CLIENTONLY | PW_RENDERFULLCONTENT (Windows 8.1+).
_PRINT_FLAGS = 0x00000001 | 0x00000002


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


@lru_cache(maxsize=1)
def _capture_api() -> tuple[Any, Any]:
    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user.GetDC.argtypes, user.GetDC.restype = [wintypes.HWND], wintypes.HDC
    user.ReleaseDC.argtypes, user.ReleaseDC.restype = [wintypes.HWND, wintypes.HDC], ctypes.c_int
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user.PrintWindow.restype = wintypes.BOOL
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetClientRect.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
    gdi.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(_BitmapInfoHeader),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi.CreateDIBSection.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.DeleteObject.argtypes, gdi.DeleteObject.restype = [wintypes.HANDLE], wintypes.BOOL
    gdi.DeleteDC.argtypes, gdi.DeleteDC.restype = [wintypes.HDC], wintypes.BOOL
    return user, gdi


def capture_composed_native_surface(surface: Any) -> QImage | None:
    """Capture our HWND only; callers still have to validate the smoke pixels.

    PrintWindow is synchronous, so this is limited to startup verification of
    our own native child, never the normal frame/pen path or a foreign HWND.
    The owned QImage outlives all DC/bitmap resources used to obtain it.
    """
    if os.name != "nt" or not is_valid(surface):
        return None
    try:
        if not surface.isVisible():
            return None
        user, gdi = _capture_api()
        hwnd = int(surface.winId())
        owner = wintypes.DWORD()
        if not hwnd or not user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner)):
            return None
        if owner.value != os.getpid():
            return None
        return _capture_client(hwnd, user, gdi)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, MemoryError):
        logger.debug("Composed native window capture unavailable", exc_info=True)
        return None


def _capture_client(hwnd: int, user: Any, gdi: Any) -> QImage | None:
    bounds = wintypes.RECT()
    if not user.GetClientRect(hwnd, ctypes.byref(bounds)):
        return None
    width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
    size = width * height * 4
    if width < 16 or height < 16 or size > _MAX_CAPTURE_BYTES:
        return None
    header = _BitmapInfoHeader(
        ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, size, 0, 0, 0, 0
    )
    with ExitStack() as resources:
        source = user.GetDC(hwnd)
        if not source:
            raise OSError("Could not obtain the native window device context")
        resources.callback(user.ReleaseDC, hwnd, source)
        memory = gdi.CreateCompatibleDC(source)
        if not memory:
            raise OSError("Could not allocate the capture device context")
        resources.callback(gdi.DeleteDC, memory)
        pixels = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(
            source, ctypes.byref(header), 0, ctypes.byref(pixels), None, 0
        )
        if not bitmap:
            raise OSError("Could not allocate the capture bitmap")
        resources.callback(gdi.DeleteObject, bitmap)
        previous = gdi.SelectObject(memory, bitmap)
        if previous in (None, 0, ctypes.c_void_p(-1).value):
            raise OSError("Could not select the capture bitmap")
        resources.callback(gdi.SelectObject, memory, previous)
        if not pixels.value or not user.PrintWindow(hwnd, memory, _PRINT_FLAGS):
            return None
        buffer = (ctypes.c_ubyte * size).from_address(pixels.value)
        # BI_RGB has BGRX bytes; GDI does not promise a meaningful alpha byte.
        return QImage(
            memoryview(buffer).cast("B"), width, height, width * 4, QImage.Format.Format_RGB32
        ).copy()
