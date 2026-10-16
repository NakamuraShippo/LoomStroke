"""Text payloads -> font usage inventory (CT-25 F1 ``inventory_fonts``).

Reads the *requested* family / style from each text payload (style map
default first, then the legacy top-level keys), groups by request, counts
characters and resolves every request once against the catalog.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .font_catalog import FontCatalog
from .font_identity_service import FontIdentityService
from .font_request import FontRequest
from .font_usage import FontUsage
from .font_usage_inventory import FontUsageInventory

__all__ = ["FontInventoryService"]

TextEntry = tuple[str, str, str, Mapping[str, Any]]  # (page_id, layer_id, object_id, payload)


class FontInventoryService:
    def __init__(self, identity: FontIdentityService | None = None) -> None:
        self._identity = identity or FontIdentityService()

    @staticmethod
    def request_for_payload(payload: Mapping[str, Any]) -> FontRequest:
        style_map = payload.get("style_map")
        defaults: Mapping[str, Any] = {}
        if isinstance(style_map, Mapping):
            default = style_map.get("default")
            if isinstance(default, Mapping):
                defaults = default
        family = str(defaults.get("font_family", payload.get("font_family", "")) or "").strip()
        style = str(defaults.get("font_style", payload.get("font_style", "")) or "").strip()
        return FontRequest(family=family, style=style or None)

    @staticmethod
    def content_for_payload(payload: Mapping[str, Any]) -> str:
        document = payload.get("document")
        if isinstance(document, Mapping) and isinstance(document.get("text"), str):
            return str(document["text"])
        return str(payload.get("content", "") or "")

    def inventory(
        self, entries: Iterable[TextEntry], catalog: FontCatalog | None = None
    ) -> FontUsageInventory:
        grouped: dict[str, dict[str, Any]] = {}
        for page_id, layer_id, object_id, payload in entries:
            request = self.request_for_payload(payload)
            content = self.content_for_payload(payload)
            bucket = grouped.setdefault(
                request.key, {"request": request, "refs": [], "chars": 0, "set": set()}
            )
            bucket["refs"].append((page_id, layer_id, object_id))
            bucket["chars"] += len(content)
            bucket["set"].update(ch for ch in content if not ch.isspace())
        usages = tuple(
            FontUsage(
                request=bucket["request"],
                object_refs=tuple(bucket["refs"]),
                char_count=int(bucket["chars"]),
                characters=frozenset(bucket["set"]),
            )
            for bucket in grouped.values()
        )
        usages = tuple(sorted(usages, key=lambda u: (u.request.family.casefold(), u.request.key)))
        resolutions = {}
        if catalog is not None:
            for usage in usages:
                resolutions[usage.request.key] = self._identity.resolve(
                    usage.request, catalog, characters=usage.characters
                )
        return FontUsageInventory(usages=usages, resolutions=resolutions)
