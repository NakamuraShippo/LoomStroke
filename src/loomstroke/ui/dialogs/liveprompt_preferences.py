"""Interface preference for the separator used by new LivePrompts."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLineEdit

from loomstroke.plugins.common.liveprompt_defaults import DEFAULT_SEPARATOR_KEY, default_separator


class LivePromptDefaultsControl:
    LABEL = "LivePrompt default separator"
    TOOLTIP = (
        r"Use \n for a new line or \t for a tab. Empty uses a new line. "
        "Applies to new LivePrompts; saved separators take precedence."
    )

    def __init__(self, page: Any) -> None:
        self.page = page
        self.separator_edit = QLineEdit(page)
        self.separator_edit.setText(default_separator(page.settings))
        self.separator_edit.setPlaceholderText(r"\n")
        page._translation_binder.register(self.separator_edit.setToolTip, page._ts(self.TOOLTIP))

    def add_to(self, form: Any) -> None:
        self.page._add_labeled_row(form, self.page._ts(self.LABEL), self.separator_edit)

    def collect_values(self) -> dict[str, Any]:
        return {DEFAULT_SEPARATOR_KEY: self.separator_edit.text()}
