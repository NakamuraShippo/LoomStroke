from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...core.commands import (
    CutSelectionCommand,
    SelectionOp,
    SelectionSnapshot,
    UpdateSelectionMaskCommand,
)
from ...core.geometry import Affine2D
from ...core.selection_clipboard import SelectionPayload
from ...core.selection_mesh import Mesh
from ...core.selection_projective import Quad
from ...host_api import StrokePoint
from ..common.document_capture_guard import (
    DocumentCaptureLease,
    begin_document_capture_edit,
    release_document_capture_edit,
)
from .rect_assist_controller import RectSelectionAssist

if TYPE_CHECKING:
    from .rect_tool import BasicSelectionRectTool


@dataclass(slots=True)
class RasterTransformDragState:
    mode: str
    handle: str
    pointer0: tuple[float, float]
    matrix0: Affine2D
    anchor_local: tuple[float, float]
    anchor_canvas: tuple[float, float]
    v0: tuple[float, float] | None
    axis_x: tuple[float, float] | None
    axis_y: tuple[float, float] | None
    den_x: float
    den_y: float
    handle_local: tuple[float, float] | None
    shift_locked: bool
    snap_override: bool
    duplicate: bool
    quad0: Quad | None = None
    mesh0: Mesh | None = None


@dataclass(slots=True)
class RasterSelectionState:
    base_mask: Any | None = None
    base_bounds: Any | None = None
    base_full_bounds: Any | None = None
    base_full_mask: Any | None = None
    base_captured: bool = False
    preview_queued: bool = False
    preview_enabled: bool = True
    drag_mode: str = "create"
    drag_offset: tuple[float, float] = (0.0, 0.0)


@dataclass(slots=True)
class RasterTransformSession:
    active: bool = False
    payload: SelectionPayload | None = None
    matrix: Affine2D | None = None
    quad: Quad | None = None
    mesh: Mesh | None = None
    projective_result: tuple[Quad, tuple[Any, Any, tuple[int, int, int, int]]] | None = None
    pivot_local: tuple[float, float] = (0.0, 0.0)
    drag: RasterTransformDragState | None = None
    mode_override: str | None = None
    duplicate_latched: bool = False
    mask_mode: str = "soft"
    cut_command: CutSelectionCommand | None = None
    base_mask: Any | None = None
    base_bounds: Any | None = None
    base_full_bounds: Any | None = None
    capture_lease: DocumentCaptureLease | None = None


