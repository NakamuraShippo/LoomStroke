"""One channel preview with bounded tiles and versioned dirty-region updates."""

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.native_cmyk.channel_image import NativeCmykChannelImage
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels


class NativeCmykChannelViewCache:
    def __init__(self):
        self._key = None
        self._image = None
        self._version = -1
        self.dirty = None

    def image(self, native, channel):
        height, width = native.alpha.shape
        key = (id(native), channel, width, height)
        dirty = QRect()
        version = getattr(native, "version", 0)
        if key != self._key:
            dirty = QRect(0, 0, width, height)
            self._image = QImage(width, height, QImage.Format.Format_RGBA8888)
        elif version != self._version:
            changes = native.dirty_since(self._version) if hasattr(native, "dirty_since") else None
            if changes is None:
                dirty = QRect(0, 0, width, height)
            else:
                for change in changes:
                    dirty = dirty.united(QRect(*change))
        if not dirty.isEmpty():
            painter = QPainter(self._image)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            try:
                for y in range(dirty.y(), dirty.y() + dirty.height(), 256):
                    for x in range(dirty.x(), dirty.x() + dirty.width(), 256):
                        rect = (
                            x,
                            y,
                            min(256, dirty.right() - x + 1),
                            min(256, dirty.bottom() - y + 1),
                        )
                        if hasattr(native, "region"):
                            pixels = native.region(rect)
                        else:
                            rw, rh = rect[2:]
                            pixels = NativeCmykPixels(
                                native.inks[y : y + rh, x : x + rw],
                                native.alpha[y : y + rh, x : x + rw],
                                native.profile,
                            )
                        painter.drawImage(x, y, NativeCmykChannelImage.image(pixels, channel))
            finally:
                painter.end()
        self._key, self._version = key, version
        self.dirty = (
            (dirty.x(), dirty.y(), dirty.width(), dirty.height()) if not dirty.isEmpty() else None
        )
        return self._image

    def release(self):
        self._key = None
        self._image = None
