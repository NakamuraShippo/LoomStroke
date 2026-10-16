"""``export.page_raster``: write the target page as PNG/JPEG/WebP/TIFF (CT-09 export_raster).

File writes are previewed by the planner (basenames only) and never overwrite:
an existing destination blocks the step (``output_exists``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from loomstroke.core.actions import (
    ActionContext,
    ActionDescriptor,
    ActionEffect,
    ActionOutcome,
    ActionParameter,
    ActionPreview,
    ResolvedTarget,
)
from loomstroke.core.export_naming import NamingContext, NamingError, render_export_names
from loomstroke.io.layer_io import DocumentMetadata
from loomstroke.io.raster_export import export_raster

from .page_renderer import PageRenderer

__all__ = ["ExportPageAction"]

RenderPage = Callable[[Any, Any], np.ndarray]
_FORMATS = ("png", "jpg", "webp", "tif")


class ExportPageAction:
    descriptor: ClassVar[ActionDescriptor] = ActionDescriptor(
        action_id="export.page_raster",
        version=1,
        title="Export page as image",
        category="export",
        parameters=(
            ActionParameter("directory", "path", required=True),
            ActionParameter("format_id", "enum", default="png", choices=_FORMATS),
            ActionParameter("template", "str", default="{document_name}_{index}"),
            ActionParameter("zero_pad", "int", default=3, minimum=0, maximum=6),
        ),
        target_kind="page",
        effects=(ActionEffect("file_write", "raster file per page", confirmation_required=True),),
        recordable=True,
    )

    def plan(
        self, context: ActionContext, target: ResolvedTarget, params: Mapping[str, Any]
    ) -> ActionPreview:
        destination, error = self._destination(context, target, params)
        if error:
            return ActionPreview(blocked_code=error)
        assert destination is not None
        if destination.exists():
            return ActionPreview(outputs=(destination.name,), blocked_code="output_exists")
        return ActionPreview(outputs=(destination.name,))

    def execute(
        self, context: ActionContext, target: ResolvedTarget, params: Mapping[str, Any]
    ) -> ActionOutcome:
        destination, error = self._destination(context, target, params)
        if error or destination is None:
            return ActionOutcome.failure(error or "destination_invalid")
        if destination.exists():
            return ActionOutcome.failure("output_exists", destination.name)
        page = self._page_snapshot(context, target.ids[0])
        if page is None:
            return ActionOutcome.failure("page_not_found")
        metadata = self._document_metadata(context)
        render = context.extras.get("render_page")
        renderer = PageRenderer() if not callable(render) else None
        try:
            rgba = (
                renderer.render(metadata, page) if renderer is not None else render(metadata, page)
            )
            page_canvas = getattr(page, "canvas", None)
            dpi = tuple(page_canvas.dpi) if page_canvas is not None else None
            destination.parent.mkdir(parents=True, exist_ok=True)
            result = export_raster(
                np.ascontiguousarray(rgba),
                destination,
                str(params.get("format_id") or "png"),
                canvas_dpi=dpi,
                document_dpi=tuple(metadata.dpi),
                background_rgba=tuple(metadata.background_color),
            )
        except Exception as error_:  # renderer / encoder failures never raise out
            return ActionOutcome.failure("export_failed", type(error_).__name__)
        finally:
            if renderer is not None:
                renderer.release()
        if result.status == "failed":
            codes = ",".join(issue.code for issue in result.issues)
            return ActionOutcome.failure("export_failed", codes)
        return ActionOutcome.success(destination.name)

    # -- helpers -----------------------------------------------------------------------

    def _destination(
        self, context: ActionContext, target: ResolvedTarget, params: Mapping[str, Any]
    ) -> tuple[Path | None, str]:
        if not target.ids:
            return None, "target_not_found"
        raw = str(params.get("directory") or "")
        if not raw:
            return None, "parameter_missing"
        directory = Path(raw).expanduser()
        if not directory.is_absolute():
            return None, "directory_not_absolute"
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            return None, "directory_invalid"
        page_id = target.ids[0]
        index, page_name = self._page_position(context, page_id)
        naming = NamingContext(
            index=index,
            page_id=page_id,
            page_name=page_name,
            document_name=str(context.extras.get("document_name") or "untitled"),
        )
        try:
            names = render_export_names(
                str(params.get("template") or "{document_name}_{index}"),
                [naming],
                zero_pad=int(params.get("zero_pad") or 0),
                extension=str(params.get("format_id") or "png"),
            )
        except NamingError:
            return None, "template_invalid"
        return directory / names[0], ""

    @staticmethod
    def _page_position(context: ActionContext, page_id: str) -> tuple[int, str]:
        lister = getattr(context.pages, "list_pages", None)
        if callable(lister):
            try:
                for index, summary in enumerate(lister(), start=1):
                    if str(getattr(summary, "id", "")) == page_id:
                        return index, str(getattr(summary, "name", "") or "")
            except Exception:
                pass
        return 1, ""

    @staticmethod
    def _page_snapshot(context: ActionContext, page_id: str) -> Any | None:
        pages = context.pages
        persist = getattr(pages, "persist_active_page", None)
        if callable(persist):
            with contextlib.suppress(Exception):
                persist()
        getter = getattr(pages, "get_page_snapshot", None)
        if not callable(getter):
            return None
        try:
            return getter(page_id)
        except Exception:
            return None

    @staticmethod
    def _document_metadata(context: ActionContext) -> DocumentMetadata:
        metadata = context.extras.get("document_metadata")
        if isinstance(metadata, DocumentMetadata):
            return metadata
        canvas = context.canvas
        return DocumentMetadata(
            width=int(getattr(canvas, "_width", 1) or 1),
            height=int(getattr(canvas, "_height", 1) or 1),
        )
