"""Detach standard native inputs without cloning renderers or GPU resources."""

from collections import deque
from copy import copy
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.native_cmyk.buffer import NativeCmykBuffer
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels
from loomstroke.core.native_cmyk.selection_preview import NativeCmykSelectionPreview
from loomstroke.core.native_cmyk.selection_source import NativeSelectionSource
from loomstroke.ui.canvas.native_cmyk_display_frame import NativeCmykDisplayFrame

from .types import McpStructuredError, ReasonCode


def unsupported_snapshot_payload():
    return McpStructuredError(
        ReasonCode.CAPABILITY_UNAVAILABLE,
        "This render snapshot payload cannot be detached safely.",
        details={"capability": "snapshot_detachment"},
    )


def detach_native(native, memo):
    if native is None or type(native) is NativeCmykPixels:
        return native
    key = id(native)
    if key in memo:
        return memo[key]
    if type(native) is NativeCmykBuffer:
        # copy() snapshots under the same RLock. Capture the version in that
        # scope too; fresh identity/empty damage history require a full upload.
        with native._lock:
            result = native.copy()
            result.version = native.version
    elif type(native) is NativeSelectionSource:
        result = copy(native)
        result.base = detach_native(native.base, memo)
        result.bounds = QRect(native.bounds)
        result.transform = copy(native.transform)
        result.inks = SimpleNamespace(shape=native.inks.shape, dtype=native.inks.dtype)
        result.alpha = SimpleNamespace(shape=native.alpha.shape, dtype=native.alpha.dtype)
        result._dirty = deque(native._dirty, maxlen=native._dirty.maxlen)
        result._projection_identity = object()
    else:
        raise unsupported_snapshot_payload()
    memo[key] = result
    return result


def detach_native_preview(preview, memo):
    if preview is None:
        return None
    if type(preview) is not NativeCmykSelectionPreview:
        raise unsupported_snapshot_payload()
    result = copy(preview)
    # payload is an opaque UI identity, never read by project_selection/the
    # worker. Do not traverse or replace it with an invented payload contract.
    object.__setattr__(result, "native", detach_native(preview.native, memo))
    return result


def detach_display_frame(frame):
    if frame is None:
        return None
    if type(frame) is not NativeCmykDisplayFrame:
        raise unsupported_snapshot_payload()
    patches = []
    for rect, image in frame:
        if not isinstance(image, (QImage, np.ndarray)):
            raise unsupported_snapshot_payload()
        patches.append((QRect(rect) if isinstance(rect, QRect) else tuple(rect), image.copy()))
    # Standard publishers replace tiles instead of mutating a published
    # surface. Share that immutable surface, preserving lazy image creation.
    # Arbitrary external writes to its QImage objects are outside this contract.
    return NativeCmykDisplayFrame(
        patches,
        frame._image.copy() if frame._image is not None else None,
        frame.key,
        surface=frame.surface,
    )
