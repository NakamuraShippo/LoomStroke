"""Image insertion into existing clip groups, preserving loaded original pixels."""

from __future__ import annotations

import uuid

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.image_placement import PLACEMENT_KEY, fit_image
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand

from .panel_source_guard import source_guard


def place_images_command(state, sources, *, panel_ids, policy="cover", guarded=True):
    """Sources are (name, detached QImage). Explicit target order supports batch distribution."""
    if not sources or len(sources) != len(panel_ids):
        raise PanelFrameError("each image requires an explicit target panel")
    commands = [source_guard(state)] if guarded else []
    for (name, image), panel_id in zip(sources, panel_ids, strict=True):
        if panel_id not in state.panel_records or image.isNull():
            raise PanelFrameError("image or target panel is missing")
        frame = read_frame_layer(state.panel_records[panel_id].metadata)
        if frame is None:
            raise PanelFrameError("image insertion requires a materialized target")
        x, y, w, h = state.panel_rects[panel_id]
        inset = frame.clip_inset_px
        placement = fit_image(
            (image.width(), image.height()),
            (x + inset, y + inset, w - 2 * inset, h - 2 * inset),
            policy=policy,
        )
        layer_id = f"panel-image-{uuid.uuid4().hex}"
        commands.append(
            PanelLayerInsertCommand(
                layer_id,
                parent_id=frame.content_group_id,
                attributes={
                    "name": name,
                    "layer_type": "raster",
                    "image": image,
                    "transform": placement.transform,
                    "metadata": {
                        PLACEMENT_KEY: {
                            "version": 1,
                            "fit_policy": policy,
                            "source_size": list(placement.source_size),
                        }
                    },
                },
            )
        )
    return PanelEditCommand(commands, label="Place panel images")
