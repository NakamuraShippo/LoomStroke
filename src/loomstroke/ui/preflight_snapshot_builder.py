"""Main-thread snapshot of the document for preflight (CT-16 §7 / §11).

Reads host state (page summaries, page snapshots, vector text payloads,
Qt font database) once and produces the immutable, Qt-free
:class:`DocumentPreflightSnapshot` the pure rules consume.  Text overflow
is decided here with the CT-03 layout diagnostics (horizontal area text);
vertical area text is left ``None`` so the rule reports it as not
verified instead of passing.  Pages hydrated for the scan are handed back
to the CT-08 budget after each page.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
from PySide6.QtGui import QFontDatabase, QFontMetricsF

from ..core.preflight.document_preflight_snapshot import DocumentPreflightSnapshot
from ..core.preflight.layer_preflight_snapshot import LayerPreflightSnapshot
from ..core.preflight.page_preflight_snapshot import PagePreflightSnapshot
from ..core.preflight.preflight_fingerprint import PreflightFingerprint
from ..core.preflight.text_preflight_snapshot import TextPreflightSnapshot
from ..core.vector_text_layout import TextLayoutCalculator
from ..io.layer_io import DocumentMetadata
from .text_preflight_facts import character_font_facts, rich_text_overflow

logger = logging.getLogger(__name__)

__all__ = ["DocumentPreflightSnapshotBuilder"]

FontAvailable = Callable[[str], bool | None]


class DocumentPreflightSnapshotBuilder:
    """Build snapshots / cheap fingerprints from the page service."""

    def __init__(
        self,
        page_service: Any,
        document_metadata: DocumentMetadata,
        *,
        document_id: str = "",
        document_name: str = "",
        has_color_profile: bool = False,
        reading_direction: str = "ltr",
        roles_by_id: Mapping[str, str] | None = None,
        font_available: FontAvailable | None = None,
    ) -> None:
        self._pages = page_service
        self._metadata = document_metadata
        self._document_id = document_id or document_name or "document"
        self._document_name = document_name
        self._has_color_profile = bool(has_color_profile)
        self._reading_direction = reading_direction
        self._roles = dict(roles_by_id or {})
        self._font_available = font_available or self._qt_font_available

    # -- public ------------------------------------------------------------------

    def build(self, page_ids: Sequence[str] | None = None) -> DocumentPreflightSnapshot:
        summaries = self._summaries()
        wanted = set(page_ids) if page_ids is not None else None
        pages: list[PagePreflightSnapshot] = []
        for index, summary in enumerate(summaries):
            page_id = str(summary.id)
            if wanted is not None and page_id not in wanted:
                continue
            pages.append(self._page_snapshot(summary, index))
        return DocumentPreflightSnapshot(
            document_id=self._document_id,
            document_name=self._document_name,
            pages=tuple(pages),
            reading_direction=self._reading_direction,
            has_color_profile=self._has_color_profile,
            dirty_signature=self.dirty_signature_for(summaries),
        )

    def structure_fingerprint(self) -> str:
        return self.structure_fingerprint_for(self._summaries(), self._metadata, self._roles)

    def dirty_signature(self) -> str:
        return self.dirty_signature_for(self._summaries())

    @staticmethod
    def structure_fingerprint_for(
        summaries: Sequence[Any],
        metadata: DocumentMetadata,
        roles_by_id: Mapping[str, str] | None = None,
    ) -> str:
        rows = []
        for summary in summaries:
            canvas = getattr(summary, "canvas", None)
            width = int(getattr(canvas, "width", 0) or metadata.width)
            height = int(getattr(canvas, "height", 0) or metadata.height)
            dpi = tuple(getattr(canvas, "dpi", None) or metadata.dpi)
            role = str((roles_by_id or {}).get(str(summary.id), "body"))
            rows.append((str(summary.id), width, height, (float(dpi[0]), float(dpi[1])), role))
        return PreflightFingerprint.structure_from_rows(rows)

    @staticmethod
    def dirty_signature_for(summaries: Sequence[Any]) -> str:
        return ",".join(
            sorted(
                str(summary.id) for summary in summaries if bool(getattr(summary, "dirty", False))
            )
        )

    # -- internals ---------------------------------------------------------------

    def _summaries(self) -> list[Any]:
        list_pages = getattr(self._pages, "list_pages", None)
        return list(list_pages() or []) if callable(list_pages) else []

    def _page_snapshot(self, summary: Any, index: int) -> PagePreflightSnapshot:
        page_id = str(summary.id)
        canvas = getattr(summary, "canvas", None)
        width = int(getattr(canvas, "width", 0) or self._metadata.width)
        height = int(getattr(canvas, "height", 0) or self._metadata.height)
        dpi = tuple(getattr(canvas, "dpi", None) or self._metadata.dpi)
        layers: list[LayerPreflightSnapshot] = []
        texts: list[TextPreflightSnapshot] = []
        hydrated = False
        try:
            page = self._pages.get_page_snapshot(page_id)
            if page is not None:
                hydrated = True
                for layer in getattr(page, "layers", None) or []:
                    self._collect_layer(layer, layers, texts)
        except Exception:
            logger.warning("preflight: page %s could not be read", page_id, exc_info=True)
        finally:
            evict = getattr(self._pages, "evict_pages_to_budget", None)
            if callable(evict):
                evict()
        return PagePreflightSnapshot(
            page_id=page_id,
            index=index,
            name=str(getattr(summary, "name", "") or page_id),
            width=width,
            height=height,
            dpi=(float(dpi[0]), float(dpi[1])),
            role=str(self._roles.get(page_id, "body")),
            layers=tuple(layers),
            texts=tuple(texts),
            has_physical_layout=False,
            hydrated=hydrated,
        )

    def _collect_layer(
        self,
        layer: Any,
        layers: list[LayerPreflightSnapshot],
        texts: list[TextPreflightSnapshot],
    ) -> None:
        layer_id = str(getattr(layer, "id", "") or "")
        layer_type = str(getattr(layer, "layer_type", "raster") or "raster")
        vector_objects = list(getattr(layer, "vector_objects", None) or [])
        children = list(getattr(layer, "children", None) or [])
        image = getattr(layer, "image", None)
        has_pixels: bool | None
        if layer_type == "group":
            has_pixels = None
        elif layer_type == "vector":
            has_pixels = bool(vector_objects)
        elif getattr(layer, "effect_type", None):
            has_pixels = False
        else:
            has_pixels = self._image_has_pixels(image)
        layers.append(
            LayerPreflightSnapshot(
                layer_id=layer_id,
                name=str(getattr(layer, "name", "") or layer_id),
                layer_type=layer_type,
                visible=bool(getattr(layer, "visible", True)),
                locked=bool(getattr(layer, "locked", False)),
                opacity=float(getattr(layer, "opacity", 1.0) or 0.0),
                has_pixels=has_pixels,
                bounds=getattr(layer, "bounds", None),
                effect_type=getattr(layer, "effect_type", None),
                object_count=len(vector_objects),
            )
        )
        for vec in vector_objects:
            text = self._text_snapshot(layer_id, vec)
            if text is not None:
                texts.append(text)
        for child in children:
            self._collect_layer(child, layers, texts)

    @staticmethod
    def _image_has_pixels(image: Any) -> bool | None:
        if image is None:
            return None
        try:
            array = np.asarray(image)
            if array.ndim == 3 and array.shape[2] == 4:
                return bool(np.any(array[:, :, 3]))
            return bool(array.size) and bool(np.any(array))
        except Exception:
            return None

    def _text_snapshot(self, layer_id: str, vec: Any) -> TextPreflightSnapshot | None:
        payload = TextLayoutCalculator.extract_text_payload(vec)
        if payload is None:
            return None
        content = TextLayoutCalculator.text_content_from_payload(payload)
        style = TextLayoutCalculator.text_style_defaults(payload)
        family = str(style.get("font_family", payload.get("font_family", "")) or "").strip()
        try:
            size_pt = float(style.get("font_size_pt", payload.get("font_size_pt", 24.0)))
        except (TypeError, ValueError):
            size_pt = 24.0
        text_mode = TextLayoutCalculator.text_mode_from_payload(payload)
        vertical = bool(style.get("vertical", payload.get("vertical", False)))
        bounds = TextLayoutCalculator.text_bbox_from_payload(payload)
        overflow = self._overflow(payload, content, text_mode, vertical, bounds)
        size_pt, available, missing, families = character_font_facts(
            payload, family, size_pt, self._font_available
        )
        return TextPreflightSnapshot(
            object_id=str(getattr(vec, "id", "") or ""),
            layer_id=layer_id,
            content=content,
            font_family=family,
            font_size_pt=size_pt,
            text_mode=text_mode,
            vertical=vertical,
            bounds=bounds,
            overflow=overflow,
            font_available=available,
            missing_font_families=missing,
            font_families=families,
        )

    @staticmethod
    def _overflow(
        payload: Mapping[str, Any],
        content: str,
        text_mode: str,
        vertical: bool,
        bounds: tuple[float, float, float, float] | None,
    ) -> bool | None:
        if text_mode != "area":
            return False
        try:
            rich = rich_text_overflow(payload)
            if rich is not None:
                return rich
        except Exception:
            logger.debug("preflight: rich text overflow check failed", exc_info=True)
            return None
        if vertical or bounds is None or not content:
            return None if vertical else False
        try:
            metrics = QFontMetricsF(TextLayoutCalculator.make_text_font(payload))
            line_step = metrics.lineSpacing() * TextLayoutCalculator.text_line_height_from_payload(
                payload
            )
            wrap_mode = TextLayoutCalculator.text_wrap_mode_from_payload(
                payload, text_mode=text_mode
            )
            issues = TextLayoutCalculator.horizontal_text_layout_issues(
                content,
                metrics,
                width=bounds[2] - bounds[0],
                height=bounds[3] - bounds[1],
                line_step=line_step,
                wrap_mode=wrap_mode,
            )
        except Exception:
            logger.debug("preflight: text overflow check failed", exc_info=True)
            return None
        return any(issue.code.startswith("text_overflow") for issue in issues)

    @staticmethod
    def _qt_font_available(family: str) -> bool | None:
        try:
            return bool(QFontDatabase.hasFamily(family))
        except Exception:
            return None