class RasterSelectionController:
    """Owns raster selection and raster transform state for the rect tool."""

    def __init__(self, tool: BasicSelectionRectTool) -> None:
        self._tool = tool
        self.state = RasterSelectionState()
        self.transform = RasterTransformSession()
        self.assist = RectSelectionAssist(tool)

    def reset_pointer_state(self) -> None:
        self.assist.reset()
        self._discard_queued_preview()
        self.state.base_mask = None
        self.state.base_bounds = None
        self.state.base_full_bounds = None
        self.state.base_full_mask = None
        self.state.base_captured = False
        self.state.preview_queued = False
        self.state.drag_mode = "create"
        self.state.drag_offset = (0.0, 0.0)

    def begin_content_preview(self, canvas: Any) -> None:
        self.transform.capture_lease = begin_document_capture_edit(
            canvas, self.transform.capture_lease
        )

    def finish_content_preview(self) -> bool:
        if not release_document_capture_edit(self.transform.capture_lease):
            return False
        self.transform.capture_lease = None
        return True

    def on_pointer_down(  # noqa: PLR0912
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        if self.assist.preparation.busy:
            self.assist.reset()
            return
        del meta
        tool = self._tool
        if not tool.host_api:
            return

        if tool._transform_active:
            if tool._begin_transform_drag(pt, alt=alt, shift=shift, ctrl=ctrl):
                return
            if tool._transform_mesh is not None:
                tool._mesh_preview.commit_when_ready(tool._transform_mesh)
                return
            cmd = tool._build_transform_commit_command()
            if cmd and tool.host_api:
                canvas = getattr(tool.host_api, "canvas", None)
                if canvas is not None:
                    try:
                        success = bool(canvas.submit(cmd))
                    except Exception:
                        success = False
                    if not success:
                        tool._pending_commands.append(cmd)
                else:
                    tool._pending_commands.append(cmd)

        initial_mods = {"shift": bool(shift), "alt": bool(alt)}
        op = tool._op_from_modifiers(initial_mods)
        is_replace_mode = op == SelectionOp.REPLACE
        selection_exists = tool._selection_exists()
        clicked_inside = tool._is_outside_selection(pt) is False

        if (
            is_replace_mode
            and selection_exists
            and (clicked_inside or tool._hit_test_transform_handle(pt) is not None)
            and tool._ensure_transform_session()
        ):
            if tool._begin_transform_drag(pt, alt=alt, shift=shift, ctrl=ctrl):
                return
            tool._cancel_transform_session()

        tool._start = pt
        tool._current = pt
        tool._press_pos = (pt.pos[0], pt.pos[1])
        tool._initial_modifiers = dict(initial_mods)
        tool._current_modifiers = dict(initial_mods)

        tool._capture_base_selection()

        if tool.host_api:
            tool.host_api.input.pointer_capture(tool.descriptor().id)

        op = tool._op_from_modifiers(tool._initial_modifiers)
        is_replace_mode = op == SelectionOp.REPLACE
        clicked_inside = tool._is_outside_selection(pt) is False

        if is_replace_mode and tool._selection_exists() and clicked_inside:
            tool._drag_mode = "move_selection"
            if tool._base_bounds:
                bx, by, _, _ = tool._base_bounds
                tool._drag_offset = (pt.pos[0] - bx, pt.pos[1] - by)
        else:
            tool._drag_mode = "create"
            self.assist.begin(pt)

    def on_pointer_move(self, points: list[StrokePoint]) -> None:
        tool = self._tool
        if tool._transform_drag and points:
            tool._update_transform_drag(points[-1])
            return
        if not tool._start or not points:
            return
        tool._current = points[-1]

        if not tool._preview_enabled:
            return

        if tool._drag_mode == "move_selection":
            self._queue_move_preview()
            return

        operation = tool._op_from_modifiers(tool._initial_modifiers)
        rect, full_rect = tool._compute_rect_geometry_with_full(
            tool._start,
            tool._current,
            square=tool._current_modifiers["shift"],
            center=tool._current_modifiers["alt"],
        )

        if rect is None:
            self._discard_queued_preview()
            tool._restore_base_selection(recompose=True)
            return
        full_bounds = full_rect if operation == SelectionOp.REPLACE else None

        if self.assist.active:
            self.assist.preview(full_rect or rect)
            return

        self._queue_preview(
            UpdateSelectionMaskCommand(
                rect,
                operation,
                merge_key="selection.preview",
                source="preview",
                full_bounds=full_bounds,
                preview_base=(
                    SelectionSnapshot(self.state.base_mask, self.state.base_bounds)
                    if operation != SelectionOp.REPLACE
                    else None
                ),
            )
        )

    def on_pointer_up(self, pt: StrokePoint) -> None:
        tool = self._tool
        if tool._transform_drag:
            tool._finish_transform_drag()
            return
        if not tool._start or not tool.host_api:
            return
        tool._current = pt

        if self.state.preview_queued:
            self._discard_queued_preview()
            tool._restore_base_selection(recompose=True)

        if tool._drag_mode == "move_selection":
            self._queue_move_commit(pt)
        elif self.assist.active and tool._movement_exceeded_threshold(pt):
            rect, full_rect = tool._compute_rect_geometry_with_full(
                tool._start,
                tool._current,
                square=tool._current_modifiers["shift"],
                center=tool._current_modifiers["alt"],
            )
            self.assist.commit(full_rect or rect, tool._op_from_modifiers(tool._initial_modifiers))
            return
        else:
            self._queue_create_commit(pt)

        tool._finish_pointer()

    def _queue_move_preview(self) -> None:
        tool = self._tool
        if not tool._current or not tool._base_bounds:
            return
        rect, full_rect = self._moved_selection_rect(tool._current)
        if rect is None:
            return
        self._queue_preview(
            UpdateSelectionMaskCommand(
                rect,
                SelectionOp.REPLACE,
                merge_key="selection.preview",
                source="preview",
                full_bounds=full_rect,
            )
        )

    def _discard_queued_preview(self) -> None:
        pending = self._tool._pending_commands
        pending[:] = [
            command
            for command in pending
            if not (isinstance(command, UpdateSelectionMaskCommand) and command.source == "preview")
        ]

    def _queue_preview(self, command: UpdateSelectionMaskCommand) -> None:
        self._discard_queued_preview()
        self.state.preview_queued = True
        self._tool._pending_commands.append(command)

    def _queue_move_commit(self, pt: StrokePoint) -> None:
        tool = self._tool
        if not tool._base_bounds:
            return
        rect, full_rect = self._moved_selection_rect(pt)
        if rect is not None:
            tool._pending_commands.append(
                UpdateSelectionMaskCommand(rect, SelectionOp.REPLACE, full_bounds=full_rect)
            )

    def _queue_create_commit(self, pt: StrokePoint) -> None:
        tool = self._tool
        operation = tool._op_from_modifiers(tool._initial_modifiers)
        rect, full_rect = tool._compute_rect_geometry_with_full(
            tool._start,
            tool._current,
            square=tool._current_modifiers["shift"],
            center=tool._current_modifiers["alt"],
        )

        is_click = not tool._movement_exceeded_threshold(pt)
        if is_click and operation == SelectionOp.REPLACE:
            tool._queue_clear()
        elif rect is not None:
            full_bounds = full_rect if operation == SelectionOp.REPLACE else None
            tool._pending_commands.append(
                UpdateSelectionMaskCommand(
                    rect,
                    operation,
                    full_bounds=full_bounds,
                    feather_radius=(
                        tool.settings.feather_radius if tool.settings.feather_enabled else 0
                    ),
                )
            )
        else:
            tool._restore_base_selection(recompose=True)

    def _moved_selection_rect(
        self,
        pt: StrokePoint,
    ) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
        tool = self._tool
        if not tool._base_bounds:
            return None, None

        new_x = float(pt.pos[0] - tool._drag_offset[0])
        new_y = float(pt.pos[1] - tool._drag_offset[1])

        if tool.settings.pixel_perfect:
            new_x = round(new_x)
            new_y = round(new_y)
        else:
            new_x = math.floor(new_x)
            new_y = math.floor(new_y)

        if tool._is_snap_enabled():
            new_x, new_y = tool._snap_position(float(new_x), float(new_y))
            new_x, new_y = int(new_x), int(new_y)

        _, _, w, h = tool._base_bounds
        rect = (int(new_x), int(new_y), int(w), int(h))
        full_rect = None
        if tool._base_full_bounds is not None:
            base_full = tool._normalize_bounds(tool._base_full_bounds)
            base_clipped = tool._normalize_bounds(tool._base_bounds)
            if base_full is not None and base_clipped is not None:
                dx = float(new_x) - float(base_clipped[0])
                dy = float(new_y) - float(base_clipped[1])
                full_rect = (
                    int(base_full[0] + dx),
                    int(base_full[1] + dy),
                    int(base_full[2]),
                    int(base_full[3]),
                )
        return rect, full_rect
