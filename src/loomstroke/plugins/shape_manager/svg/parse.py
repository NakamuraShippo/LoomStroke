from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import fromstring as _safe_fromstring

from ...vector_tools.common import Anchor, VectorPath, VectorStyle
from ..model.shape_record import ShapeTemplate
from .path_validation import tokenize_path_data, validate_path_commands
from .point_budget import SvgPointBudget, iter_point_pairs

logger = logging.getLogger(__name__)

_COMMAND_RE = re.compile(r"[MmLlHhVvCcQqAaZz]|[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
_MAX_PATH_COMMANDS = 4096
_MAX_SVG_ANCHORS = 65_536
_MAX_ABS_COORDINATE = 1_000_000.0
_TEXT_META_KEY = "text_tool"
_SVG_TEXT_META_KEY = "svg_text"
_SVG_OPACITY_META_KEY = "svg_opacity"
_TEXT_FONT_FAMILY_FALLBACK = "sans-serif"
_TEXT_WIDTH_FACTOR = 0.6


def parse_svg(svg_text: str) -> ShapeTemplate:
    root = _safe_fromstring(svg_text)
    if _local_tag(root.tag) != "svg":
        raise ValueError("Not an SVG document")

    viewport = _parse_svg_viewport(root)
    paths: list[VectorPath] = []
    path_roles: list[str] = []
    text_entries: list[dict[str, object]] = []
    opacity_entries: list[dict[str, object]] = []
    style_holder: list[VectorStyle | None] = [None]
    budget = SvgPointBudget(_MAX_SVG_ANCHORS)
    _parse_children(
        root,
        {},
        _identity_matrix(),
        paths,
        budget=budget,
        path_roles=path_roles,
        text_entries=text_entries,
        opacity_entries=opacity_entries,
        style_holder=style_holder,
        viewport=viewport,
    )
    style = style_holder[0] or VectorStyle()

    if not paths:
        raise ValueError("No supported SVG shapes found")

    bbox = _compute_bbox(paths)
    meta: dict[str, object] = {"source": "svg"}
    if text_entries:
        meta[_SVG_TEXT_META_KEY] = text_entries
    if opacity_entries:
        meta[_SVG_OPACITY_META_KEY] = opacity_entries
    if len(text_entries) == 1 and path_roles == ["text"]:
        meta[_TEXT_META_KEY] = _text_tool_payload_from_svg_text(text_entries[0])
    return ShapeTemplate(paths=tuple(paths), style=style, bbox=bbox, meta=meta)


def _parse_children(
    node: ET.Element,
    parent_style: Mapping[str, str],
    parent_transform: tuple[float, float, float, float, float, float],
    out_paths: list[VectorPath],
    *,
    budget: SvgPointBudget,
    path_roles: list[str],
    text_entries: list[dict[str, object]],
    opacity_entries: list[dict[str, object]],
    style_holder: list[VectorStyle | None],
    viewport: tuple[float, float, float, float] | None = None,
) -> None:
    for child in list(node):
        tag = _local_tag(child.tag)
        if tag == "g":
            style = _merge_style(parent_style, _extract_style(child))
            transform = _combine_transform(
                parent_transform, _parse_transform(child.get("transform"))
            )
            _parse_children(
                child,
                style,
                transform,
                out_paths,
                budget=budget,
                path_roles=path_roles,
                text_entries=text_entries,
                opacity_entries=opacity_entries,
                style_holder=style_holder,
                viewport=viewport,
            )
            continue
        if tag not in {"path", "rect", "circle", "ellipse", "line", "polygon", "polyline", "text"}:
            continue

        style = _merge_style(parent_style, _extract_style(child))
        if tag == "rect" and _is_background_rect(child, style, viewport):
            continue
        transform = _combine_transform(parent_transform, _parse_transform(child.get("transform")))
        if tag == "text":
            text_path, text_entry = _parse_text_element(child, style, transform, budget)
            if text_path is not None and text_entry is not None:
                path_index = len(out_paths)
                text_entry["path_index"] = path_index
                out_paths.append(text_path)
                path_roles.append("text")
                opacity = _style_opacity(style)
                if opacity < 1.0:
                    text_entry["opacity"] = opacity
                    opacity_entries.append(
                        {"path_index": path_index, "opacity": opacity, "role": "text"}
                    )
                text_entries.append(text_entry)
            continue
        element_paths = _parse_shape(tag, child, transform, budget)
        if element_paths:
            start_index = len(out_paths)
            out_paths.extend(element_paths)
            path_roles.extend("shape" for _ in element_paths)
            opacity = _style_opacity(style)
            if opacity < 1.0:
                opacity_entries.extend(
                    {"path_index": start_index + offset, "opacity": opacity}
                    for offset in range(len(element_paths))
                )
            if style_holder[0] is None:
                style_holder[0] = _style_from_map(style)


def _parse_shape(  # noqa: PLR0911
    tag: str,
    elem: ET.Element,
    transform: tuple[float, float, float, float, float, float],
    budget: SvgPointBudget,
) -> list[VectorPath]:
    if tag == "path":
        data = elem.get("d", "")
        if not data:
            return []
        return _parse_path_data(data, transform, budget)
    if tag == "rect":
        return _rect_to_path(elem, transform, budget)
    if tag == "circle":
        return _circle_to_path(elem, transform, budget)
    if tag == "ellipse":
        return _ellipse_to_path(elem, transform, budget)
    if tag == "line":
        return _line_to_path(elem, transform, budget)
    if tag in {"polygon", "polyline"}:
        return _poly_to_path(elem, transform, budget, closed=(tag == "polygon"))
    return []


def _rect_to_path(
    elem: ET.Element,
    transform: tuple[float, float, float, float, float, float],
    budget: SvgPointBudget,
) -> list[VectorPath]:
    x = _parse_svg_number(elem.get("x"), default=0.0)
    y = _parse_svg_number(elem.get("y"), default=0.0)
    w = _parse_svg_number(elem.get("width"), default=0.0)
    h = _parse_svg_number(elem.get("height"), default=0.0)
    if w <= 0 or h <= 0:
        return []
    budget.consume(4)
    anchors = [
        Anchor(pos=(x, y), kind="corner"),
        Anchor(pos=(x + w, y), kind="corner"),
        Anchor(pos=(x + w, y + h), kind="corner"),
        Anchor(pos=(x, y + h), kind="corner"),
    ]
    path = VectorPath(anchors=anchors, closed=True)
    return [_apply_transform_to_path(path, transform)]


def _circle_to_path(
    elem: ET.Element,
    transform: tuple[float, float, float, float, float, float],
    budget: SvgPointBudget,
) -> list[VectorPath]:
    cx = _parse_svg_number(elem.get("cx"), default=0.0)
    cy = _parse_svg_number(elem.get("cy"), default=0.0)
    r = _parse_svg_number(elem.get("r"), default=0.0)
    if r <= 0:
        return []
    return [_ellipse_path(cx, cy, r, r, transform, budget)]


def _ellipse_to_path(
    elem: ET.Element,
    transform: tuple[float, float, float, float, float, float],
    budget: SvgPointBudget,
) -> list[VectorPath]:
    cx = _parse_svg_number(elem.get("cx"), default=0.0)
    cy = _parse_svg_number(elem.get("cy"), default=0.0)
    rx = _parse_svg_number(elem.get("rx"), default=0.0)
    ry = _parse_svg_number(elem.get("ry"), default=0.0)
    if rx <= 0 or ry <= 0:
        return []
    return [_ellipse_path(cx, cy, rx, ry, transform, budget)]


def _ellipse_path(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    transform: tuple[float, float, float, float, float, float],
    budget: SvgPointBudget,
) -> VectorPath:
    kappa = 0.5522847498307936
    kx = rx * kappa
    ky = ry * kappa
    budget.consume(4)
    anchors = [
        Anchor(pos=(cx + rx, cy), kind="smooth", h_in=(0.0, -ky), h_out=(0.0, ky)),
        Anchor(pos=(cx, cy + ry), kind="smooth", h_in=(kx, 0.0), h_out=(-kx, 0.0)),
        Anchor(pos=(cx - rx, cy), kind="smooth", h_in=(0.0, ky), h_out=(0.0, -ky)),
        Anchor(pos=(cx, cy - ry), kind="smooth", h_in=(-kx, 0.0), h_out=(kx, 0.0)),
    ]
    path = VectorPath(anchors=anchors, closed=True)
    return _apply_transform_to_path(path, transform)


def _line_to_path(
    elem: ET.Element,
    transform: tuple[float, float, float, float, float, float],
    budget: SvgPointBudget,
) -> list[VectorPath]:
    x1 = _parse_svg_number(elem.get("x1"), default=0.0)
    y1 = _parse_svg_number(elem.get("y1"), default=0.0)
    x2 = _parse_svg_number(elem.get("x2"), default=0.0)
    y2 = _parse_svg_number(elem.get("y2"), default=0.0)
    if x1 == x2 and y1 == y2:
        return []
    budget.consume(2)
    path = VectorPath(
        anchors=[
            Anchor(pos=(x1, y1), kind="corner"),
            Anchor(pos=(x2, y2), kind="corner"),
        ],
        closed=False,
    )
    return [_apply_transform_to_path(path, transform)]


def _poly_to_path(
    elem: ET.Element,
    transform: tuple[float, float, float, float, float, float],
    budget: SvgPointBudget,
    *,
    closed: bool,
) -> list[VectorPath]:
    raw = elem.get("points", "")
    pairs = iter_point_pairs(raw, _COMMAND_RE, _parse_svg_number)
    first = next(pairs, None)
    second = next(pairs, None)
    if first is None or second is None:
        return []
    budget.consume(2)
    anchors = [Anchor(pos=first, kind="corner"), Anchor(pos=second, kind="corner")]
    for point in pairs:
        budget.consume()
        anchors.append(Anchor(pos=point, kind="corner"))
    path = VectorPath(anchors=anchors, closed=closed)
    return [_apply_transform_to_path(path, transform)]


def _parse_text_element(
    elem: ET.Element,
    style: Mapping[str, str],
    transform: tuple[float, float, float, float, float, float],
    budget: SvgPointBudget,
) -> tuple[VectorPath | None, dict[str, object] | None]:
    content = _extract_text_content(elem)
    if not content:
        return (None, None)
    font_size = _parse_svg_length(style.get("font-size") or elem.get("font-size"), default=16.0)
    font_size = max(1.0, min(512.0, font_size))
    x = _parse_svg_number_list_first(elem.get("x"), default=0.0)
    y = _parse_svg_number_list_first(elem.get("y"), default=font_size)
    lines = content.splitlines() or [content]
    max_line_len = max((len(line) for line in lines), default=1)
    max_line_len = max(1, max_line_len)
    width = max(1.0, max_line_len * font_size * _TEXT_WIDTH_FACTOR)
    height = max(font_size, len(lines) * font_size * 1.2)
    top = y - font_size
    budget.consume(4)
    frame_path = _rect_path_from_bounds(x, top, width, height)
    transformed = _apply_transform_to_path(frame_path, transform)
    color = _text_color_rgba(style)
    font_family = _safe_text_font_family(style.get("font-family") or elem.get("font-family"))
    vertical = str(style.get("writing-mode") or elem.get("writing-mode") or "").startswith(
        "vertical"
    )
    entry: dict[str, object] = {
        "content": content,
        "x": x,
        "baseline_y": y,
        "frame": [x, top, width, height],
        "font_family": font_family,
        "font_size_pt": font_size,
        "color_rgba": color,
        "vertical": vertical,
    }
    return (transformed, entry)


def _rect_path_from_bounds(x: float, y: float, width: float, height: float) -> VectorPath:
    return VectorPath(
        anchors=[
            Anchor(pos=(x, y), kind="corner"),
            Anchor(pos=(x + width, y), kind="corner"),
            Anchor(pos=(x + width, y + height), kind="corner"),
            Anchor(pos=(x, y + height), kind="corner"),
        ],
        closed=True,
    )


def _extract_text_content(elem: ET.Element) -> str:
    return "".join(elem.itertext()).strip()


def _text_color_rgba(style: Mapping[str, str]) -> list[int]:
    color = _parse_color(style.get("fill")) or _parse_color(style.get("stroke")) or (0, 0, 0)
    opacity = _clamp_opacity(
        _parse_float(style.get("fill-opacity"), default=1.0) * _style_opacity(style)
    )
    return [int(color[0]), int(color[1]), int(color[2]), round(opacity * 255)]


def _safe_text_font_family(value: object) -> str:
    candidate = str(value or "").strip()
    lower = candidate.lower()
    if (
        not candidate
        or len(candidate) > 128
        or any(ord(ch) < 32 for ch in candidate)
        or any(marker in lower for marker in ("user_data", "api_key", "token", "secret"))
    ):
        return _TEXT_FONT_FAMILY_FALLBACK
    return candidate


def _text_tool_payload_from_svg_text(entry: Mapping[str, object]) -> dict[str, object]:
    content = str(entry.get("content", "") or "")
    text_end = len(content)
    font_family = _safe_text_font_family(entry.get("font_family"))
    font_size = _coerce_text_float(entry.get("font_size_pt"), default=16.0)
    color = entry.get("color_rgba")
    if not isinstance(color, list) or len(color) < 4:
        color = [0, 0, 0, 255]
    return {
        "object_type": "text",
        "content": content,
        "document": {
            "text": content,
            "runs": [{"start": 0, "end": text_end, "style_ref": "default"}],
            "paragraphs": [
                {
                    "start": 0,
                    "end": text_end,
                    "align": "left",
                    "line_height": 1.0,
                    "vertical_flow": "left",
                }
            ],
        },
        "style_map": {
            "default": {
                "font_family": font_family,
                "font_style": "",
                "font_size_pt": font_size,
                "tracking": 0.0,
                "color_rgba": [int(v) for v in color[:4]],
                "vertical": bool(entry.get("vertical", False)),
                "rotate_half_width": False,
            }
        },
        "text_mode": "point",
        "layout": {
            "wrap_mode": "none",
            "overflow_mode": "expand",
            "padding_top": 0.0,
            "padding_right": 0.0,
            "padding_bottom": 0.0,
            "padding_left": 0.0,
        },
        "font_family": font_family,
        "font_style": "",
        "font_size_pt": font_size,
        "color_rgba": [int(v) for v in color[:4]],
        "vertical": bool(entry.get("vertical", False)),
        "vertical_flow": "left",
        "align": "left",
        "line_height": 1.0,
        "tracking": 0.0,
        "auto_size": True,
    }


def _parse_path_data(  # noqa: PLR0912, PLR0915
    data: str,
    transform: tuple[float, float, float, float, float, float],
    budget: SvgPointBudget,
) -> list[VectorPath]:
    tokens = tokenize_path_data(data, _COMMAND_RE)
    if not tokens:
        return []
    command_count = sum(1 for token in tokens if token.isalpha())
    if command_count > _MAX_PATH_COMMANDS:
        raise ValueError("SVG path exceeds supported command count")
    validate_path_commands(tokens)
    paths: list[VectorPath] = []
    anchors: list[Anchor] = []
    cmd = None
    i = 0
    current = (0.0, 0.0)
    start = (0.0, 0.0)

    def _finish_path(closed: bool) -> None:
        nonlocal anchors
        if anchors:
            paths.append(VectorPath(anchors=list(anchors), closed=closed))
        anchors = []

    def _add_anchor(point: tuple[float, float]) -> None:
        budget.consume()
        anchors.append(Anchor(pos=point, kind="corner"))

    def _update_last(h_out: tuple[float, float] | None = None) -> None:
        if not anchors:
            return
        last = anchors[-1]
        anchors[-1] = Anchor(
            pos=last.pos,
            kind="smooth" if (h_out or last.h_in or last.h_out) else "corner",
            h_in=last.h_in,
            h_out=h_out if h_out is not None else last.h_out,
        )

    def _append_curve_end(
        point: tuple[float, float],
        h_in: tuple[float, float] | None,
        h_out: tuple[float, float],
    ) -> None:
        budget.consume()
        _update_last(h_out=h_out)
        anchors.append(
            Anchor(
                pos=point,
                kind="smooth" if h_in else "corner",
                h_in=h_in,
                h_out=None,
            )
        )

    while i < len(tokens):
        token = tokens[i]
        if token.isalpha():
            cmd = token
            i += 1
            if cmd in ("Z", "z"):
                if anchors:
                    if anchors[0].pos != current:
                        _add_anchor(anchors[0].pos)
                    _finish_path(closed=True)
                current = start
            continue
        if cmd is None:
            raise ValueError("SVG path values require a command")

        if cmd in ("M", "m"):
            x = _parse_svg_number(tokens[i])
            y = _parse_svg_number(tokens[i + 1])
            i += 2
            if cmd == "m":
                x += current[0]
                y += current[1]
            point = (x, y)
            if anchors:
                _finish_path(closed=False)
            _add_anchor(point)
            current = point
            start = point
            cmd = "L" if cmd == "M" else "l"
            continue

        if cmd in ("L", "l"):
            x = _parse_svg_number(tokens[i])
            y = _parse_svg_number(tokens[i + 1])
            i += 2
            if cmd == "l":
                x += current[0]
                y += current[1]
            point = (x, y)
            _add_anchor(point)
            current = point
            continue

        if cmd in ("H", "h"):
            x = _parse_svg_number(tokens[i])
            i += 1
            if cmd == "h":
                x += current[0]
            point = (x, current[1])
            _add_anchor(point)
            current = point
            continue

        if cmd in ("V", "v"):
            y = _parse_svg_number(tokens[i])
            i += 1
            if cmd == "v":
                y += current[1]
            point = (current[0], y)
            _add_anchor(point)
            current = point
            continue

        if cmd in ("C", "c"):
            x1 = _parse_svg_number(tokens[i])
            y1 = _parse_svg_number(tokens[i + 1])
            x2 = _parse_svg_number(tokens[i + 2])
            y2 = _parse_svg_number(tokens[i + 3])
            x = _parse_svg_number(tokens[i + 4])
            y = _parse_svg_number(tokens[i + 5])
            i += 6
            if cmd == "c":
                x1 += current[0]
                y1 += current[1]
                x2 += current[0]
                y2 += current[1]
                x += current[0]
                y += current[1]
            p0 = current
            p1 = (x1, y1)
            p2 = (x2, y2)
            p3 = (x, y)
            h_out = (p1[0] - p0[0], p1[1] - p0[1])
            h_in = (p2[0] - p3[0], p2[1] - p3[1])
            _append_curve_end(p3, h_in=h_in, h_out=h_out)
            current = p3
            continue

        if cmd in ("Q", "q"):
            x1 = _parse_svg_number(tokens[i])
            y1 = _parse_svg_number(tokens[i + 1])
            x = _parse_svg_number(tokens[i + 2])
            y = _parse_svg_number(tokens[i + 3])
            i += 4
            if cmd == "q":
                x1 += current[0]
                y1 += current[1]
                x += current[0]
                y += current[1]
            p0 = current
            q1 = (x1, y1)
            p2 = (x, y)
            c1 = (p0[0] + (q1[0] - p0[0]) * 2.0 / 3.0, p0[1] + (q1[1] - p0[1]) * 2.0 / 3.0)
            c2 = (p2[0] + (q1[0] - p2[0]) * 2.0 / 3.0, p2[1] + (q1[1] - p2[1]) * 2.0 / 3.0)
            h_out = (c1[0] - p0[0], c1[1] - p0[1])
            h_in = (c2[0] - p2[0], c2[1] - p2[1])
            _append_curve_end(p2, h_in=h_in, h_out=h_out)
            current = p2
            continue

        if cmd in ("A", "a"):
            # Arc not supported; degrade to line-to end point.
            if i + 6 >= len(tokens):
                raise ValueError("SVG path command has incomplete arguments")
            _ = _parse_svg_number(tokens[i])
            _ = _parse_svg_number(tokens[i + 1])
            _ = _parse_svg_number(tokens[i + 2])
            _ = _parse_svg_number(tokens[i + 3])
            _ = _parse_svg_number(tokens[i + 4])
            x = _parse_svg_number(tokens[i + 5])
            y = _parse_svg_number(tokens[i + 6])
            i += 7
            if cmd == "a":
                x += current[0]
                y += current[1]
            point = (x, y)
            _add_anchor(point)
            current = point
            logger.info("Arc command ignored during SVG import")
            continue

        raise ValueError("SVG path values require a command")

    if anchors:
        # Treat path as closed if it visually returns to the start (Z was absent
        # but start ≈ end within 0.5px), so QPainterPath.closeSubpath() is called
        # on rendering and fill behaves correctly.
        _auto_closed = _visually_closed(anchors)
        _finish_path(closed=_auto_closed)
    if transform == _identity_matrix():
        return paths
    return [_apply_transform_to_path(path, transform) for path in paths]


def _apply_transform_to_path(
    path: VectorPath, transform: tuple[float, float, float, float, float, float]
) -> VectorPath:
    anchors: list[Anchor] = []
    for anchor in path.anchors:
        pos = _apply_transform(transform, anchor.pos)
        h_in = _transform_handle(transform, anchor.pos, anchor.h_in) if anchor.h_in else None
        h_out = _transform_handle(transform, anchor.pos, anchor.h_out) if anchor.h_out else None
        kind = "smooth" if (h_in or h_out) else anchor.kind
        anchors.append(Anchor(pos=pos, kind=kind, h_in=h_in, h_out=h_out))
    return VectorPath(anchors=anchors, closed=path.closed)


def _transform_handle(
    transform: tuple[float, float, float, float, float, float],
    anchor_pos: tuple[float, float],
    handle: tuple[float, float] | None,
) -> tuple[float, float] | None:
    if handle is None:
        return None
    abs_pos = (anchor_pos[0] + handle[0], anchor_pos[1] + handle[1])
    abs_transformed = _apply_transform(transform, abs_pos)
    anchor_transformed = _apply_transform(transform, anchor_pos)
    return (abs_transformed[0] - anchor_transformed[0], abs_transformed[1] - anchor_transformed[1])


def _compute_bbox(paths: list[VectorPath]) -> tuple[float, float, float, float]:
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    for path in paths:
        for anchor in path.anchors:
            min_x = min(min_x, anchor.pos[0])
            min_y = min(min_y, anchor.pos[1])
            max_x = max(max_x, anchor.pos[0])
            max_y = max(max_y, anchor.pos[1])
            if anchor.h_in:
                hx = anchor.pos[0] + anchor.h_in[0]
                hy = anchor.pos[1] + anchor.h_in[1]
                min_x = min(min_x, hx)
                min_y = min(min_y, hy)
                max_x = max(max_x, hx)
                max_y = max(max_y, hy)
            if anchor.h_out:
                hx = anchor.pos[0] + anchor.h_out[0]
                hy = anchor.pos[1] + anchor.h_out[1]
                min_x = min(min_x, hx)
                min_y = min(min_y, hy)
                max_x = max(max_x, hx)
                max_y = max(max_y, hy)
    if min_x == float("inf"):
        return (0.0, 0.0, 0.0, 0.0)
    return (min_x, min_y, max_x, max_y)


def _parse_transform(raw: str | None) -> tuple[float, float, float, float, float, float]:
    if not raw:
        return _identity_matrix()
    transform = _identity_matrix()
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", raw):
        values = _parse_floats(args)
        if name == "translate":
            tx = values[0] if values else 0.0
            ty = values[1] if len(values) > 1 else 0.0
            transform = _combine_transform(transform, (1.0, 0.0, 0.0, 1.0, tx, ty))
        elif name == "scale":
            sx = values[0] if values else 1.0
            sy = values[1] if len(values) > 1 else sx
            transform = _combine_transform(transform, (sx, 0.0, 0.0, sy, 0.0, 0.0))
        elif name == "rotate":
            angle = values[0] if values else 0.0
            rad = math.radians(angle)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            rot = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(values) >= 3:
                cx, cy = values[1], values[2]
                transform = _combine_transform(transform, (1.0, 0.0, 0.0, 1.0, cx, cy))
                transform = _combine_transform(transform, rot)
                transform = _combine_transform(transform, (1.0, 0.0, 0.0, 1.0, -cx, -cy))
            else:
                transform = _combine_transform(transform, rot)
        elif name == "matrix" and len(values) >= 6:
            mat = (values[0], values[1], values[2], values[3], values[4], values[5])
            transform = _combine_transform(transform, mat)
        else:
            logger.info("Unsupported transform: %s", name)
    return transform


def _apply_transform(
    mat: tuple[float, float, float, float, float, float], point: tuple[float, float]
) -> tuple[float, float]:
    a, b, c, d, e, f = mat
    x = point[0]
    y = point[1]
    return (a * x + c * y + e, b * x + d * y + f)


def _combine_transform(
    first: tuple[float, float, float, float, float, float],
    second: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _identity_matrix() -> tuple[float, float, float, float, float, float]:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _merge_style(parent: Mapping[str, str], child: Mapping[str, str]) -> dict[str, str]:
    merged = dict(parent)
    merged.update(child)
    parent_opacity = _style_opacity(parent)
    child_opacity = _style_opacity(child)
    combined_opacity = parent_opacity * child_opacity
    if combined_opacity < 1.0 or "opacity" in parent or "opacity" in child:
        merged["opacity"] = f"{_clamp_opacity(combined_opacity):.6f}"
    return merged


def _extract_style(elem: ET.Element) -> dict[str, str]:
    style_map: dict[str, str] = {}
    if "style" in elem.attrib:
        style_map.update(_parse_style_string(elem.attrib.get("style", "")))
    for key in (
        "fill",
        "fill-opacity",
        "font-family",
        "font-size",
        "opacity",
        "writing-mode",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-dasharray",
    ):
        if key in elem.attrib:
            style_map[key] = elem.attrib[key]
    return style_map


def _parse_style_string(style: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for chunk in style.split(";"):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key:
            result[key] = value
    return result


def _style_opacity(style: Mapping[str, str]) -> float:
    return _clamp_opacity(_parse_float(style.get("opacity"), default=1.0))


def _clamp_opacity(value: float) -> float:
    return max(0.0, min(1.0, value if math.isfinite(value) else 1.0))


def _style_from_map(style: Mapping[str, str]) -> VectorStyle:
    stroke_color = _parse_color(style.get("stroke")) or (0, 0, 0)
    fill_color = _parse_color(style.get("fill"))
    stroke_width = _parse_float(style.get("stroke-width"), default=2.0)
    dash = tuple(_parse_floats(style.get("stroke-dasharray", "")))
    return VectorStyle(
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        fill_enabled=fill_color is not None,
        fill_color=fill_color,
        join=style.get("stroke-linejoin", "miter"),
        cap=style.get("stroke-linecap", "butt"),
        miter_limit=_parse_float(style.get("stroke-miterlimit"), default=4.0),
        dash=dash,
    )


def _parse_color(value: str | None) -> tuple[int, int, int] | None:  # noqa: PLR0911
    if not value:
        return None
    val = value.strip()
    if val == "none":
        return None
    if val.startswith("#"):
        hex_part = val[1:]
        if len(hex_part) == 3:
            try:
                r = int(hex_part[0] * 2, 16)
                g = int(hex_part[1] * 2, 16)
                b = int(hex_part[2] * 2, 16)
                return (r, g, b)
            except ValueError:
                return None
        if len(hex_part) == 6:
            try:
                r = int(hex_part[0:2], 16)
                g = int(hex_part[2:4], 16)
                b = int(hex_part[4:6], 16)
                return (r, g, b)
            except ValueError:
                return None
    if val.startswith("rgb"):
        nums = _parse_floats(val[val.find("(") + 1 : val.find(")")])
        if len(nums) >= 3:
            return (int(nums[0]), int(nums[1]), int(nums[2]))
    return None


def _parse_floats(text: str) -> list[float]:
    if not text:
        return []
    return [_parse_svg_number(token) for token in _COMMAND_RE.findall(text) if not token.isalpha()]


def _parse_float(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        return _parse_svg_number(value)
    except ValueError:
        return default


def _parse_svg_length(value: object, *, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return _parse_svg_number(value)
    except ValueError:
        values = _parse_floats(str(value))
        return values[0] if values else default


def _parse_svg_number_list_first(value: object, *, default: float) -> float:
    if value is None or value == "":
        return default
    values = _parse_floats(str(value))
    return values[0] if values else default


def _coerce_text_float(value: object, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _parse_svg_number(value: object, *, default: float | None = None) -> float:
    if value is None or value == "":
        if default is None:
            raise ValueError("SVG numeric value is missing")
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid SVG numeric value: {value!r}") from exc
    if not math.isfinite(number) or abs(number) > _MAX_ABS_COORDINATE:
        raise ValueError("SVG numeric value outside supported range")
    return number


def _visually_closed(anchors: list[Anchor]) -> bool:
    """Return True when the path starts and ends at the same position (within 0.5 px)."""
    if len(anchors) < 3:
        return False
    fx, fy = anchors[0].pos
    lx, ly = anchors[-1].pos
    return (fx - lx) ** 2 + (fy - ly) ** 2 < 0.25


def _parse_svg_viewport(
    root: ET.Element,
) -> tuple[float, float, float, float] | None:
    """Return (x, y, width, height) of the SVG viewport, or None if not determinable."""
    vb = root.get("viewBox") or root.get("viewbox")
    if vb:
        vals = _parse_floats(vb)
        if len(vals) >= 4:
            return (vals[0], vals[1], vals[2], vals[3])
    try:
        w = _parse_svg_number(root.get("width"))
        h = _parse_svg_number(root.get("height"))
        if w > 0 and h > 0:
            return (0.0, 0.0, w, h)
    except ValueError:
        logger.debug("svg width/height parse failed", exc_info=True)
    return None


def _is_background_rect(
    elem: ET.Element,
    style: Mapping[str, str],
    viewport: tuple[float, float, float, float] | None,
) -> bool:
    """Return True if this <rect> appears to be a background/canvas rectangle to skip."""
    if viewport is None:
        return False
    try:
        rx = _parse_svg_number(elem.get("x"), default=0.0)
        ry = _parse_svg_number(elem.get("y"), default=0.0)
        rw = _parse_svg_number(elem.get("width"), default=0.0)
        rh = _parse_svg_number(elem.get("height"), default=0.0)
    except (TypeError, ValueError):
        return False
    vx, vy, vw, vh = viewport
    tol = max(2.0, max(vw, vh) * 0.01)
    if not (
        abs(rx - vx) <= tol and abs(ry - vy) <= tol and abs(rw - vw) <= tol and abs(rh - vh) <= tol
    ):
        return False
    # Only skip if there is no visible stroke (pure background rect)
    stroke = style.get("stroke", "").strip()
    return not (stroke and stroke != "none")


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag
