"""Delegate frame gestures to Comic's boundary editor, preserving layout authority."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.commands import _resolve_target_layer
from loomstroke.plugins.comic_tools import layout_tree as lt
from loomstroke.plugins.comic_tools.frame_boundary_continuation import panel_interaction_state
from loomstroke.plugins.comic_tools.model import (
    LAYER_ROLE_FRAMES,
    layer_comic_role,
    panel_record_from_object,
)
from loomstroke.plugins.comic_tools.panel_edit_gate import require_editable_parts
from loomstroke.plugins.comic_tools.panel_source_guard import source_guard
from loomstroke.plugins.comic_tools.service import ComicPageState, _iter_layers
from loomstroke.plugins.comic_tools.tool import ComicToolsPlugin


@dataclass
class _Gesture:
    canvas: Any
    page_id: str | None
    comic: ComicToolsPlugin
    state: ComicPageState
    panel_id: str | None
    origin: tuple[float, float]
    layers: tuple[tuple[Any, str | None, int], ...]
    layer_cache_version: int


class ComicPanelMove:
    def __init__(self, tool):
        self.tool = tool
        self.gesture: _Gesture | None = None
        self.fallback: ComicToolsPlugin | None = None

    def _page_id(self):
        pages = getattr(self.tool.host_api, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        return getter() if callable(getter) else None

    def _comic(self):
        host = self.tool.host_api
        manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
        state = getattr(manager, "_tools", {}).get("comic_tool")
        comic = getattr(state, "plugin", state)
        if isinstance(comic, ComicToolsPlugin):
            return comic
        if self.fallback is None:
            self.fallback = ComicToolsPlugin()
        self.fallback.host_api = host
        return self.fallback

    @staticmethod
    def _frame_layer(layer):
        marker = (getattr(layer, "metadata", None) or {}).get(PART_KEY, {})
        return layer_comic_role(layer) == LAYER_ROLE_FRAMES or (
            isinstance(marker, Mapping)
            and marker.get("role") in {"panel", "edge", "carrier", "restore"}
        )

    def begin(self, pt):  # noqa: PLR0911 - reject unrelated moves before decoding comic state
        tool = self.tool
        canvas = getattr(tool.host_api, "canvas", None)
        if canvas is None:
            return False
        active = _resolve_target_layer(canvas, None)
        hit = tool._find_vector_hit(pt)
        layer = _resolve_target_layer(canvas, hit.layer_id) if hit else None
        frame_selected = self._frame_layer(active) or (
            hit and (panel_record_from_object(hit.obj) or self._frame_layer(layer))
        )
        if not frame_selected and not any(self._frame_layer(node) for node in _iter_layers(canvas)):
            return False
        comic = self._comic()
        state = comic.read_state()
        if state is None or state.layout is None or state.panel_issues:
            return bool(frame_selected)  # Invalid frames must not fall through to path editing.
        workflow = comic.frame_workflow
        interaction = panel_interaction_state(workflow, state)
        point = float(pt.pos[0]), float(pt.pos[1])
        if not frame_selected and (
            interaction is None
            or lt.boundary_hit_test(interaction.layout, *comic._page_size(), point) is None
        ):
            return False
        tool._clear_layer_transform_overlay(clear_selection=True)
        tool._clear_vector_selection_on_miss()
        # Clearing a previous generic selection can invalidate its preview source.
        state = comic.read_state()
        interaction = panel_interaction_state(workflow, state)
        if comic._drag is not None or interaction is None:
            return True
        try:
            self._editable(state)
        except PanelFrameError:
            return True
        comic.selected_balloon_id = None
        comic._pointer_down_panels(state, point)
        panel_id = comic.controller.panel_at(interaction, point)
        if panel_id is None and hit:
            record = panel_record_from_object(hit.obj)
            panel_id = record.panel_id if record else None
        comic.selected_panel_id = panel_id
        layers = tuple(
            (layer, layer.parent_id, layer.texture_version)
            for layer_id in self._targets(state)
            if (layer := _resolve_target_layer(canvas, layer_id)) is not None
        )
        self.gesture = _Gesture(
            canvas,
            self._page_id(),
            comic,
            state,
            panel_id,
            point,
            layers,
            canvas.get_layer_tree_snapshot().layer_cache_version,
        )
        tool._mode = "comic_panel"
        tool._dragging = True
        tool._start_pos = point
        tool.host_api.input.pointer_capture(tool.descriptor().id)
        comic._install_overlay(True)
        comic._notify_panel()
        return True

    @staticmethod
    def _targets(state):
        targets = [state.frames_layer_id] if state.frames_layer_id else []
        for record in state.panel_records.values():
            frame = read_frame_layer(record.metadata)
            if frame:
                targets.extend([frame.panel_group_id, frame.content_group_id])
                targets.extend(edge.part_layer_id for edge in frame.edges)
        # Include ancestors so a mid-gesture lock also cancels the operation.
        for layer_id in list(targets):
            if layer_id not in state.panel_parts:
                raise PanelFrameError("frame edit has missing ancestry")
            parent = state.panel_parts[layer_id].parent_id
            while parent and parent not in targets:
                if parent not in state.panel_parts:
                    raise PanelFrameError("frame edit has missing ancestry")
                targets.append(parent)
                parent = state.panel_parts[parent].parent_id
        return targets

    @classmethod
    def _editable(cls, state):
        require_editable_parts(state, cls._targets(state))

    def _valid(self, gesture):
        if (
            getattr(self.tool.host_api, "canvas", None) is not gesture.canvas
            or self._page_id() != gesture.page_id
            or gesture.canvas.get_layer_tree_snapshot().layer_cache_version
            != gesture.layer_cache_version
        ):
            return False
        return all(
            layer.parent_id == parent
            and layer.texture_version == version
            and not layer.locked
            and not layer.mask_edit_mode
            and getattr(layer, "_preview_transform", None) is None
            for layer, parent, version in gesture.layers
        )

    def update(self, pt):
        gesture = self.gesture
        if gesture is None:
            return
        if not self._valid(gesture):
            self.cancel()
            self.tool._finish_pointer()
            self.tool._clear_last_click_state()
            return
        gesture.comic.on_pointer_move([pt])

    def finish(self, pt):
        gesture = self.gesture
        if gesture is None:
            return
        valid = self._valid(gesture) and source_guard(gesture.state).apply(gesture.canvas)
        changed = tuple(pt.pos) != gesture.origin
        if valid and changed:
            gesture.comic.on_pointer_up(pt)
        self.cancel()
        self.tool._finish_pointer()
        if valid and not changed and gesture.panel_id:
            self._click(gesture.panel_id, pt)
        else:
            self.tool._clear_last_click_state()

    def _click(self, panel_id, pt):
        tool = self.tool
        identity = f"comic-panel:{panel_id}"
        time = tool._event_time_ms(pt)
        if tool._is_double_click(identity, time, pt):
            tool._clear_last_click_state()
            if tool._activate_tool("comic_tool"):
                comic = self._comic()
                comic.selected_panel_id = panel_id
                comic.selected_balloon_id = None
                comic._notify_panel()
            return
        tool._last_click_time_ms = time
        tool._last_click_pos = tuple(pt.pos)
        tool._last_click_vec_id = identity

    def cancel(self):
        gesture, self.gesture = self.gesture, None
        if gesture is not None:
            gesture.comic._drag = None
            gesture.comic._install_overlay(False)
