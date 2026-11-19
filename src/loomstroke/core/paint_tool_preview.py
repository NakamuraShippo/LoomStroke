"""Display-only image and sparse upload receipts for an active paint tool stroke."""

from __future__ import annotations

from itertools import count
from weakref import ref

from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter

from .channels.channel_pixel_policy import ChannelPixelPolicy
from .commands_layer_lookup import find_layer

_PREVIEW_VERSIONS = count(-1, -1)


def _cache_shadow(canvas, layer, image):
    canvas_ref = ref(canvas)

    def release(layer_ref):
        owner = canvas_ref()
        shadow = getattr(owner, "_paint_tool_shadow", None)
        if shadow and shadow[0] is layer_ref:
            owner._paint_tool_shadow = None

    canvas._paint_tool_shadow = (ref(layer, release), layer.image.cacheKey(), image)


def discard_detached_paint_shadow(canvas):
    """A replaced document can still have external references to its old layers."""
    shadow = getattr(canvas, "_paint_tool_shadow", None)
    if shadow:
        layer = shadow[0]()
        if layer is None or find_layer(canvas, layer.id) is not layer:
            canvas._paint_tool_shadow = None


class PaintToolPreview:
    def __init__(self, session):
        self.owner = ref(session)
        layer = session.layer
        self.base_version = self.version = layer.texture_version
        self.base_key = session.source_key
        self.image = None
        self.completed = None
        self.committed = False
        self.versions = set()
        self.tiles = {}
        self.dirty = QRect()

    def update(self, session, origins):
        if not origins:
            return
        if self.image is None:
            cached = getattr(session.canvas, "_paint_tool_shadow", None)
            session.canvas._paint_tool_shadow = None
            if cached and cached[0]() is session.layer and cached[1] == session.source_key:
                self.image = cached[2]
            else:
                self.image = session.layer.image.copy()
        self.version = next(_PREVIEW_VERSIONS)
        self.versions.add(self.version)
        dirty = QRect()
        painter = QPainter(self.image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        try:
            for tile in origins:
                rect = tile.rect
                painter.drawImage(rect.topLeft(), ChannelPixelPolicy.image(tile.pixels))
                self.tiles[rect.x(), rect.y()] = (self.version, rect)
                dirty = dirty.united(rect)
        finally:
            painter.end()
        self.dirty = self.dirty.united(dirty)
        session.refresh(dirty)

    def finish(self, session, *, committed=False, stale=False):
        layer = session.layer
        self.owner = None
        self.committed = committed
        if committed and self.image is not None:
            _cache_shadow(session.canvas, layer, self.image)
        self.image = None
        if not stale:
            self.completed = (layer.texture_version, layer.image.cacheKey())

    def configure_snapshot(self, layer, snapshot):
        # An export snapshot uses the authoritative image; only viewport callers enter here.
        owner = self.owner() if self.owner is not None else None
        if owner is not None and not owner.current():
            owner.close(stale=True, notify=False)
        if self.image is not None:
            snapshot.image = self.image
            snapshot.texture_version = self.version
        elif self.completed != (layer.texture_version, layer.image.cacheKey()):
            return
        snapshot.paint_upload = self
        snapshot.paint_upload_key = (snapshot.texture_version, snapshot.image.cacheKey())

    def rects_since(self, previous):
        if previous == self.base_version:
            if self.owner is None and not self.committed:
                return ()
            rects = [rect for _, rect in self.tiles.values()]
        elif previous in self.versions:
            restore = self.owner is None and not self.committed
            rects = [rect for version, rect in self.tiles.values() if restore or version < previous]
        else:
            return None
        # Adjacent tiles on one row make a single upload. Gaps are untouched.
        rows = []
        for rect in sorted(rects, key=lambda r: (r.y(), r.x())):
            if rows and rows[-1].y() == rect.y() and rows[-1].right() + 1 == rect.x():
                rows[-1] = rows[-1].united(rect)
            else:
                rows.append(QRect(rect))
        return tuple(rows)
