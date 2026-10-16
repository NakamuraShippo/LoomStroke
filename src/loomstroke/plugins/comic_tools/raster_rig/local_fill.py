"""Image-preserving local attachment fill and junction composition."""

import cv2
import numpy as np

from .compose import _clip_rect
from .local_join import reconstruct_join
from .opening import InkFillOpeningComposer


def attachment_colour(body, stroke, root, normal, radius=64):
    """Nearest interior non-ink colour; transparent interiors stay transparent."""
    h, w = body.shape[:2]
    previous = None
    stable = 0
    for depth in range(3, radius + 1):
        x, y = np.rint(np.asarray(root) - np.asarray(normal) * depth).astype(int)
        if not (1 <= x < w - 1 and 1 <= y < h - 1):
            continue
        pixels = body[y - 1 : y + 2, x - 1 : x + 2]
        weights = stroke[y - 1 : y + 2, x - 1 : x + 2]
        if np.all(pixels[:, :, 3] == 0):
            return None
        valid = (pixels[:, :, 3] >= 240) & (weights < 0.8)
        if valid.all() and np.ptp(pixels[:, :, :3].astype(int), axis=(0, 1)).max() <= 8:
            colour = np.median(pixels[valid, :3], axis=0)
            stable = (
                stable + 1 if previous is not None and np.max(np.abs(colour - previous)) <= 4 else 1
            )
            previous = colour
            if stable >= 3:
                return tuple(int(v) for v in colour)
        else:
            previous, stable = None, 0
    return None


class LocalFillComposer(InkFillOpeningComposer):
    """Local-colour raster composition shared by conversion, re-edit and lab."""

    def update(self, pose, *, opening=None):
        root, _, normal = self.guide.frame_at(pose.root_u)
        # Guide points are body-local; colour/stroke canvases are page-local.
        # Production crops retain a nonzero origin, unlike the standalone lab.
        root = np.asarray(root) + np.asarray(self.guide.origin)
        colour = attachment_colour(self.body_canvas, self._stroke_canvas, root, normal)
        # Dark filled bodies have no non-ink samples in the existing classifier.
        if (
            colour is None
            and self.stroke_info.get("mode") == "ink_fallback"
            and self.guide.fill_ratio >= 0.5
        ):
            x, y = np.rint(np.asarray(root) - np.asarray(normal) * 12).astype(int)
            if 0 <= y < self.body_canvas.shape[0] and 0 <= x < self.body_canvas.shape[1]:
                pixel = self.body_canvas[y, x]
                if pixel[3] >= 240:
                    colour = tuple(int(v) for v in pixel[:3])
        self.fill_rgb = colour
        self._join_repair = None
        self.last_repair_count = 0
        dirty = super().update(pose, opening=opening)
        self._repair_join()
        return dirty

    def _tail_interior(self, patch):
        src = self._interior_src()
        mapping = self._deformer.last_map
        tail = self._last_tail
        if src is None or mapping is None or tail is None:
            return None
        rect = _clip_rect(tail.rect, *self.canvas_size)
        if rect is None:
            return None
        mx, my, origin_x, origin_y = mapping
        x, y, w, h = rect
        # Clipping to the body and the canvas can both shrink the posed patch.
        # Remap in its original frame, then extract the exact destination ROI.
        interior = cv2.remap(src, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT)
        interior = cv2.dilate((interior > 0).astype(np.uint8), np.ones((3, 3), np.uint8))
        aligned = np.zeros((h, w), bool)
        dx, dy = x - origin_x, y - origin_y
        x0, y0 = max(0, dx), max(0, dy)
        x1, y1 = min(interior.shape[1], dx + w), min(interior.shape[0], dy + h)
        if x1 > x0 and y1 > y0:
            aligned[y0 - dy : y1 - dy, x0 - dx : x1 - dx] = interior[y0:y1, x0:x1] > 0
        return aligned & (patch[:, :, 3] < 255)

    def _repair_join(self):
        mask, tail = self._last_mask, self._last_tail
        if mask is None or tail is None:
            return
        rect = _clip_rect(mask.rect, *self.canvas_size)
        if rect is None:
            return
        x, y, w, h = rect
        patch = np.zeros((h, w, 4), np.uint8)
        tx, ty, tw, th = tail.rect
        x0, y0, x1, y1 = max(x, tx), max(y, ty), min(x + w, tx + tw), min(y + h, ty + th)
        if x1 > x0 and y1 > y0:
            patch[y0 - y : y1 - y, x0 - x : x1 - x] = tail.rgba[
                y0 - ty : y1 - ty, x0 - tx : x1 - tx
            ]
        output = self.output[y : y + h, x : x + w]
        repaired, changed = reconstruct_join(
            output,
            self.body_canvas[y : y + h, x : x + w],
            patch,
            self._stroke_canvas[y : y + h, x : x + w],
        )
        output[:] = repaired
        self.last_repair_count = int(changed.sum())
        if self.last_repair_count:
            self._join_repair = (rect, repaired[:, :, :3].copy(), changed)

    def _repair_region(self, rect, rgba):
        repair = getattr(self, "_join_repair", None)
        if repair is None:
            return rgba
        (rx, ry, rw, rh), rgb, changed = repair
        x, y, w, h = rect
        x0, y0, x1, y1 = max(x, rx), max(y, ry), min(x + w, rx + rw), min(y + h, ry + rh)
        if x1 > x0 and y1 > y0:
            sub = rgba[y0 - y : y1 - y, x0 - x : x1 - x, :3]
            selected = changed[y0 - ry : y1 - ry, x0 - rx : x1 - rx]
            sub[selected] = rgb[y0 - ry : y1 - ry, x0 - rx : x1 - rx][selected]
        return rgba

    def body_region(self, rect):
        # Give both layers the same repaired ink RGB; their original alpha
        # and layer blending remain intact. Source images are never modified.
        return self._repair_region(rect, super().body_region(rect))

    def preview_tail_patch(self):
        patch = super().preview_tail_patch()
        if patch is None:
            return None
        rect, rgba = patch
        return rect, self._repair_region(rect, rgba.copy())
