"""Metadata revisions for edits outside the active canvas, without page hydration."""

from __future__ import annotations

from typing import Any

from .extension_services import get_services
from .serialization import to_jsonable


def metadata_revision(service: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    pages = getattr(service, "page_service", None)
    list_pages = getattr(pages, "list_pages", None)
    metadata = getattr(pages, "get_page_metadata", None)
    if callable(list_pages):
        summaries = list_pages()
        result["pages"] = to_jsonable(summaries)
        if callable(metadata):
            result["pageMetadata"] = {
                str(page.id): to_jsonable(metadata(page.id)) for page in summaries
            }
    document = getattr(getattr(service, "host_api", None), "document", None)
    if document is None:
        document = get_services(service).get("document_service")
    for name in ("resolve_book_layout", "read_masters_metadata"):
        reader = getattr(document, name, None)
        if callable(reader):
            result[name] = to_jsonable(reader())
    return result
