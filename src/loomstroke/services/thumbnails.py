"""Shared thumbnail helper for the GenerationHistoryStore Gallery panel.

Originally lived as a private helper inside the ComfyUI main panel
(``_generate_thumbnail``). Extracted here so the AI Effect plugin's
gallery adapter can produce the **same** WebP shape (128x128 @ q70)
the panel does — without that, AI Effect rows show a placeholder
where ComfyUI rows show a thumbnail, which broke at-a-glance browsing
of the Gallery.

The helper is thread-safe by design (no QPixmap, only QImage / QBuffer)
so callers can hand it off to ``asyncio.to_thread`` and avoid blocking
the Qt event loop on the WebP encode step.
"""

from __future__ import annotations

import logging
from typing import Any, cast

logger = logging.getLogger(__name__)

# Match the ComfyUI panel exactly — changing these would split the
# Gallery into "old vs new shape" rows after an upgrade. If the size
# ever needs revisiting, do it in lockstep across both call sites.
THUMBNAIL_MAX_DIMENSION = 128
THUMBNAIL_FORMAT = "WEBP"
THUMBNAIL_QUALITY = 70


def generate_gallery_thumbnail(image_bytes: bytes) -> bytes | None:
    """Decode a PNG and re-encode as a 128px WebP thumbnail.

    Returns the WebP bytes on success, or ``None`` when the input
    fails to decode (corrupt PNG / non-image). ``None`` is the signal
    callers use to skip ``GenerationHistoryStore.save_thumbnail`` and
    leave ``record.thumbnail_path`` unset rather than write a broken
    file.

    Implementation notes:
    - Uses :class:`PySide6.QtGui.QImage` (not QPixmap) so the function
      is safe to call off the GUI thread; QPixmap requires an
      QApplication context.
    - Aspect ratio is preserved (``KeepAspectRatio``) — landscape and
      portrait outputs both fit inside the 128x128 bounding box rather
      than getting squashed.
    """
    # Local imports keep PySide6 out of any consumer that imports the
    # module without ever calling the function (pytest collection
    # without Qt, headless CI, etc.).
    from PySide6.QtCore import QBuffer, QIODevice, Qt  # noqa: PLC0415
    from PySide6.QtGui import QImage  # noqa: PLC0415

    qimg = QImage()
    qimg.loadFromData(image_bytes)
    if qimg.isNull():
        logger.debug("generate_gallery_thumbnail: input failed to decode")
        return None
    thumb = qimg.scaled(
        THUMBNAIL_MAX_DIMENSION,
        THUMBNAIL_MAX_DIMENSION,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    thumb.save(buf, cast(Any, THUMBNAIL_FORMAT), THUMBNAIL_QUALITY)
    return bytes(cast(Any, buf.data()))


__all__ = [
    "THUMBNAIL_FORMAT",
    "THUMBNAIL_MAX_DIMENSION",
    "THUMBNAIL_QUALITY",
    "generate_gallery_thumbnail",
]
