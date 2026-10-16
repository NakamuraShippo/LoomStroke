"""Decode local sources off-thread; apply original pixels through one panel command."""

import asyncio
import logging
from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QImageReader

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError

from .model import PANEL_META_KEY, PanelRecord
from .panel_content_plan import place_images_command
from .panel_source_guard import source_guard

logger = logging.getLogger(__name__)


def decode_images(paths):
    result = []
    for path in paths:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            raise PanelFrameError(f"cannot read image {Path(path).name}: {reader.errorString()}")
        result.append((Path(path).stem, image))
    return result


def placement_command(tool, state, sources, panel_ids, policy):
    commands = [source_guard(state)]
    records = dict(state.panel_records)
    for panel_id in dict.fromkeys(panel_ids):
        if read_frame_layer(records[panel_id].metadata):
            continue
        conversion = tool.controller.panel_frame_conversion_command(
            state, panel_id, page_size=tool._page_size(), max_group_depth=8
        )
        commands.append(conversion)
        records[panel_id] = PanelRecord.from_meta(conversion._carrier.meta[PANEL_META_KEY])
    planned = replace(state, panel_records=records)
    commands.append(
        place_images_command(planned, sources, panel_ids=panel_ids, policy=policy, guarded=False)
    )
    return PanelEditCommand(commands, label="Drop images into panels")


def handle_image_drop(tool, payload):
    if payload.get("mime") != "application/x-loomstroke-image-files":
        return False
    state = tool.read_state()
    pos = payload.get("pos_canvas")
    if state is None or state.panel_issues or not isinstance(pos, (tuple, list)) or len(pos) != 2:
        return False
    panel_id = tool.controller.panel_at(state, pos)
    if not panel_id:
        return False
    controls = getattr(tool._panel_widget, "frame_controls", None)
    if controls is None:
        return False
    try:
        loop = asyncio.get_running_loop()
        page_id = tool.frame_workflow.page_id()
    except (RuntimeError, PanelFrameError):
        return False
    paths = payload["image_paths"]
    targets = [panel_id] * len(paths)
    if controls.distribution.currentData() == "reading_order":
        start = state.reading_order.index(panel_id)
        targets = list(state.reading_order[start : start + len(paths)])
    policy = controls.fit.currentData()
    payload["handled"] = True

    async def load_and_place():
        try:
            if len(targets) != len(paths):
                raise PanelFrameError("not enough panels in reading order for these images")
            sources = await asyncio.to_thread(decode_images, paths)
            if tool.frame_workflow.page_id() != page_id:
                raise PanelFrameError("active page changed while loading images")
            command = placement_command(tool, state, sources, targets, policy)
            if not tool._submit(command):
                raise PanelFrameError(command.last_error or "image placement was rejected")
        except PanelFrameError as exc:
            controls.run(lambda error=exc: raise_error(error))
        except Exception:
            logger.exception("panel image drop failed")
            controls.run(lambda: raise_error(PanelFrameError("image loading failed")))

    task = loop.create_task(load_and_place())
    tool.frame_workflow.image_tasks.add(task)
    task.add_done_callback(tool.frame_workflow.image_tasks.discard)
    return True


def raise_error(error):
    raise error
