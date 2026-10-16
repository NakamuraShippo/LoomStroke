"""Pointer gestures edit only the existing detached composition draft."""

import math

from PySide6.QtCore import QRectF, Qt

from loomstroke.core.comic_panels.drawing_state import panel_input_busy
from loomstroke.core.comic_panels.image_placement import PLACEMENT_KEY
from loomstroke.core.comic_panels.models import PanelFrameError


class FrameCompositionGesture:
    def __init__(self, overlay, controls):
        self.overlay, self.controls = overlay, controls
        self.enabled = False
        self.drag = None

    @property
    def workflow(self):
        return self.overlay.workflow

    def enable(self, enabled):
        if enabled and panel_input_busy(self.workflow.tool._canvas()):
            raise PanelFrameError("finish the current stroke or text composition first")
        guides = getattr(self.controls.controls, "guides", None)
        if enabled and getattr(guides, "_preedit", False):
            raise PanelFrameError("finish the current stroke or text composition first")
        if enabled and self.selected() is not None:
            self.workflow.repair.content_bounds(self.selected())
        self.enabled = bool(enabled and self.workflow.repair)
        self.drag = None
        self.overlay.set_input_enabled(self.enabled)
        self.overlay.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus if self.enabled else Qt.FocusPolicy.NoFocus
        )
        if self.enabled:
            self.overlay.setFocus()
        self.overlay.update()

    def selected(self):
        repair = self.workflow.repair
        item_id = self.controls.items.currentData()
        return item_id if repair and item_id in repair.items else None

    def widget_rect(self, item_id):
        x, y, w, h = self.workflow.repair.content_bounds(item_id)
        viewport = self.overlay.canvas_widget.viewport
        origin = self.overlay.canvas_origin()
        a, b = viewport.canvas_to_widget(x, y), viewport.canvas_to_widget(x + w, y + h)
        return QRectF(a[0] + origin.x(), a[1] + origin.y(), b[0] - a[0], b[1] - a[1])

    def handle_rect(self, item_id):
        part = self.workflow.repair.state.panel_parts[item_id]
        if PLACEMENT_KEY not in (part.values["metadata"] or {}):
            return QRectF()
        corner = self.widget_rect(item_id).bottomRight()
        visible = self.overlay.rect().adjusted(6, 6, -6, -6)
        corner.setX(min(visible.right(), max(visible.left(), corner.x())))
        corner.setY(min(visible.bottom(), max(visible.top(), corner.y())))
        return QRectF(corner.x() - 6, corner.y() - 6, 12, 12)

    def canvas_point(self, position):
        origin = self.overlay.canvas_origin()
        return self.overlay.canvas_widget.viewport.widget_to_canvas(
            position.x() - origin.x(), position.y() - origin.y()
        )

    def press(self, event):
        if not self.enabled or event.button() != Qt.MouseButton.LeftButton:
            return False
        if not self.workflow.draft or not self.workflow.repair:
            self.enable(False)
            return True
        if self.workflow.draft.page_id != self.workflow.page_id():
            self.workflow.cancel()
            self.controls.refresh()
            return True
        item = self.selected()
        if item is None:
            return True
        position = event.position()
        handle = self.handle_rect(item).contains(position)
        if not handle and not self.widget_rect(item).contains(position):
            return True
        repair = self.workflow.repair
        x, y, w, h = repair.content_bounds(item)
        start = self.canvas_point(position)
        center = x + w / 2, y + h / 2
        self.drag = (
            item,
            start,
            center,
            handle,
            repair.image_repairs.get(item),
            repair.moves.get(item),
        )
        return True

    def move(self, event):
        if not self.enabled:
            return False
        if not self.workflow.draft or not self.workflow.repair:
            self.enable(False)
            return True
        if self.drag is None:
            return True
        if self.workflow.draft.page_id != self.workflow.page_id():
            self.workflow.cancel()
            self.controls.refresh()
            return True
        item, start, center, handle, placement, move = self.drag
        repair = self.workflow.repair
        for mapping, value in ((repair.image_repairs, placement), (repair.moves, move)):
            if value is None:
                mapping.pop(item, None)
            else:
                mapping[item] = value
        point = self.canvas_point(event.position())
        if handle:
            factor = math.dist(center, point) / max(1e-9, math.dist(center, start))
            repair.scale_image(item, min(20, max(0.05, factor)))
        else:
            dx, dy = move or (0, 0)
            repair.move(item, dx + point[0] - start[0], dy + point[1] - start[1])
        self.overlay.update()
        self.workflow.refresh_view()
        return True

    def release(self, event):
        if not self.enabled:
            return False
        if event.button() == Qt.MouseButton.LeftButton:
            self.move(event)
            item = self.selected()
            self.drag = None
            if item is not None:
                result = self.controls.controls.run(self.workflow.confirm)
                if result is not False:
                    self.controls.adjust_image(item)
            self.controls.refresh()
        return True
