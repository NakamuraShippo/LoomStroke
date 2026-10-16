"""Copy-on-write codec for the Comic Tools metadata namespaces.

Reads and merges ``metadata["plugins"]["loomstroke.comictools.book"]`` and
``metadata["plugins"]["loomstroke.comictools.page"]`` without mutating the
input and without disturbing sibling plugin namespaces. Pure Python, no I/O,
no Qt/PageService/DocumentService dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

BOOK_METADATA_NAMESPACE: Final[str] = "loomstroke.comictools.book"
PAGE_METADATA_NAMESPACE: Final[str] = "loomstroke.comictools.page"
#: CT-07 T3: document-local master page definitions (spec 07 §5.3/§10.1).
MASTERS_METADATA_NAMESPACE: Final[str] = "loomstroke.comictools.masters"

_PLUGINS_KEY: Final[str] = "plugins"

_MISSING: Final[Any] = object()


def _deep_copy_container(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_copy_container(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy_container(item) for item in value]
    return value


def _read_namespace(
    root: Mapping[str, Any] | None,
    namespace_key: str,
) -> dict[str, Any]:
    if root is None:
        return {}
    if not isinstance(root, Mapping):
        raise ValueError("metadata root must be a Mapping or None")

    plugins_raw = root.get(_PLUGINS_KEY, _MISSING)
    if plugins_raw is _MISSING:
        return {}
    if not isinstance(plugins_raw, Mapping):
        raise ValueError("metadata['plugins'] must be a Mapping")

    namespace_raw = plugins_raw.get(namespace_key, _MISSING)
    if namespace_raw is _MISSING:
        return {}
    if not isinstance(namespace_raw, Mapping):
        raise ValueError(f"metadata['plugins'][{namespace_key!r}] must be a Mapping")

    return {key: _deep_copy_container(value) for key, value in namespace_raw.items()}


def _merge_namespace(
    root: Mapping[str, Any] | None,
    updates: Mapping[str, Any],
    namespace_key: str,
) -> dict[str, Any]:
    if root is not None and not isinstance(root, Mapping):
        raise ValueError("metadata root must be a Mapping or None")
    if not isinstance(updates, Mapping):
        raise ValueError("updates must be a Mapping")

    new_root: dict[str, Any] = dict(root) if root is not None else {}

    plugins_raw = new_root.get(_PLUGINS_KEY, _MISSING)
    plugins_present = plugins_raw is not _MISSING
    if plugins_present and not isinstance(plugins_raw, Mapping):
        raise ValueError("metadata['plugins'] must be a Mapping")

    namespace_raw = plugins_raw.get(namespace_key, _MISSING) if plugins_present else _MISSING
    namespace_present = namespace_raw is not _MISSING
    if namespace_present and not isinstance(namespace_raw, Mapping):
        raise ValueError(f"metadata['plugins'][{namespace_key!r}] must be a Mapping")

    if not updates and not namespace_present:
        return new_root

    new_namespace: dict[str, Any] = (
        {key: _deep_copy_container(value) for key, value in namespace_raw.items()}
        if namespace_present
        else {}
    )
    for key, value in updates.items():
        new_namespace[key] = _deep_copy_container(value)

    new_plugins: dict[str, Any] = dict(plugins_raw) if plugins_present else {}
    new_plugins[namespace_key] = new_namespace
    new_root[_PLUGINS_KEY] = new_plugins
    return new_root


def read_book_metadata(document_metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    return _read_namespace(document_metadata, BOOK_METADATA_NAMESPACE)


def read_page_metadata(page_metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    return _read_namespace(page_metadata, PAGE_METADATA_NAMESPACE)


def merge_book_metadata(
    document_metadata: Mapping[str, Any] | None,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    return _merge_namespace(document_metadata, updates, BOOK_METADATA_NAMESPACE)


def merge_page_metadata(
    page_metadata: Mapping[str, Any] | None,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    return _merge_namespace(page_metadata, updates, PAGE_METADATA_NAMESPACE)


def read_masters_metadata(document_metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    return _read_namespace(document_metadata, MASTERS_METADATA_NAMESPACE)


def merge_masters_metadata(
    document_metadata: Mapping[str, Any] | None,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    return _merge_namespace(document_metadata, updates, MASTERS_METADATA_NAMESPACE)


__all__ = [
    "BOOK_METADATA_NAMESPACE",
    "MASTERS_METADATA_NAMESPACE",
    "PAGE_METADATA_NAMESPACE",
    "merge_book_metadata",
    "merge_masters_metadata",
    "merge_page_metadata",
    "read_book_metadata",
    "read_masters_metadata",
    "read_page_metadata",
]
