"""Physical column regions and baseline grids for a single text frame."""

from collections.abc import Mapping
from math import ceil

from PySide6.QtCore import QRectF

from .metadata_values import finite_number
from .story_metadata import validated_story


def flow_values(raw):
    raw = raw if isinstance(raw, Mapping) else {}
    return {
        **raw,
        "columns": int(finite_number(raw.get("columns"), 1, 1, 32)),
        "gap_em": finite_number(raw.get("gap_em"), 1.0, 0.0, 100.0),
        "grid_em": finite_number(raw.get("grid_em"), 0.0, 0.0, 100.0),
        "balance": raw.get("balance") is True,
    }


def payload_flow(payload):
    layout = payload.get("layout", {}) if isinstance(payload, Mapping) else {}
    return flow_values(layout.get("text_flow")) if isinstance(layout, Mapping) else flow_values({})


def has_frame_flow(payload):
    values = payload_flow(payload)
    return (
        values["columns"] > 1
        or values["grid_em"] > 0
        or (isinstance(payload, Mapping) and bool(payload.get("linked_story")))
    )


class ColumnFlow:
    def __init__(self, payload, frame, em, vertical, direction, before, after):
        self.options = payload_flow(payload)
        self.vertical, self.direction = vertical, direction
        self.em, self.before, self.after = em, before, after
        count = self.options["columns"]
        extent = frame.height() if vertical else frame.width()
        gap = min(self.options["gap_em"] * em, max(0, (extent - count) / max(1, count - 1)))
        size = max(0.01, (extent - gap * (count - 1)) / count)
        self.regions = [
            (
                QRectF(frame.left(), frame.top() + i * (size + gap), frame.width(), size)
                if vertical
                else QRectF(frame.left() + i * (size + gap), frame.top(), size, frame.height())
            )
            for i in range(count)
        ]
        self.index = 0
        self.members = [None] * len(self.regions)
        story = validated_story(payload.get("linked_story"))
        if story is not None:
            regions, members = [], []
            for entry in story["frames"]:
                if (
                    not isinstance(entry, Mapping)
                    or not isinstance(entry.get("rect"), (list, tuple))
                    or len(entry["rect"]) != 4
                ):
                    continue
                region = QRectF(*entry["rect"])
                own = entry.get("id") == story.get("member")
                child = ColumnFlow(
                    {"layout": {"text_flow": self.options if own else entry.get("flow", {})}},
                    frame if own else region,
                    em,
                    vertical,
                    direction,
                    before,
                    after,
                )
                regions.extend(child.regions)
                members.extend([entry.get("id")] * len(child.regions))
            if regions:
                self.regions, self.members = regions, members

    @property
    def member(self):
        return self.members[self.index]

    @property
    def frame(self):
        return self.regions[self.index]

    def origin(self):
        if self.vertical:
            return (
                self.frame.right() - self.em - self.before
                if self.direction == "left"
                else self.frame.left() + self.after
            )
        return self.frame.top() + self.before

    def advance(self, cross):
        outside = (
            (
                cross < self.frame.left() + self.after - 1e-6
                or cross + self.em + self.before > self.frame.right() + 1e-6
            )
            if self.vertical
            else cross + self.em + self.after > self.frame.bottom() + 1e-6
        )
        if outside and self.index + 1 < len(self.regions):
            self.index += 1
            return self.origin()
        return cross

    def step(self, distance):
        grid = self.options["grid_em"] * self.em
        return ceil((distance - 1e-8) / grid) * grid if grid else distance
