"""Plan and apply font substitutions to text payloads (CT-25 F2).

The payload keeps the original request under ``font_substitution``
(``requested_family`` / ``requested_style``) so a later "restore" can
offer the original font once it is available again; the effective
family / style go where the renderer reads them (style map default and
legacy top-level keys).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, ClassVar

from .font_request import FontRequest
from .font_substitution_plan import FontSubstitutionPlan
from .font_usage import ObjectRef
from .font_usage_inventory import FontUsageInventory

__all__ = ["FontSubstitutionPlanner"]


class FontSubstitutionPlanner:
    MARKER: ClassVar[str] = "font_substitution"

    def plan(
        self,
        inventory: FontUsageInventory,
        *,
        source: FontRequest,
        target: FontRequest,
        scope: str = "document",
        page_id: str | None = None,
        object_refs: Iterable[ObjectRef] | None = None,
    ) -> FontSubstitutionPlan:
        usage = inventory.usage_for_family(source.family, source.style)
        refs: tuple[ObjectRef, ...] = usage.object_refs if usage else ()
        if scope == "page":
            refs = tuple(ref for ref in refs if page_id is not None and ref[0] == page_id)
        elif scope == "selection":
            wanted = set(object_refs or ())
            refs = tuple(ref for ref in refs if ref in wanted)
        elif scope != "document":
            raise ValueError(f"unknown substitution scope: {scope}")
        return FontSubstitutionPlan(source=source, target=target, scope=scope, object_refs=refs)  # type: ignore[arg-type]

    def apply_to_payload(
        self, payload: Mapping[str, Any], plan: FontSubstitutionPlan
    ) -> dict[str, Any]:
        """Return a new payload with the target font applied (original kept)."""
        updated: dict[str, Any] = dict(payload)
        style_map = (
            dict(payload.get("style_map") or {})
            if isinstance(payload.get("style_map"), Mapping)
            else None
        )
        if style_map is not None:
            default = (
                dict(style_map.get("default") or {})
                if isinstance(style_map.get("default"), Mapping)
                else {}
            )
            default["font_family"] = plan.target.family
            if plan.target.style is not None:
                default["font_style"] = plan.target.style
            style_map["default"] = default
            updated["style_map"] = style_map
        updated["font_family"] = plan.target.family
        if plan.target.style is not None:
            updated["font_style"] = plan.target.style
        if plan.keep_original_request:
            existing = payload.get(self.MARKER)
            original = (
                existing
                if isinstance(existing, Mapping) and existing.get("requested_family")
                else None
            )
            updated[self.MARKER] = {
                "requested_family": (original or {}).get("requested_family", plan.source.family),
                "requested_style": (original or {}).get("requested_style", plan.source.style or ""),
                "applied_family": plan.target.family,
                "applied_style": plan.target.style or "",
            }
        return updated

    def restore_payload(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Undo a recorded substitution (``None`` when nothing was recorded)."""
        marker = payload.get(self.MARKER)
        if not isinstance(marker, Mapping) or not marker.get("requested_family"):
            return None
        original = FontRequest(
            family=str(marker.get("requested_family", "")),
            style=str(marker.get("requested_style", "")) or None,
        )
        restored = self.apply_to_payload(
            payload,
            FontSubstitutionPlan(
                source=original,
                target=original,
                scope="selection",
                object_refs=(),
                keep_original_request=False,
            ),
        )
        restored.pop(self.MARKER, None)
        return restored
