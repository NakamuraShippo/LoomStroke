"""Runtime-only metadata resolver for Comic Tools book layout (CT-06 Phase B3-b).

Bridges the B3-a defensive metadata codec (:mod:`comic_metadata_codec`) and
the B1 pure spread resolver (:mod:`book_spread_layout`): reads the optional
``loomstroke.comictools.book`` / ``loomstroke.comictools.page`` namespaces and
derives the existing ``BookLayout`` / ``PageBookAttributes`` runtime models,
falling back to safe defaults (with a warning) for malformed containers,
malformed schema versions, unsupported future schema versions, and invalid
field values. Never mutates metadata, never implements its own spread
algorithm, and has no Qt/I/O/PageService/HostAPI/Command/EventBus dependency.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from .book_spread_layout import (
    BODY_ROLE,
    BindingDirection,
    BodyStartSide,
    BookLayout,
    DefaultView,
    PageBookAttributes,
    SpreadDescriptor,
    resolve_spreads,
)
from .comic_metadata_codec import read_book_metadata, read_page_metadata

logger = logging.getLogger(__name__)

_SUPPORTED_SCHEMA_VERSION = 1

_VALID_BINDINGS: tuple[str, ...] = ("left", "right")
_VALID_BODY_STARTS: tuple[str, ...] = ("auto", "recto", "verso")
_VALID_DEFAULT_VIEWS: tuple[str, ...] = ("single", "spread")

FolioMode = Literal["auto", "hidden", "custom"]
_VALID_FOLIO_MODES: tuple[str, ...] = ("auto", "hidden", "custom")


def _is_supported_schema_version(namespace: Mapping[str, Any], *, category: str) -> bool:
    if "schema_version" not in namespace:
        return True
    version = namespace["schema_version"]
    if type(version) is int and version == _SUPPORTED_SCHEMA_VERSION:
        return True
    logger.warning(
        "comic_%s_schema_unsupported: schema_version not recognized as version %s; "
        "falling back to defaults",
        category,
        _SUPPORTED_SCHEMA_VERSION,
    )
    return False


def _warn_field_invalid(category: str, field: str, fallback: object) -> None:
    logger.warning(
        "comic_%s_field_invalid: field=%s invalid; falling back to %r",
        category,
        field,
        fallback,
    )


def _validated_binding(namespace: Mapping[str, Any]) -> BindingDirection:
    if "binding" not in namespace:
        return "left"
    value = namespace["binding"]
    if value in _VALID_BINDINGS:
        return cast(BindingDirection, value)
    _warn_field_invalid("book", "binding", "left")
    return "left"


def _validated_body_start(namespace: Mapping[str, Any]) -> BodyStartSide:
    if "body_start" not in namespace:
        return "auto"
    value = namespace["body_start"]
    if value in _VALID_BODY_STARTS:
        return cast(BodyStartSide, value)
    _warn_field_invalid("book", "body_start", "auto")
    return "auto"


def _validated_default_view(namespace: Mapping[str, Any]) -> DefaultView:
    if "default_view" not in namespace:
        return "single"
    value = namespace["default_view"]
    if value in _VALID_DEFAULT_VIEWS:
        return cast(DefaultView, value)
    _warn_field_invalid("book", "default_view", "single")
    return "single"


def _validated_folio_start(namespace: Mapping[str, Any]) -> int:
    if "folio_start" not in namespace:
        return 1
    value = namespace["folio_start"]
    if type(value) is int:
        return value
    _warn_field_invalid("book", "folio_start", 1)
    return 1


def _validated_folio_prefix(namespace: Mapping[str, Any]) -> str:
    if "folio_prefix" not in namespace:
        return ""
    value = namespace["folio_prefix"]
    if isinstance(value, str):
        return value
    _warn_field_invalid("book", "folio_prefix", "")
    return ""


def _validated_folio_hidden_roles(namespace: Mapping[str, Any]) -> tuple[str, ...]:
    if "folio_hidden_roles" not in namespace:
        return ()
    value = namespace["folio_hidden_roles"]
    if (
        isinstance(value, Sequence)
        and not isinstance(value, str)
        and all(isinstance(item, str) for item in value)
    ):
        return tuple(value)
    _warn_field_invalid("book", "folio_hidden_roles", ())
    return ()


def _validated_role(namespace: Mapping[str, Any]) -> str:
    if "role" not in namespace:
        return BODY_ROLE
    value = namespace["role"]
    if isinstance(value, str):
        return value
    _warn_field_invalid("page", "role", BODY_ROLE)
    return BODY_ROLE


def _validated_folio_mode(namespace: Mapping[str, Any]) -> FolioMode:
    if "folio_mode" not in namespace:
        return "auto"
    value = namespace["folio_mode"]
    if value in _VALID_FOLIO_MODES:
        return cast(FolioMode, value)
    _warn_field_invalid("page", "folio_mode", "auto")
    return "auto"


def _validated_custom_folio(namespace: Mapping[str, Any]) -> str | None:
    if "custom_folio" not in namespace:
        return None
    value = namespace["custom_folio"]
    if value is None or isinstance(value, str):
        return value
    _warn_field_invalid("page", "custom_folio", None)
    return None


class BookLayoutService:
    """Stateless resolver: Comic Tools metadata -> ``BookLayout`` / ``PageBookAttributes``.

    Runtime-only (CT-06 Phase B3-b): reads via the B3-a codec, never writes
    metadata, and delegates spread derivation entirely to the existing B1
    ``resolve_spreads()``.
    """

    def resolve_layout(self, document_metadata: Mapping[str, Any] | None) -> BookLayout:
        try:
            namespace = read_book_metadata(document_metadata)
        except ValueError:
            logger.warning(
                "comic_book_metadata_malformed: metadata container malformed; "
                "falling back to BookLayout defaults"
            )
            return BookLayout()

        if not namespace:
            return BookLayout()
        if not _is_supported_schema_version(namespace, category="book"):
            return BookLayout()

        return BookLayout(
            binding=_validated_binding(namespace),
            body_start=_validated_body_start(namespace),
            # How the book opens (Single/Spread). B3-b pinned this to
            # "single" because the view was runtime-only UI state; the Pages
            # panel now stores it here so a book drawn as spreads reopens
            # as spreads.
            default_view=_validated_default_view(namespace),
            folio_start=_validated_folio_start(namespace),
            folio_prefix=_validated_folio_prefix(namespace),
            folio_hidden_roles=_validated_folio_hidden_roles(namespace),
        )

    def resolve_page_attributes(
        self, page_metadata: Mapping[str, Any] | None
    ) -> PageBookAttributes:
        try:
            namespace = read_page_metadata(page_metadata)
        except ValueError:
            logger.warning(
                "comic_page_metadata_malformed: metadata container malformed; "
                "falling back to PageBookAttributes defaults"
            )
            return PageBookAttributes()

        if not namespace:
            return PageBookAttributes()
        if not _is_supported_schema_version(namespace, category="page"):
            return PageBookAttributes()

        return PageBookAttributes(
            role=_validated_role(namespace),
            folio_mode=_validated_folio_mode(namespace),
            custom_folio=_validated_custom_folio(namespace),
        )

    def resolve_page_attributes_by_id(
        self,
        page_metadata_by_id: Mapping[str, Mapping[str, Any] | None],
    ) -> dict[str, PageBookAttributes]:
        return {
            page_id: self.resolve_page_attributes(metadata)
            for page_id, metadata in page_metadata_by_id.items()
        }

    def resolve_spreads_from_metadata(
        self,
        page_ids: Sequence[str],
        document_metadata: Mapping[str, Any] | None,
        page_metadata_by_id: Mapping[str, Mapping[str, Any] | None] | None = None,
    ) -> tuple[SpreadDescriptor, ...]:
        layout = self.resolve_layout(document_metadata)
        attributes = self.resolve_page_attributes_by_id(page_metadata_by_id or {})
        return resolve_spreads(page_ids, layout, attributes)


__all__ = ["BookLayoutService"]
