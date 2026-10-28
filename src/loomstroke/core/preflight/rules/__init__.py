"""Built-in preflight rules (CT-16 §5) — one rule per module."""

from .binding_danger_rule import BindingDangerRule
from .bleed_coverage_rule import BleedCoverageRule
from .bleed_rule import BleedRule
from .color_profile_rule import ColorProfileRule
from .dpi_rule import DpiRule
from .empty_page_rule import EmptyPageRule
from .empty_text_rule import EmptyTextRule
from .linked_resource_rule import LinkedResourceRule
from .min_font_size_rule import MinFontSizeRule
from .missing_font_rule import MissingFontRule
from .page_count_rule import PageCountRule
from .page_size_rule import PageSizeRule
from .text_overflow_rule import TextOverflowRule

BUILTIN_RULES = (
    BleedCoverageRule,
    BindingDangerRule,
    PageCountRule,
    PageSizeRule,
    DpiRule,
    EmptyPageRule,
    TextOverflowRule,
    EmptyTextRule,
    MinFontSizeRule,
    MissingFontRule,
    ColorProfileRule,
    BleedRule,
    LinkedResourceRule,
)

__all__ = [
    "BUILTIN_RULES",
    "BindingDangerRule",
    "BleedCoverageRule",
    "BleedRule",
    "ColorProfileRule",
    "DpiRule",
    "EmptyPageRule",
    "EmptyTextRule",
    "LinkedResourceRule",
    "MinFontSizeRule",
    "MissingFontRule",
    "PageCountRule",
    "PageSizeRule",
    "TextOverflowRule",
]
