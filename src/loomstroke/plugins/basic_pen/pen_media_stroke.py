"""Send the basic pen's spaced stroke points to the watercolor or oil engine."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from typing import Any

from ...core.dab_variation import DabVariation
from ...core.paint_tool_session import PaintToolCommand
from ...core.pen_taper_model import taper_scale
from ...core.pressure_stroke_point import PressureStrokePoint
from ...core.recontour.media_record import MediaStrokeRecord
from ..sdk import PropertySpec, PropertyType

logger = logging.getLogger(__name__)

ENGINES = {"Watercolor": "water", "Oil": "oil"}
MEDIA = ("Normal", *ENGINES)
PAPERS = ("Standard", "Smooth", "Rough")
MAX_DIAMETER = 128
NUMBERS = (
    "water_amount",
    "paint_load",
    "paint_reach",
    "paint_remaining",
    "paint_lifting",
    "paint_pickup",
    "paint_recovery",
)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("PaintTool", text, use_fallback=True)


class PenMediaStroke:
    """The pen's medium settings and the provisional stroke painted with them.

    The pen keeps its own input handling (stabilization, spacing, pressure curve, guides,
    symmetry). Only the pixels come from the paint engine, which shows the stroke as a
    preview and commits it as one history entry at pen-up.
    """

    def __init__(self, tool: Any) -> None:
        self.tool = tool
        self.medium = "Normal"
        self.paper = "Standard"
        self.water_amount = 50
        self.paint_load = 50
        self.paint_reach = 50
        self.paint_remaining = 20
        self.paint_lifting = 50
        self.paint_pickup = 50
        self.paint_recovery = 50
        self._token: object | None = None
        # x, y, pressure, tilt_x, tilt_y, distance from the stroke start, time, density
        self._points: list[tuple[float, ...]] = []
        self._length = 0.0
        self._tail: tuple[float, float] | None = None
        self._sent: dict[int, tuple[int, int]] = {}
        self._begin: PaintToolCommand | None = None
        self._painted: list[tuple[float, ...]] = []
        self._paths: list[tuple[float, float]] = []
        self._outline: tuple | None = None
        self._rgb: tuple[int, int, int] = (0, 0, 0)

    @property
    def enabled(self) -> bool:
        return self.medium in ENGINES

    @property
    def active(self) -> bool:
        return self._token is not None

    def values(self) -> dict[str, Any]:
        return {
            "medium": self.medium,
            "paper": self.paper,
            "water_amount": self.water_amount,
            "paint_load": self.paint_load,
            "paint_reach": self.paint_reach,
            "paint_remaining": self.paint_remaining,
            "paint_lifting": self.paint_lifting,
            "paint_pickup": self.paint_pickup,
            "paint_recovery": self.paint_recovery,
        }

    def update(self, payload: Mapping[str, Any]) -> None:
        if payload.get("medium") in MEDIA:
            self.medium = payload["medium"]
        if payload.get("paper") in PAPERS:
            self.paper = payload["paper"]
        for key in NUMBERS:
            if key in payload:
                try:
                    setattr(self, key, min(100, max(0, int(payload[key]))))
                except (TypeError, ValueError, OverflowError):
                    continue

    def properties(self) -> list[PropertySpec]:
        return [
            PropertySpec(
                "medium",
                _ts("Medium"),
                PropertyType.ENUM,
                default_value=self.medium,
                enum_items=list(MEDIA),
            ),
            PropertySpec(
                "paper",
                _ts("Paper type"),
                PropertyType.ENUM,
                default_value=self.paper,
                enum_items=list(PAPERS),
            ),
            PropertySpec(
                "water_amount",
                _ts("Water amount"),
                PropertyType.INT,
                min_value=0,
                max_value=100,
                default_value=self.water_amount,
                step=1,
            ),
            PropertySpec(
                "paint_load",
                _ts("Paint load"),
                PropertyType.INT,
                min_value=0,
                max_value=100,
                default_value=self.paint_load,
                step=1,
            ),
            PropertySpec(
                "paint_reach",
                _ts("Paint reach"),
                PropertyType.INT,
                min_value=0,
                max_value=100,
                default_value=self.paint_reach,
                step=1,
            ),
            PropertySpec(
                "paint_remaining",
                _ts("Remaining paint"),
                PropertyType.INT,
                min_value=0,
                max_value=100,
                default_value=self.paint_remaining,
                step=1,
            ),
            PropertySpec(
                "paint_lifting",
                _ts("Paint lifting"),
                PropertyType.INT,
                min_value=0,
                max_value=100,
                default_value=self.paint_lifting,
                step=1,
            ),
            PropertySpec(
                "paint_pickup",
                _ts("Color pickup"),
                PropertyType.INT,
                min_value=0,
                max_value=100,
                default_value=self.paint_pickup,
                step=1,
            ),
            PropertySpec(
                "paint_recovery",
                _ts("Color recovery"),
                PropertyType.INT,
                min_value=0,
                max_value=100,
                default_value=self.paint_recovery,
                step=1,
            ),
        ]

    def _wear(self) -> tuple[float, float, float]:
        if self.medium == "Watercolor":
            rest = self.paint_remaining, self.paint_lifting
        else:
            rest = self.paint_pickup, self.paint_recovery
        return self.paint_reach / 100.0, rest[0] / 100.0, rest[1] / 100.0

    def begin(self) -> bool:
        tool = self.tool
        host = tool.host_api
        opacity = min(1.0, float(tool.settings.opacity))
        if host is None or opacity <= 0.0 or self.paint_load == 0:
            return False
        red, green, blue = self._rgb = tool._active_color_rgb()
        token = object()
        begin = self._begin = PaintToolCommand(
            "begin",
            token,
            medium=ENGINES[self.medium],
            # The raster pen stamps a tip image at twice the size setting.
            diameter=max(
                1, min(MAX_DIAMETER, round(tool.settings.size * (2 if tool._tip_path else 1)))
            ),
            color_bgr=(blue, green, red),
            opacity=opacity,
            paper=self.paper,
            water_amount=self.water_amount / 100.0,
            paint_load=self.paint_load / 100.0,
            wear=self._wear(),
            tip=(tool._tip_path or "", float(tool.settings.hardness), tool._hardness_mode),
            surface=(
                tool._dynamics.texture_strength,
                tool._dynamics.texture_scale,
                tool._dynamics.variation_seed,
                tool._dynamics.color_mixing,
            ),
            # The basic pen keeps what was drawn when another edit interrupts it.
            commit_on_interrupt=True,
        )
        accepted = self._send(begin)
        if not accepted:
            if accepted is None:
                # A failed call may already have created the session before raising.
                self._send(PaintToolCommand("cancel", token))
                self._notify("The paint stroke could not be completed.")
            else:
                self._notify("Use an editable RGB raster layer without a selection to paint.")
            return False
        self._token = token
        self._points, self._length, self._tail, self._sent = [], 0.0, None, {}
        self._painted, self._paths, self._outline = [], [], None
        return True

    def feed(self, points: Iterable[Any]) -> bool:
        """Paint the pen's new stroke points; False means the stroke had to be dropped."""
        if self._token is None:
            return False
        first = len(self._points)
        self._record(points)
        paths: list = []
        dabs = self._dabs(self._points[first:], None, self._sent, paths)
        if not dabs or self._send(PaintToolCommand("move", self._token, dabs)):
            self._painted.extend(dabs)
            self._paths.extend(paths)
            return True
        self.cancel()
        self._notify("The paint stroke could not be completed.")
        return False

    def finish(self) -> bool:
        """Commit the stroke as one history entry; True when a stroke was committed."""
        token = self._token
        if token is None:
            return False
        settings = self.tool.settings
        start, end = settings.taper_start_length, settings.taper_end_length
        painted, paths = tuple(self._painted), list(self._paths)
        if end > 0.0 or (start > 0.0 and self._length <= 1e-6):
            # The end taper depends on the full length, so the stroke is painted again.
            paths = []
            painted = self._dabs(self._points, self._length, {}, paths)
            command = PaintToolCommand("end", token, painted, replay=True)
        else:
            command = PaintToolCommand("end", token)
        committed = bool(self._send(command))
        if not committed:
            self._send(PaintToolCommand("cancel", token))
            self._notify("The paint stroke could not be completed.")
        step = max(1.0, float(settings.size) * float(settings.spacing))
        self._outline = (
            (token, self._begin, painted, paths, step) if committed and painted else None
        )
        self._clear()
        return committed

    def outline_record(self, smoothness: float) -> MediaStrokeRecord | None:
        """Editable path of the stroke that was just committed, for the outline handles."""
        outline, self._outline = self._outline, None
        return None if outline is None else MediaStrokeRecord(*outline, smoothness)

    def cancel(self) -> None:
        if self._token is not None:
            self._send(PaintToolCommand("cancel", self._token))
            self._clear()

    def _clear(self) -> None:
        self._token = None
        self._points, self._length, self._tail, self._sent = [], 0.0, None, {}

    def _record(self, points: Iterable[Any]) -> None:
        tool = self.tool
        for point in points:
            x, y = float(point.pos[0]), float(point.pos[1])
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            if self._tail is not None:
                self._length += math.hypot(x - self._tail[0], y - self._tail[1])
            self._tail = x, y
            settings = tool.settings
            pressure, opacity, tone = tool._pressure_response.response(
                point.pressure, enabled=settings.pressure_sensitive
            )
            # Pressure sets the contact size through its curve. How much paint a dab leaves
            # follows flow, the opacity curve and tone shading, as in the pen's own strokes.
            shade = (1.0 - settings.tone_range) + settings.tone_range * tone**settings.tone_gamma
            density = round(min(1.0, settings.flow * opacity * shade), 6)
            tilt_x, tilt_y = tool._command_tilt(point)
            time = point.time if point.time else 0.0
            self._points.append((x, y, pressure, tilt_x, tilt_y, self._length, time, density))

    def _varied(self, record: tuple[float, ...]) -> tuple[float, float, float, float, tuple]:
        x, y, pressure, tilt_x, tilt_y, _distance, time, _density = record
        dynamics = self.tool._dynamics
        # Texture and mixing belong to the whole stroke; only these vary from dab to dab.
        if not (
            dynamics.scatter
            or dynamics.size_variation
            or dynamics.angle_variation
            or dynamics.color_variation
        ):
            return x, y, pressure, 0.0, ()
        point = DabVariation.apply(
            [
                PressureStrokePoint(
                    x=x,
                    y=y,
                    pressure=pressure,
                    tilt_x=tilt_x,
                    tilt_y=tilt_y,
                    rotation=0.0,
                    timestamp=time,
                )
            ],
            dynamics.values(),
            size=float(self.tool.settings.size),
            color=self._rgb,
            stroke_id=self.tool._stroke_id,
        )[0]
        red, green, blue = point.dab_color
        color = (float(blue), float(green), float(red)) if dynamics.color_variation else ()
        return (
            point.x,
            point.y,
            min(1.0, pressure * point.size_scale),
            point.tip_angle,
            color,
        )

    def _dabs(
        self,
        records: list[tuple[float, ...]],
        total: float | None,
        sent: dict,
        paths: list | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        """Dabs for the engine; paths receives each dab's pen position before scatter."""
        settings = self.tool.settings
        start, end = settings.taper_start_length, settings.taper_end_length
        session = self.tool._assist.session
        copies = session is not None and session.has_copies
        dabs = []
        for record in records:
            x, y, pressure, angle, color = self._varied(record)
            pressure *= taper_scale(record[5], total, start, end)
            if pressure <= 0.0 or record[7] <= 0.0:
                continue
            pen = record[0], record[1]
            targets = [(0, x, y, pen)] if not copies or session.include_original else []
            if copies:
                targets.extend(
                    (lane, cx, cy, path)
                    for lane, ((cx, cy), path) in enumerate(
                        zip(session.copies(x, y), session.copies(*pen), strict=True), 1
                    )
                )
            for lane, px, py, path in targets:
                position = round(px), round(py)
                if sent.get(lane) != position:
                    sent[lane] = position
                    dabs.append(
                        (
                            *position,
                            round(pressure, 6),
                            record[3],
                            record[4],
                            lane,
                            record[7],
                            angle,
                            *color,
                        )
                    )
                    if paths is not None:
                        paths.append(path)
        return tuple(dabs)

    def _send(self, command: PaintToolCommand) -> bool | None:
        host = self.tool.host_api
        if host is None:
            return False
        try:
            return bool(host.canvas.submit(command))
        except Exception:
            logger.exception("Pen medium stroke command %s failed", command.phase)
            return None

    def _notify(self, message: str) -> None:
        show = getattr(getattr(self.tool.host_api, "ui", None), "show_message", None)
        if callable(show):
            show(_ts(message))
