"""Logical page order -> publication items (CT-26 spec §データモデル / CBZ).

Story order is the only output order; binding never reverses it.  CT-06
page roles map onto the four publication roles, the cover is the first
item, and ``package_path`` is ``pages/000001.<ext>`` so lexical order ==
logical order inside every archive.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from .publication_item import PublicationItem, PublicationRole, SpreadSide

__all__ = ["DigitalComicPackagePlanner"]


class DigitalComicPackagePlanner:
    """Assign logical indices, roles and member paths."""

    ROLE_MAP: ClassVar[dict[str, PublicationRole]] = {
        "cover": "cover",
        "inside_cover": "front_matter",
        "title": "front_matter",
        "body": "body",
        "colophon": "back_matter",
        "back_cover": "back_matter",
    }
    PAGE_DIRECTORY: ClassVar[str] = "pages"
    PAD: ClassVar[int] = 6
    MAX_ITEMS: ClassVar[int] = 5000

    @classmethod
    def publication_role(cls, page_role: str | None) -> PublicationRole:
        return cls.ROLE_MAP.get(str(page_role or "body"), "body")

    def plan(
        self,
        page_ids: Sequence[str],
        *,
        roles_by_id: Mapping[str, str] | None = None,
        spread_sides_by_id: Mapping[str, SpreadSide] | None = None,
        extension: str = "png",
        cover_page_id: str | None = None,
    ) -> tuple[PublicationItem, ...]:
        """``page_ids`` in logical (story) order, cover first when present."""
        if len(page_ids) > self.MAX_ITEMS:
            raise ValueError(f"too many pages for one package (> {self.MAX_ITEMS})")
        if len(set(page_ids)) != len(page_ids):
            raise ValueError("duplicate page ids")
        media_type = PublicationItem.media_type_for(extension)
        roles = dict(roles_by_id or {})
        sides = dict(spread_sides_by_id or {})
        ordered = list(page_ids)
        cover = cover_page_id or next(
            (pid for pid in ordered if self.publication_role(roles.get(pid)) == "cover"), None
        )
        if cover is not None and cover in ordered:
            ordered.remove(cover)
            ordered.insert(0, cover)
        items: list[PublicationItem] = []
        for index, page_id in enumerate(ordered):
            role = "cover" if page_id == cover else self.publication_role(roles.get(page_id))
            if role == "cover" and page_id != cover:
                role = "front_matter"  # a second "cover" page is never the cover
            items.append(
                PublicationItem(
                    source_id=page_id,
                    logical_index=index,
                    role=role,
                    spread_side=sides.get(page_id, "unspecified"),
                    media_type=media_type,
                    package_path=(
                        f"{self.PAGE_DIRECTORY}/{index + 1:0{self.PAD}d}.{extension.lower()}"
                    ),
                )
            )
        return tuple(items)
