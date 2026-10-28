"""Stable reason codes -> translated user text (CT-16 §6 / §12).

Issues carry only ``reason_code`` + ``message_args``; this presenter owns
the wording, split into *reason* and *recommended action* so the table can
show them separately and runtime language switches re-translate by
rebuilding from the codes.
"""

from __future__ import annotations

from typing import ClassVar

from ..core.preflight.preflight_issue import PreflightIssue
from ..core.preflight.preflight_profile import MangaPreflightProfile
from .translations import translate_with_fallback

__all__ = ["PreflightIssuePresenter"]


class PreflightIssuePresenter:
    """Translate codes for the preflight UI."""

    DOMAIN: ClassVar[str] = "MangaPreflightDialog"

    MESSAGES: ClassVar[dict[str, str]] = {
        "bleed_coverage_short": "Artwork reaches trim but not the bleed edge: {sides}.",
        "binding_danger_content": "Text or balloon overlaps the binding danger zone.",
        "no_pages": "The document has no pages.",
        "duplicate_page_id": "Duplicate page id.",
        "odd_page_count": "Odd page count ({count}); print books usually need an even count.",
        "page_too_small": "Page is {width} x {height} px, below the minimum of {minimum} px.",
        "page_size_mismatch": (
            "Page size {width} x {height} px differs from the document's "
            "{expected_width} x {expected_height} px."
        ),
        "dpi_below_minimum": "Resolution {dpi} dpi is below the minimum of {minimum} dpi.",
        "dpi_mismatch": "Pages use different resolutions ({values} dpi).",
        "empty_page": "The page has no visible content.",
        "page_content_not_inspected": "Page content could not be inspected.",
        "text_overflow": "Text does not fit its frame.",
        "text_layout_not_verified": "Text layout was not verified (vertical area text).",
        "empty_text": "Empty text object.",
        "font_size_below_minimum": "Font size {size} pt is below the minimum of {minimum} pt.",
        "font_missing": 'Font "{family}" is not installed.',
        "color_profile_missing": "The document has no working colour profile.",
        "bleed_unknown": "No physical layout: bleed and trim are unknown for this page.",
    }
    ACTIONS: ClassVar[dict[str, str]] = {
        "extend_to_bleed": "Extend artwork to the bleed edge",
        "move_from_binding": "Move the object away from the binding edge",
        "add_blank_page": "Add a blank page",
        "image_size": "Adjust with Image Size...",
        "enlarge_frame": "Enlarge the text frame",
        "reduce_font_size": "Reduce the font size",
        "delete_object": "Delete the object",
        "increase_font_size": "Increase the font size",
        "install_font": "Install the font",
        "replace_font": "Replace the font",
        "assign_color_profile": "Assign a colour profile in Image Properties",
        "set_physical_layout": "Create the page from a manga profile",
    }
    NOT_CHECKED: ClassVar[dict[str, str]] = {
        "bleed_bounds_unavailable": (
            "Some content bounds could not be verified for the bleed check."
        ),
        "binding_bounds_unavailable": (
            "Some text bounds could not be verified for the binding check."
        ),
        "linked_assets_not_available": "Linked assets are not available in this build.",
        "page_content_not_loaded": "Page content was not loaded.",
        "font_database_unavailable": "The font database is unavailable.",
        "profile_capability_missing": "The profile references an unknown rule.",
        "rule_error": "The rule failed to run.",
        "scan_cancelled": "The scan was cancelled.",
    }
    SEVERITIES: ClassVar[dict[str, str]] = {
        "fatal": "Fatal",
        "warning": "Warning",
        "info": "Info",
    }
    PROFILES: ClassVar[dict[str, str]] = {
        "loomstroke.digital": "Digital comic",
        "loomstroke.webtoon": "Webtoon",
        "loomstroke.print_mono": "Print (monochrome)",
    }

    def _tr(self, text: str) -> str:
        return translate_with_fallback(self.DOMAIN, text)

    def message(self, issue: PreflightIssue) -> str:
        template = self.MESSAGES.get(issue.reason_code)
        if template is None:
            return issue.reason_code
        try:
            args = issue.args()
            if issue.reason_code == "bleed_coverage_short":
                args["sides"] = ", ".join(
                    self._tr(side.title()) for side in str(args.get("sides", "")).split(",")
                )
            return self._tr(template).format(**args)
        except (KeyError, IndexError, ValueError):
            return self._tr(template)

    def actions(self, issue: PreflightIssue) -> str:
        return ", ".join(
            self._tr(self.ACTIONS.get(code, code)) for code in issue.recommended_actions
        )

    def severity_label(self, severity: str) -> str:
        return self._tr(self.SEVERITIES.get(severity, severity))

    def rule_label(self, rule_id: str) -> str:
        return rule_id

    def not_checked_label(self, rule_id: str, reason_code: str) -> str:
        return f"{rule_id}: {self._tr(self.NOT_CHECKED.get(reason_code, reason_code))}"

    def profile_label(self, profile: MangaPreflightProfile) -> str:
        return self._tr(self.PROFILES.get(profile.profile_id, profile.profile_id))
