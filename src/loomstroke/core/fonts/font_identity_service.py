"""Deterministic request -> resolution (CT-25 ``FontIdentityService``).

Only observes the catalog; never rewrites the request.  An empty family
means "default font" and is always available.
"""

from __future__ import annotations

from collections.abc import Iterable

from .font_catalog import FontCatalog
from .font_identity import FontIdentity
from .font_request import FontRequest
from .font_resolution_result import FontResolutionResult

__all__ = ["FontIdentityService"]


class FontIdentityService:
    def resolve(
        self,
        request: FontRequest,
        catalog: FontCatalog,
        *,
        characters: Iterable[str] = (),
    ) -> FontResolutionResult:
        family = request.family.strip()
        if not family:
            return FontResolutionResult(request, "exact", resolved_family="", resolved_style="")
        if not catalog.has_family(family):
            return FontResolutionResult(request, "fallback")
        styles = [str(style) for style in catalog.styles(family)]
        wanted = (request.style or "").strip()
        exact_style = next((s for s in styles if s.casefold() == wanted.casefold()), None)
        status = "exact" if (not wanted or exact_style) else "family_only"
        resolved_style = exact_style or (styles[0] if styles else "")
        missing = catalog.missing_glyphs(family, characters) if characters else 0
        return FontResolutionResult(
            request,
            status,  # type: ignore[arg-type]
            resolved_family=family,
            resolved_style=resolved_style,
            identity=FontIdentity(
                postscript_name=None,
                file_fingerprint=None,
                family=family,
                style=resolved_style,
                source_kind="system",
            ),
            missing_glyphs=int(missing),
        )
