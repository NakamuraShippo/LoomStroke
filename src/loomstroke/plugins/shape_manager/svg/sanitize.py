from __future__ import annotations

import logging
import re
from xml.etree import ElementTree as _StdET

from defusedxml import ElementTree as _DefusedET
from defusedxml.common import DefusedXmlException

logger = logging.getLogger(__name__)

_ALLOWED_TAGS = {
    "svg",
    "g",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polygon",
    "polyline",
    "text",
    "tspan",
}
_UNSAFE_TAGS = {
    "script",
    "foreignobject",
    "image",
    "use",
    "style",
    "iframe",
    "object",
    "embed",
    "link",
}
_ALLOWED_ATTRS = {
    "d",
    "x",
    "x1",
    "x2",
    "y",
    "y1",
    "y2",
    "width",
    "height",
    "rx",
    "ry",
    "cx",
    "cy",
    "r",
    "points",
    "transform",
    "style",
    "fill",
    "fill-opacity",
    "font-family",
    "font-size",
    "opacity",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
    "viewBox",
    "writing-mode",
    "preserveAspectRatio",
}
_STYLE_ATTRS = {
    "fill",
    "fill-opacity",
    "opacity",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
}
_ROOT_ATTRS = {
    "height": "height",
    "preserveaspectratio": "preserveAspectRatio",
    "viewbox": "viewBox",
    "width": "width",
}
_ATTR_NAMES = {name.lower(): name for name in _ALLOWED_ATTRS}
_DANGEROUS_VALUE_RE = re.compile(
    r"@import|data:|file:|https?://|javascript:|url\s*\(",
    re.IGNORECASE,
)
_MAX_SVG_BYTES = 2 * 1024 * 1024
_MAX_ELEMENT_COUNT = 2048
_MAX_DEPTH = 32


def sanitize_svg(svg_text: str) -> str:
    """Return a sanitized SVG string containing only safe elements and attributes."""
    if len(svg_text.encode("utf-8")) > _MAX_SVG_BYTES:
        raise ValueError("SVG exceeds supported import size")

    try:
        root = _DefusedET.fromstring(svg_text)
    except (_StdET.ParseError, DefusedXmlException) as exc:
        raise ValueError(f"Invalid SVG: {exc}") from exc

    root_tag = _local_tag(root.tag)
    if root_tag != "svg":
        raise ValueError("Not an SVG root element")

    sanitized_root = _StdET.Element("svg")
    sanitized_root.set("xmlns", "http://www.w3.org/2000/svg")
    _copy_attrs(root, sanitized_root, allow_root=True)

    budget = [1]
    for child in list(root):
        _copy_element(child, sanitized_root, depth=1, budget=budget)

    return _StdET.tostring(sanitized_root, encoding="unicode")


def _copy_element(
    src: _StdET.Element, dst_parent: _StdET.Element, *, depth: int, budget: list[int]
) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError("SVG nesting exceeds supported import depth")
    budget[0] += 1
    if budget[0] > _MAX_ELEMENT_COUNT:
        raise ValueError("SVG exceeds supported element count")

    tag = _local_tag(src.tag)
    tag_key = tag.lower()
    if tag_key in _UNSAFE_TAGS:
        raise ValueError(f"Unsafe SVG element is not allowed: {tag}")
    _check_attrs_safe(src)
    if tag not in _ALLOWED_TAGS:
        for child in list(src):
            _scan_element_safety(child, depth=depth + 1, budget=budget)
        return
    if tag == "svg":
        return
    if tag == "g":
        dst = _StdET.SubElement(dst_parent, "g")
        _copy_attrs(src, dst, allow_root=False)
        for child in list(src):
            _copy_element(child, dst, depth=depth + 1, budget=budget)
        return
    if tag in {"text", "tspan"}:
        _copy_text_element(src, dst_parent, tag=tag, depth=depth, budget=budget)
        return

    dst = _StdET.SubElement(dst_parent, tag)
    _copy_attrs(src, dst, allow_root=False)


def _copy_attrs(src: _StdET.Element, dst: _StdET.Element, *, allow_root: bool) -> None:
    for key, value in src.attrib.items():
        name = _local_attr(key)
        name_key = name.lower()
        _reject_unsafe_attr(name, value)
        if allow_root and name_key in _ROOT_ATTRS:
            dst.set(_ROOT_ATTRS[name_key], value)
            continue
        attr_name = _ATTR_NAMES.get(name_key)
        if attr_name is None:
            continue
        if attr_name == "style":
            sanitized_style = _sanitize_style(value)
            if sanitized_style:
                dst.set(attr_name, sanitized_style)
            continue
        dst.set(attr_name, value)


def _sanitize_style(value: str) -> str:
    declarations: list[str] = []
    for chunk in value.split(";"):
        if ":" not in chunk:
            continue
        raw_name, raw_value = chunk.split(":", 1)
        name = raw_name.strip().lower()
        style_value = raw_value.strip()
        if not name or not style_value:
            continue
        if name not in _STYLE_ATTRS:
            continue
        declarations.append(f"{name}: {style_value}")
    return "; ".join(declarations)


def _copy_text_content(src: _StdET.Element, dst: _StdET.Element) -> None:
    if src.text:
        dst.text = src.text
    if src.tail:
        dst.tail = src.tail


def _copy_text_element(
    src: _StdET.Element,
    dst_parent: _StdET.Element,
    *,
    tag: str,
    depth: int,
    budget: list[int],
) -> None:
    dst = _StdET.SubElement(dst_parent, tag)
    _copy_attrs(src, dst, allow_root=False)
    _copy_text_content(src, dst)
    for child in list(src):
        if tag == "text" and _local_tag(child.tag) == "tspan":
            _copy_element(child, dst, depth=depth + 1, budget=budget)
        else:
            _scan_element_safety(child, depth=depth + 1, budget=budget)


def _check_attrs_safe(src: _StdET.Element) -> None:
    for key, value in src.attrib.items():
        _reject_unsafe_attr(_local_attr(key), value)


def _scan_element_safety(src: _StdET.Element, *, depth: int, budget: list[int]) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError("SVG nesting exceeds supported import depth")
    budget[0] += 1
    if budget[0] > _MAX_ELEMENT_COUNT:
        raise ValueError("SVG exceeds supported element count")
    tag = _local_tag(src.tag)
    if tag.lower() in _UNSAFE_TAGS:
        raise ValueError(f"Unsafe SVG element is not allowed: {tag}")
    _check_attrs_safe(src)
    for child in list(src):
        _scan_element_safety(child, depth=depth + 1, budget=budget)


def _reject_unsafe_attr(name: str, value: str) -> None:
    name_key = name.lower()
    if name_key.startswith("on"):
        raise ValueError(f"Unsafe SVG event attribute is not allowed: {name}")
    if name_key in ("href", "xlink:href"):
        raise ValueError("SVG external references are not allowed")
    if _DANGEROUS_VALUE_RE.search(value):
        raise ValueError(f"Unsafe SVG attribute value is not allowed: {name}")


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _local_attr(attr: str) -> str:
    if "}" in attr:
        return attr.split("}", 1)[1]
    return attr
