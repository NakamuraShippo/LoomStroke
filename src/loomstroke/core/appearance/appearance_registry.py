"""Built-in appearance kinds (CT-21 §5): fills, layer styles, and adjustments (from effects)."""

from __future__ import annotations

from typing import Any, ClassVar

from .appearance_issue import AppearanceValidationIssue
from .appearance_kind import AppearanceFamily, AppearanceKind
from .appearance_param import AppearanceParam

__all__ = ["AppearanceRegistry"]

_BLACK = [0, 0, 0, 255]
_WHITE = [255, 255, 255, 255]


def _fill_kinds() -> tuple[AppearanceKind, ...]:
    return (
        AppearanceKind(
            "solid",
            "fill",
            "Solid Color",
            (
                AppearanceParam("color", "color", _BLACK, "Color"),
                AppearanceParam("opacity", "float", 1.0, "Opacity", 0.0, 1.0, step=0.05),
            ),
        ),
        AppearanceKind(
            "gradient",
            "fill",
            "Gradient",
            (
                AppearanceParam("shape", "enum", "linear", "Shape", choices=("linear", "radial")),
                AppearanceParam("color_start", "color", _BLACK, "Start color"),
                AppearanceParam("color_end", "color", _WHITE, "End color"),
                AppearanceParam("angle", "float", 0.0, "Angle", -360.0, 360.0, step=5.0),
                AppearanceParam("center", "point", [0.5, 0.5], "Center (0-1)"),
                AppearanceParam("opacity", "float", 1.0, "Opacity", 0.0, 1.0, step=0.05),
            ),
        ),
        AppearanceKind(
            "pattern",
            "fill",
            "Pattern",
            (
                AppearanceParam(
                    "pattern", "enum", "checker", "Pattern", choices=("checker", "stripes", "dots")
                ),
                AppearanceParam("size", "float", 16.0, "Size (px)", 1.0, 1024.0, step=1.0),
                AppearanceParam("angle", "float", 0.0, "Angle", -360.0, 360.0, step=5.0),
                AppearanceParam("foreground", "color", _BLACK, "Foreground"),
                AppearanceParam("background", "color", [255, 255, 255, 0], "Background"),
                AppearanceParam("opacity", "float", 1.0, "Opacity", 0.0, 1.0, step=0.05),
            ),
        ),
    )


def _style_kinds() -> tuple[AppearanceKind, ...]:
    return (
        AppearanceKind(
            "drop_shadow",
            "style",
            "Drop Shadow",
            (
                AppearanceParam("offset_x", "float", 4.0, "Offset X", -256.0, 256.0, step=1.0),
                AppearanceParam("offset_y", "float", 4.0, "Offset Y", -256.0, 256.0, step=1.0),
                AppearanceParam("blur", "int", 4, "Blur", 0, 64, step=1),
                AppearanceParam("color", "color", _BLACK, "Color"),
                AppearanceParam("opacity", "float", 0.6, "Opacity", 0.0, 1.0, step=0.05),
            ),
        ),
        AppearanceKind(
            "outer_glow",
            "style",
            "Outer Glow",
            (
                AppearanceParam("size", "int", 6, "Size", 1, 64, step=1),
                AppearanceParam("color", "color", [255, 255, 160, 255], "Color"),
                AppearanceParam("opacity", "float", 0.75, "Opacity", 0.0, 1.0, step=0.05),
            ),
        ),
        AppearanceKind(
            "color_overlay",
            "style",
            "Color Overlay",
            (
                AppearanceParam("color", "color", [255, 0, 0, 255], "Color"),
                AppearanceParam("opacity", "float", 1.0, "Opacity", 0.0, 1.0, step=0.05),
            ),
        ),
        AppearanceKind(
            "stroke",
            "style",
            "Stroke",
            (
                AppearanceParam("size", "int", 2, "Size", 1, 64, step=1),
                AppearanceParam("color", "color", _BLACK, "Color"),
                AppearanceParam("opacity", "float", 1.0, "Opacity", 0.0, 1.0, step=0.05),
            ),
        ),
    )


class AppearanceRegistry:
    STYLE_RENDER_ORDER: ClassVar[tuple[str, ...]] = (
        "drop_shadow",
        "outer_glow",
        "color_overlay",
        "stroke",
    )
    """Fixed composite order (below the source ... above the source), CT-21 §5.3."""

    def __init__(self, *, include_adjustments: bool = True) -> None:
        self._kinds: dict[tuple[str, str], AppearanceKind] = {}
        for kind in (*_fill_kinds(), *_style_kinds()):
            self._kinds[(kind.family, kind.kind_id)] = kind
        if include_adjustments:
            for kind in self._adjustment_kinds():
                self._kinds[("adjustment", kind.kind_id)] = kind

    # -- queries -----------------------------------------------------------------------

    def get(self, family: AppearanceFamily, kind_id: str) -> AppearanceKind | None:
        return self._kinds.get((family, kind_id))

    def kinds(self, family: AppearanceFamily) -> tuple[AppearanceKind, ...]:
        return tuple(kind for (fam, _), kind in self._kinds.items() if fam == family)

    def validate(
        self, family: AppearanceFamily, kind_id: str, params: Any
    ) -> tuple[dict[str, Any], tuple[AppearanceValidationIssue, ...]]:
        kind = self.get(family, kind_id)
        if kind is None:
            return dict(params or {}), (AppearanceValidationIssue("kind_unknown", "kind", "error"),)
        return kind.validate(params)

    def defaults(self, family: AppearanceFamily, kind_id: str) -> dict[str, Any]:
        kind = self.get(family, kind_id)
        return kind.defaults() if kind is not None else {}

    # -- adjustments from the live-filter registry -------------------------------------

    @staticmethod
    def _adjustment_kinds() -> tuple[AppearanceKind, ...]:
        try:
            from ..filters import FilterRegistry  # noqa: PLC0415
        except Exception:  # pragma: no cover - headless builds without Qt effects
            return ()
        kinds: list[AppearanceKind] = []
        for type_id, name, category in FilterRegistry.list_filters():
            if category != "adjustment":
                continue
            filter_cls = FilterRegistry.get(type_id)
            params: list[AppearanceParam] = []
            if filter_cls is not None:
                try:
                    schemas = filter_cls().param_schema()
                except Exception:  # pragma: no cover - defensive against plugin effects
                    schemas = []
                for schema in schemas:
                    params.append(_param_from_filter_schema(schema))
            kinds.append(AppearanceKind(type_id, "adjustment", name, tuple(params), category))
        return tuple(kinds)


def _param_from_filter_schema(schema: Any) -> AppearanceParam:
    kind = str(getattr(schema, "param_type", "float") or "float")
    if kind not in ("float", "int", "bool", "enum"):
        kind = "float"
    choices = tuple(getattr(schema, "choices", None) or ())
    return AppearanceParam(
        key=str(schema.key),
        kind=kind,  # type: ignore[arg-type]
        default=schema.default,
        label=str(getattr(schema, "label", "") or schema.key),
        minimum=getattr(schema, "min_val", None),
        maximum=getattr(schema, "max_val", None),
        choices=choices,
        step=getattr(schema, "step", None),
    )


_param_from_effect_schema = _param_from_filter_schema
