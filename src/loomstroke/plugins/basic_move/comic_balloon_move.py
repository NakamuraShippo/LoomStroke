"""Route Move gestures through the comic balloon's existing movement rules."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from loomstroke.core.commands import _layer_transform, _resolve_target_layer
from loomstroke.plugins.comic_tools.model import (
    balloon_record_from_object,
    balloon_text_link_from_object,
    move_balloon_record,
)
from loomstroke.plugins.comic_tools.panel_balloon_plan import UNIT_KEY
from loomstroke.plugins.comic_tools.raster_parts import find_part_layer, part_layer_balloon_id
from loomstroke.plugins.comic_tools.service import ComicPageController
from loomstroke.plugins.vector_tools.common import copy_vector_object

logger = logging.getLogger(__name__)


@dataclass
class _Drag:
    canvas: Any
    page_id: Any
    state: Any
    record: Any
    origin: tuple[float, float]
    originals: list[tuple[Any, Any]]
    part: Any
    delta: tuple[float, float] = (0.0, 0.0)


class ComicBalloonMove:
    """One balloon, including its linked text and raster parts, per gesture."""

    def __init__(self, tool):
        self.tool = tool
        self.controller = ComicPageController()
        self.drag: _Drag | None = None

    def _page_id(self):
        pages = getattr(self.tool.host_api, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        return getter() if callable(getter) else None

    @staticmethod
    def _layer_available(canvas, layer, *, require_visible=False):
        seen = set()
        while layer is not None:
            if layer.id in seen or getattr(layer, "locked", False):
                return False
            seen.add(layer.id)
            if require_visible and not getattr(layer, "visible", True):
                return False
            parent_id = getattr(layer, "parent_id", None)
            layer = _resolve_target_layer(canvas, parent_id) if parent_id else None
        return True

    def _target(self, canvas, pt):
        tool = self.tool
        active = _resolve_target_layer(canvas, None)
        unit = (getattr(active, "metadata", None) or {}).get(UNIT_KEY)
        if tool._active_layer_is_group():
            return str(unit.get("balloon_id") or "") if isinstance(unit, Mapping) else None
        hit = tool._find_vector_hit(pt)
        if hit is not None:
            record = balloon_record_from_object(hit.obj)
            return record.balloon_id if record else balloon_text_link_from_object(hit.obj)
        return self._placement_target(canvas, pt) or part_layer_balloon_id(active)

    def _placement_target(self, canvas, pt):
        tool = self.tool
        getter = getattr(canvas, "get_vector_objects", None)
        if not callable(getter):
            return None
        # Raster balloons have a pathless record carrier. Test its placement,
        # using its visible part layer for occlusion rather than the carrier.
        for layer_id, obj in reversed(list(getter())):
            record = balloon_record_from_object(obj)
            if record is None:
                continue
            part = find_part_layer(canvas, record.balloon_id)
            visual = part or _resolve_target_layer(canvas, layer_id)
            if visual is None or not self._layer_available(canvas, visual, require_visible=True):
                continue
            rects = [record.body_rect]
            if record.tail.enabled and record.tail_rect is not None:
                rects.append(record.tail_rect)
            if any(
                x <= pt.pos[0] <= x + w and y <= pt.pos[1] <= y + h for x, y, w, h in rects
            ) and not tool._is_occluded_at(canvas, visual.id, pt):
                return record.balloon_id
        return None

    def begin(self, pt):
        tool = self.tool
        canvas = getattr(tool.host_api, "canvas", None)
        if canvas is None:
            return False
        balloon_id = self._target(canvas, pt)
        if not balloon_id:
            return False
        state = self.controller.read_state(canvas, page_size=canvas.get_canvas_size())
        record = state.balloons.get(balloon_id)
        if record is None:
            return True  # An orphan part must not be independently displaced.
        originals = []
        for layer_id, obj in canvas.get_vector_objects(include_hidden=True):
            linked = balloon_text_link_from_object(obj)
            body = balloon_record_from_object(obj)
            if linked == balloon_id or (body and body.balloon_id == balloon_id):
                layer = _resolve_target_layer(canvas, layer_id)
                if layer is not None:
                    originals.append((layer, copy_vector_object(obj)))
        part = find_part_layer(canvas, balloon_id)
        if any(not self._layer_available(canvas, layer) for layer, _ in originals) or (
            part is not None and not self._layer_available(canvas, part, require_visible=True)
        ):
            return True
        self.drag = _Drag(
            canvas,
            self._page_id(),
            state,
            record,
            (float(pt.pos[0]), float(pt.pos[1])),
            originals,
            part,
        )
        x, y, w, h = record.body_rect
        tool._vector_drag_bounds = (x, y, x + w, y + h)
        tool._clear_layer_transform_overlay(clear_selection=True)
        tool._clear_vector_selection_on_miss()
        tool._mode = "comic_balloon"
        tool._dragging = True
        tool._start_pos = self.drag.origin
        tool.host_api.input.pointer_capture(tool.descriptor().id)
        return True

    def _valid(self, drag):
        return (
            getattr(self.tool.host_api, "canvas", None) is drag.canvas
            and self._page_id() == drag.page_id
            and all(
                _resolve_target_layer(drag.canvas, layer.id) is layer
                and self._layer_available(drag.canvas, layer)
                for layer, _ in drag.originals
            )
            and (
                drag.part is None
                or (
                    _resolve_target_layer(drag.canvas, drag.part.id) is drag.part
                    and self._layer_available(drag.canvas, drag.part)
                )
            )
        )

    def _delta(self, pt, drag):
        dx, dy = pt.pos[0] - drag.origin[0], pt.pos[1] - drag.origin[1]
        if self.tool.settings.pixel_perfect:
            dx, dy = float(round(dx)), float(round(dy))
        return self.tool._apply_snap_vector(dx, dy)

    def update(self, pt):
        drag = self.drag
        if drag is None:
            return
        if not self._valid(drag):
            self.cancel()
            self.tool._finish_pointer()
            self.tool._clear_last_click_state()
            return
        dx, dy = self._delta(pt, drag)
        if (dx, dy) == drag.delta:
            return
        drag.delta = (dx, dy)
        for layer, obj in drag.originals:
            moved = self.tool._translate_vector_object(obj, dx, dy)
            moved = replace(obj, paths=moved.paths, meta=moved.meta)
            drag.canvas.replace_vector_object(obj.id, moved, layer_id=layer.id, recompose=False)
        if drag.part is not None:
            transform = _layer_transform(drag.part)
            drag.canvas.set_layer_transform_preview(
                drag.part.id, transform.with_translation(transform.tx + dx, transform.ty + dy)
            )
        self._notify(drag)

    def _notify(self, drag):
        # Nested managed layers need their own versions, and unrelated vector
        # layers must not be rasterized again for this balloon's preview.
        drag.canvas.bump_layer_texture_versions([layer.id for layer, _ in drag.originals])
        self.tool._recompose_canvas()

    def _restore(self, drag):
        for layer, obj in drag.originals:
            # Restore the original page's layer objects even if the canvas was
            # reused for a new page; do not replace that page's objects by ID.
            if _resolve_target_layer(drag.canvas, layer.id) is layer:
                drag.canvas.replace_vector_object(obj.id, obj, layer_id=layer.id, recompose=False)
            else:
                for index, current in enumerate(layer.vector_objects):
                    if current.id == obj.id:
                        layer.vector_objects[index] = obj
                        break
        if drag.part is not None:
            drag.part._preview_transform = None
            drag.part._preview_transform_version += 1
        if getattr(self.tool.host_api, "canvas", None) is drag.canvas:
            self._notify(drag)

    def finish(self, pt):
        drag = self.drag
        if drag is None:
            return
        valid = self._valid(drag)
        dx, dy = self._delta(pt, drag)
        self._restore(drag)
        self.drag = None
        is_click = not (dx or dy) and (
            self.tool._distance_from_start(pt) <= self.tool._click_distance_threshold_px
        )
        self.tool._finish_pointer()
        if not valid:
            self.tool._clear_last_click_state()
            return
        if dx or dy:
            command = self.controller.update_balloon_command(
                drag.state, move_balloon_record(drag.record, dx, dy), label="comic_balloon_move"
            )
            if not drag.canvas.submit(command):
                logger.warning("Comic balloon move command was rejected")
        if is_click:
            self._click(drag.record.balloon_id, pt)
        else:
            self.tool._clear_last_click_state()

    def _click(self, balloon_id, pt):
        tool = self.tool
        click_time = tool._event_time_ms(pt)
        if tool._is_double_click(balloon_id, click_time, pt):
            tool._clear_last_click_state()
            if tool._activate_tool("comic_tool"):
                host = tool.host_api
                manager = getattr(host, "_tool_manager", None) or getattr(
                    host, "tool_manager", None
                )
                active = getattr(manager, "_active_tool", None)
                select = getattr(active, "select_balloon", None)
                if callable(select):
                    select(balloon_id)
            return
        tool._last_click_time_ms = click_time
        tool._last_click_pos = (float(pt.pos[0]), float(pt.pos[1]))
        tool._last_click_vec_id = balloon_id

    def cancel(self):
        if self.drag is not None:
            self._restore(self.drag)
            self.drag = None
