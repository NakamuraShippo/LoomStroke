"""Comic page controller: layout/balloon state ↔ undoable canvas commands.

Reads the current comic state of the active page's canvas (managed layers,
layout tree, panel/balloon objects) and builds :class:`ComicEditCommand`
instances for every edit. Pure with respect to the canvas: nothing here
mutates the document — callers submit the returned command through the
regular history (``canvas.submit``).
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.command_types import Command
from loomstroke.plugins.text_tool.common import (
    extract_text_content,
    extract_text_payload,
    extract_word_wrap,
    normalize_color_rgba,
    normalize_vertical_flow,
)

from . import layout_tree as lt
from .commands import ComicEditCommand, EnsureLayerSpec
from .model import (
    LAYER_ROLE_BALLOON_TEXT,
    LAYER_ROLE_BALLOONS,
    LAYER_ROLE_FRAMES,
    BalloonRecord,
    BalloonTail,
    PanelRecord,
    balloon_record_from_object,
    layer_comic_role,
    layer_role_marker,
    merged_layer_metadata_with_layout,
    read_layer_layout_payload,
)
from .panel_content_snapshot import PanelContentUnit, content_units
from .panel_frame_state import read_panel_sources
from .panel_part_snapshot import PanelPartSnapshot, part_snapshots
from .text_frames import autosize_body_rect, build_balloon_text_object

if TYPE_CHECKING:
    from loomstroke.core.comic_panels.materialize_command import PanelFrameMaterializeCommand

FRAMES_LAYER_NAME = "コマ枠"
BALLOONS_LAYER_NAME = "フキダシ"
TEXT_LAYER_NAME = "フキダシテキスト"


@dataclass(frozen=True)
class TextProps:
    text: str = ""
    word_wrap: bool = True
    font_size_pt: float = 20.0
    vertical: bool = True
    vertical_flow: str = "right"
    align: str = "center"
    line_height: float = 1.35
    tracking: float = 0.0
    font_family: str = ""
    font_style: str = ""
    color_rgba: tuple[int, int, int, int] = (17, 17, 17, 255)
    padding_top: float = 0.0
    padding_right: float = 0.0
    padding_bottom: float = 0.0
    padding_left: float = 0.0
    rotate_half_width: bool = False


@dataclass
class ComicPageState:
    frames_layer_id: str | None = None
    frames_layer_metadata: dict[str, Any] | None = None
    balloons_layer_id: str | None = None
    text_layer_id: str | None = None
    layout: lt.ComicPageLayout | None = None
    panel_rects: dict[str, lt.Rect] = field(default_factory=dict)
    panel_polygons: dict[str, Any] = field(default_factory=dict)
    panel_records: dict[str, PanelRecord] = field(default_factory=dict)
    panel_sources: dict[str, tuple[str, Any]] = field(default_factory=dict)
    panel_issues: tuple[str, ...] = ()
    panel_parts: dict[str, PanelPartSnapshot] = field(default_factory=dict)
    panel_contents: dict[str, tuple[PanelContentUnit, ...]] = field(default_factory=dict)
    balloons: dict[str, BalloonRecord] = field(default_factory=dict)
    text_objects: dict[str, Any] = field(default_factory=dict)  # balloon_id -> obj
    balloon_sources: dict[str, str] = field(default_factory=dict)
    text_sources: dict[str, str] = field(default_factory=dict)
    reading_order: tuple[str, ...] = ()


def _iter_layers(canvas: Any):
    stack = list(getattr(canvas, "_layers", None) or [])
    while stack:
        layer = stack.pop()
        yield layer
        stack.extend(getattr(layer, "children", None) or [])


def _find_role_layer(canvas: Any, role: str) -> Any | None:
    from loomstroke.core.comic_panels.membership import UNIT_KEY  # noqa: PLC0415

    layers = list(_iter_layers(canvas))
    lookup = {layer.id: layer for layer in layers}
    for layer in layers:
        if layer_comic_role(layer) != role:
            continue
        ancestor = lookup.get(getattr(layer, "parent_id", None))
        seen = set()
        while ancestor is not None and ancestor.id not in seen:
            seen.add(ancestor.id)
            if UNIT_KEY in (getattr(ancestor, "metadata", None) or {}):
                break
            ancestor = lookup.get(getattr(ancestor, "parent_id", None))
        else:
            return layer
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_align(value: Any, *, default: str = "center") -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in {"left", "center", "right", "justify"} else default


def text_props_from_object(obj: Any) -> TextProps:
    payload = extract_text_payload(obj) or {}
    document = payload.get("document") if isinstance(payload, Mapping) else None
    paragraph: Mapping[str, Any] = {}
    if isinstance(document, Mapping):
        paragraphs = document.get("paragraphs")
        if isinstance(paragraphs, (list, tuple)) and paragraphs:
            first = paragraphs[0]
            if isinstance(first, Mapping):
                paragraph = first
    style_map = payload.get("style_map") if isinstance(payload, Mapping) else None
    style: Mapping[str, Any] = {}
    if isinstance(style_map, Mapping):
        default = style_map.get("default")
        if isinstance(default, Mapping):
            style = default
    layout = payload.get("layout") if isinstance(payload, Mapping) else None
    if not isinstance(layout, Mapping):
        layout = {}

    font_size_pt = _float_or_default(
        _first_present(payload.get("font_size_pt"), style.get("font_size_pt")),
        20.0,
    )
    line_height = _float_or_default(
        _first_present(payload.get("line_height"), paragraph.get("line_height")),
        1.35,
    )
    tracking = _float_or_default(
        _first_present(payload.get("tracking"), style.get("tracking")),
        0.0,
    )

    return TextProps(
        text=extract_text_content(payload),
        word_wrap=extract_word_wrap(payload),
        font_size_pt=font_size_pt,
        vertical=bool(_first_present(payload.get("vertical"), style.get("vertical"), True)),
        vertical_flow=normalize_vertical_flow(
            _first_present(payload.get("vertical_flow"), paragraph.get("vertical_flow")),
            default="right",
        ),
        align=_normalize_align(
            _first_present(payload.get("align"), paragraph.get("align")),
        ),
        line_height=line_height,
        tracking=tracking,
        font_family=str(_first_present(payload.get("font_family"), style.get("font_family")) or ""),
        font_style=str(_first_present(payload.get("font_style"), style.get("font_style")) or ""),
        color_rgba=normalize_color_rgba(
            _first_present(payload.get("color_rgba"), style.get("color_rgba")),
            (17, 17, 17, 255),
        ),
        padding_top=_float_or_default(
            _first_present(payload.get("padding_top"), layout.get("padding_top")),
            0.0,
        ),
        padding_right=_float_or_default(
            _first_present(payload.get("padding_right"), layout.get("padding_right")),
            0.0,
        ),
        padding_bottom=_float_or_default(
            _first_present(payload.get("padding_bottom"), layout.get("padding_bottom")),
            0.0,
        ),
        padding_left=_float_or_default(
            _first_present(payload.get("padding_left"), layout.get("padding_left")),
            0.0,
        ),
        rotate_half_width=bool(
            _first_present(payload.get("rotate_half_width"), style.get("rotate_half_width"), False)
        ),
    )


class ComicPageController:
    """Stateless command builder over one canvas (the active page)."""

    def __init__(self, *, page_key: str = "") -> None:
        # Stable per-document-page layer ids; a random suffix avoids id
        # collisions across pages without depending on page-id formats.
        self._page_key = page_key or uuid.uuid4().hex[:8]

    # ── state ──────────────────────────────────────────────────────────

    def read_state(self, canvas: Any, *, page_size: tuple[float, float]) -> ComicPageState:
        state = ComicPageState()
        frames = _find_role_layer(canvas, LAYER_ROLE_FRAMES)
        balloons = _find_role_layer(canvas, LAYER_ROLE_BALLOONS)
        texts = _find_role_layer(canvas, LAYER_ROLE_BALLOON_TEXT)
        state.frames_layer_id = getattr(frames, "id", None)
        if frames is not None:
            raw_metadata = getattr(frames, "metadata", None)
            if isinstance(raw_metadata, Mapping):
                state.frames_layer_metadata = copy.deepcopy(dict(raw_metadata))
        state.balloons_layer_id = getattr(balloons, "id", None)
        state.text_layer_id = getattr(texts, "id", None)
        if frames is not None:
            state.layout = lt.layout_from_payload(read_layer_layout_payload(frames))
        state.panel_records, state.panel_sources, state.panel_issues = read_panel_sources(
            _iter_layers(canvas)
        )
        state.panel_parts = part_snapshots(_iter_layers(canvas))
        state.panel_contents = content_units(_iter_layers(canvas))
        from .panel_frame_state import populate_frame_regions  # noqa: PLC0415

        populate_frame_regions(state, page_size)
        for source_layer in _iter_layers(canvas):
            for obj in getattr(source_layer, "vector_objects", None) or []:
                record = balloon_record_from_object(obj)
                if record is not None:
                    from .raster_parts import (  # noqa: PLC0415
                        ensure_root_authority,
                        ensure_tail_base_defaults,
                    )

                    state.balloons[record.balloon_id] = ensure_root_authority(
                        ensure_tail_base_defaults(record)
                    )
                    state.balloon_sources[record.balloon_id] = source_layer.id
                from .model import balloon_text_link_from_object  # noqa: PLC0415

                balloon_id = balloon_text_link_from_object(obj)
                if balloon_id:
                    state.text_objects[balloon_id] = obj
                    state.text_sources[balloon_id] = source_layer.id
        return state

    # ── layer bootstrap ────────────────────────────────────────────────

    def _frames_layer_id(self, state: ComicPageState) -> str:
        return state.frames_layer_id or f"comic-frames-{self._page_key}"

    def _balloons_layer_id(self, state: ComicPageState) -> str:
        return state.balloons_layer_id or f"comic-balloons-{self._page_key}"

    def _text_layer_id(self, state: ComicPageState) -> str:
        return state.text_layer_id or f"comic-text-{self._page_key}"

    def _ensure_frames(self, state: ComicPageState) -> list[EnsureLayerSpec]:
        if state.frames_layer_id is not None:
            return []
        return [
            EnsureLayerSpec(
                layer_id=self._frames_layer_id(state),
                name=FRAMES_LAYER_NAME,
                vector_kind="shape",
                metadata=layer_role_marker(LAYER_ROLE_FRAMES),
            )
        ]

    def _ensure_balloon_layers(self, state: ComicPageState) -> list[EnsureLayerSpec]:
        specs: list[EnsureLayerSpec] = []
        if state.balloons_layer_id is None:
            specs.append(
                EnsureLayerSpec(
                    layer_id=self._balloons_layer_id(state),
                    name=BALLOONS_LAYER_NAME,
                    vector_kind="shape",
                    metadata=layer_role_marker(LAYER_ROLE_BALLOONS),
                )
            )
        if state.text_layer_id is None:
            specs.append(
                EnsureLayerSpec(
                    layer_id=self._text_layer_id(state),
                    name=TEXT_LAYER_NAME,
                    vector_kind="text",
                    metadata=layer_role_marker(LAYER_ROLE_BALLOON_TEXT),
                )
            )
        return specs

    # ── panel commands ─────────────────────────────────────────────────

    def panel_frame_conversion_command(
        self,
        state: ComicPageState,
        panel_id: str,
        *,
        page_size: tuple[int, int],
        max_group_depth: int,
    ) -> PanelFrameMaterializeCommand:
        """Internal P1 conversion; callers submit through the existing HostAPI."""
        from .panel_frame_conversion import prepare_panel_frame_conversion  # noqa: PLC0415
        from .panel_frame_layout_adapter import layout_snapshot  # noqa: PLC0415

        if state.panel_issues or state.layout is None or panel_id not in state.panel_sources:
            raise PanelFrameError("panel state is not valid for conversion")
        snapshot = layout_snapshot(state.layout, page_size, state.panel_records)
        region = next(r for r in snapshot.regions if r.panel_id == panel_id)
        if region.layout_leaf_ids != (panel_id,):
            raise PanelFrameError("merged frame conversion requires a later capability slice")
        boundaries = {side: ref for leaf, side, ref in snapshot.boundaries if leaf == panel_id}
        source_layer_id, obj = state.panel_sources[panel_id]
        return prepare_panel_frame_conversion(
            source_layer_id=source_layer_id,
            source_object=obj,
            record=state.panel_records[panel_id],
            rect=region.rect,
            boundaries=boundaries,
            page_size=page_size,
            max_group_depth=max_group_depth,
        )

    def layout_command(
        self,
        state: ComicPageState,
        new_layout: lt.ComicPageLayout,
        *,
        page_size: tuple[float, float],
        label: str = "comic_layout",
    ) -> ComicEditCommand:
        """Materialize ``new_layout``: layer metadata + panel object diff."""

        if state.panel_issues:
            raise PanelFrameError("panel state has unresolved references")
        from .panel_convex_geometry import has_inclined_cuts  # noqa: PLC0415

        if has_inclined_cuts(new_layout.root) or (
            state.layout and has_inclined_cuts(state.layout.root)
        ):
            from .panel_convex_plan import convex_layout_command  # noqa: PLC0415

            return convex_layout_command(state, new_layout, page_size=page_size, label=label)
        if state.panel_polygons:
            from .panel_convex_plan import convex_layout_command  # noqa: PLC0415

            return convex_layout_command(state, new_layout, page_size=page_size, label=label)
        if any("frame_layer" in r.metadata for r in state.panel_records.values()):
            from .panel_layout_plan import materialized_layout_command  # noqa: PLC0415

            return materialized_layout_command(state, new_layout, page_size=page_size, label=label)
        frames_layer_id = self._frames_layer_id(state)
        rects = lt.compute_panel_rects(new_layout, *page_size)
        order = lt.normalized_reading_order(new_layout, rects)
        new_layout = replace(new_layout, reading_order=order)

        additions: list[tuple[str, Any]] = []
        replacements: list[tuple[str, Any]] = []
        removals: list[str] = []
        for panel_id, rect in rects.items():
            previous = state.panel_records.get(panel_id)
            record = PanelRecord(
                panel_id=panel_id,
                reading_order=order.index(panel_id),
                gutter_px=new_layout.gutter_px,
                metadata=dict(previous.metadata) if previous else {},
            )
            from .balloon_objects import build_panel_object  # noqa: PLC0415

            obj = build_panel_object(record, rect)
            if previous is not None:
                replacements.append((frames_layer_id, obj))
            else:
                additions.append((frames_layer_id, obj))
        for panel_id in state.panel_records:
            if panel_id not in rects:
                removals.append(f"comic-panel-{panel_id}")

        # Merge into the layer's current metadata so the role marker and any
        # sibling plugin namespaces survive; a brand-new layer starts from
        # its role marker.
        base_metadata: Mapping[str, Any] | None = state.frames_layer_metadata
        if state.frames_layer_id is None:
            base_metadata = layer_role_marker(LAYER_ROLE_FRAMES)
        metadata_updates = {
            frames_layer_id: merged_layer_metadata_with_layout(
                base_metadata, lt.layout_to_payload(new_layout)
            )
        }
        return ComicEditCommand(
            label=label,
            ensure_layers=self._ensure_frames(state),
            layer_metadata_updates=metadata_updates,
            additions=additions,
            replacements=replacements,
            removals=removals,
        )

    # ── balloon commands ───────────────────────────────────────────────

    def create_balloon_command(
        self,
        state: ComicPageState,
        *,
        center: tuple[float, float],
        panel_id: str = "",
        kind: str = "speech_round",
        text: str = "",
        vertical: bool = True,
    ) -> tuple[Command, BalloonRecord]:
        balloon_id = f"b{uuid.uuid4().hex[:8]}"
        width, height = 220.0, 150.0
        body_rect = (center[0] - width / 2, center[1] - height / 2, width, height)
        record = BalloonRecord(
            balloon_id=balloon_id,
            panel_id=panel_id or self._panel_at(state, center),
            kind=kind,
            body_rect=body_rect,
            tail=BalloonTail(target=(center[0], center[1] + height * 0.85)),
            text_object_id=f"comic-text-{balloon_id}",
        )
        if record.auto_size and text:
            record = replace(
                record,
                body_rect=autosize_body_rect(record, text, font_size_pt=20.0, vertical=vertical),
            )
        from .balloon_objects import build_balloon_object  # noqa: PLC0415

        balloon_obj = build_balloon_object(record)
        text_obj = build_balloon_text_object(record, text, vertical=vertical)
        if (
            record.panel_id in state.panel_records
            and "frame_layer" in state.panel_records[record.panel_id].metadata
        ):
            from .panel_balloon_create_plan import create_panel_balloon_command  # noqa: PLC0415

            return create_panel_balloon_command(state, record, balloon_obj, text_obj), record
        command = ComicEditCommand(
            label="comic_balloon_create",
            ensure_layers=self._ensure_balloon_layers(state),
            additions=[
                (
                    state.balloon_sources.get(record.balloon_id, self._balloons_layer_id(state)),
                    balloon_obj,
                ),
                (state.text_sources.get(record.balloon_id, self._text_layer_id(state)), text_obj),
            ],
        )
        from .panel_balloon_adoption_plan import adopt_created_balloon  # noqa: PLC0415

        return adopt_created_balloon(self, state, record, command), record

    def update_balloon_command(
        self,
        state: ComicPageState,
        record: BalloonRecord,
        *,
        text: str | None = None,
        label: str = "comic_balloon_update",
        text_props: TextProps | None = None,
    ) -> ComicEditCommand:
        """Regenerate the balloon body (and text object when ``text`` given
        or auto-size applies). The tail target is preserved as stored."""

        previous_text_obj = state.text_objects.get(record.balloon_id)
        props = text_props or (
            text_props_from_object(previous_text_obj)
            if previous_text_obj is not None
            else TextProps()
        )
        effective_text = props.text if text is None else text
        from loomstroke.core.japanese_text.linked_story import story_of  # noqa: PLC0415

        from .raster_parts import is_raster_preserve  # noqa: PLC0415

        if is_raster_preserve(record) or story_of(previous_text_obj):
            # raster balloons (rig / raster preserve): body_rect is the crop
            # PLACEMENT (origin + pixel size). Autosizing it detaches the
            # visible pixels from the hit rect and the balloon becomes
            # unselectable — never resize it from text.
            # Linked stories also need fixed frame capacities to flow to the next frame.
            record = replace(record, auto_size=False)
        elif record.auto_size:
            from .rich_text_measurement import rich_text_block  # noqa: PLC0415

            record = replace(
                record,
                body_rect=autosize_body_rect(
                    record,
                    effective_text,
                    font_size_pt=props.font_size_pt,
                    vertical=props.vertical,
                    line_height=props.line_height,
                    measured_block=rich_text_block(previous_text_obj, effective_text, props),
                ),
            )
        from .balloon_objects import build_balloon_object  # noqa: PLC0415

        balloon_obj = build_balloon_object(record)
        previous_meta = dict(getattr(previous_text_obj, "meta", None) or {}) or None
        text_obj = build_balloon_text_object(
            record,
            effective_text,
            word_wrap=props.word_wrap,
            font_size_pt=props.font_size_pt,
            vertical=props.vertical,
            vertical_flow=props.vertical_flow,
            align=props.align,
            line_height=props.line_height,
            tracking=props.tracking,
            font_family=props.font_family,
            font_style=props.font_style,
            color_rgba=props.color_rgba,
            padding_top=props.padding_top,
            padding_right=props.padding_right,
            padding_bottom=props.padding_bottom,
            padding_left=props.padding_left,
            rotate_half_width=props.rotate_half_width,
            previous_meta=previous_meta,
        )
        from .raster_parts import is_raster_preserve  # noqa: PLC0415

        return ComicEditCommand(
            label=label,
            ensure_layers=[
                spec
                for spec in self._ensure_balloon_layers(state)
                if spec.layer_id
                in {
                    state.balloon_sources.get(record.balloon_id, self._balloons_layer_id(state)),
                    state.text_sources.get(record.balloon_id, self._text_layer_id(state)),
                }
            ],
            replacements=[
                (
                    state.balloon_sources.get(record.balloon_id, self._balloons_layer_id(state)),
                    balloon_obj,
                ),
                (state.text_sources.get(record.balloon_id, self._text_layer_id(state)), text_obj),
            ],
            raster_parts=[record] if is_raster_preserve(record) else [],
            # Text-only edits must not rasterize unchanged artwork again.
            # The command still recreates a missing part layer when necessary.
            preserve_existing_raster_parts=(
                [record.balloon_id]
                if text is not None and state.balloons.get(record.balloon_id) == record
                else []
            ),
        )

    def delete_balloon_command(self, state: ComicPageState, balloon_id: str) -> Command:
        from .balloon_layer_units import unit_layer_id  # noqa: PLC0415

        unit_id = unit_layer_id(state, balloon_id)
        if unit_id:
            from loomstroke.core.commands import DeleteLayerCommand  # noqa: PLC0415

            return DeleteLayerCommand(unit_id)
        record = state.balloons.get(balloon_id)
        removals = [f"comic-balloon-{balloon_id}"]
        if record is not None and record.text_object_id:
            removals.append(record.text_object_id)
        return ComicEditCommand(
            label="comic_balloon_delete",
            removals=removals,
            raster_part_removals=[balloon_id],
        )

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _panel_at(state: ComicPageState, point: tuple[float, float]) -> str:
        if state.panel_polygons:
            for panel_id, polygon in state.panel_polygons.items():
                if all(
                    (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) >= -1e-8
                    for a, b in zip(
                        polygon.points, polygon.points[1:] + polygon.points[:1], strict=True
                    )
                ):
                    return panel_id
            return ""
        for panel_id, (x, y, w, h) in state.panel_rects.items():
            if x <= point[0] <= x + w and y <= point[1] <= y + h:
                return panel_id
        return ""

    panel_at = _panel_at


__all__ = [
    "BALLOONS_LAYER_NAME",
    "FRAMES_LAYER_NAME",
    "TEXT_LAYER_NAME",
    "ComicPageController",
    "ComicPageState",
    "TextProps",
    "text_props_from_object",
]
