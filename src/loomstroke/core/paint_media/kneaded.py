"""A kneaded eraser: a soft lump that lifts paint a little at a time and gets dirty."""

from __future__ import annotations

from weakref import proxy

import numpy as np

from .paper import sample_tooth

# Share of the paint one firm press takes at full strength, how much paint (in brush areas of
# opaque paint) the lump holds with its durability half way, and the grip a full lump keeps.
_LIFT = 0.24
_CAPACITY = 3.0
_GRIP_FLOOR = 0.12


class KneadedEraserBrush:
    """Press and drag to lighten; nothing is cut out, and a loaded lump lifts less.

    The lump is kneaded clean between strokes, so every stroke starts with full grip.
    """

    def __init__(self, stroke):
        self.stroke = proxy(stroke)
        radius = stroke.radius
        distance = np.hypot(stroke.x, stroke.y)
        # A pressed lump touches fully in the middle and gives way towards its rim.
        self.footprint = np.clip(
            (radius + 0.5 - distance) / max(1.5, radius * 0.45), 0.0, 1.0
        ).astype(np.float32)
        durability = stroke.wear[0]
        area = float(self.footprint.sum())
        # Half way is the reference capacity; the top of the range never fills up.
        self.capacity = (
            np.inf if durability >= 1 else _CAPACITY * area * 4.0 ** (2 * durability - 1)
        )
        self.taken = 0.0
        self.grip = 1.0

    def dab(self, x, y, pieces):
        stroke = self.stroke
        contact = stroke.contact.apply(self.footprint)
        hold = np.float32(_LIFT * stroke.opacity * self.grip)
        layer = stroke.layer
        for tile, sector, mask in pieces:
            if tile.tooth is None:
                sheet = tile.rect.translated(int(layer.offset_x), int(layer.offset_y))
                tile.tooth = sample_tooth(sheet, stroke.paper, tile.grain)
            # The lump reaches the tooth of the surface first; paint in the hollows stays
            # longer, which is what leaves a lifted area slightly grainy.
            amount = contact[mask] * (hold * (0.5 + 0.5 * tile.tooth[sector]))
            pixels = tile.pixels[sector]
            before = pixels[..., 3].astype(np.float32)
            # Premultiplied pixels: taking paint away scales colour and coverage together.
            pixels[:] = np.rint(pixels * (1.0 - amount)[..., None])
            self.taken += float((before - pixels[..., 3]).sum()) / 255.0
        self.grip = max(_GRIP_FLOOR, 1.0 - self.taken / self.capacity)
