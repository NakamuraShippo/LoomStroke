"""The optional MCP confirmation control, independent of the page's HTTP UI."""

from PySide6.QtWidgets import QCheckBox

from loomstroke.platform.settings_schema import (
    MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW,
    MCP_SKIP_HARD_CONFIRMATIONS_KEY,
)


class McpHardConfirmationControl:
    LABEL = "Skip additional confirmation dialogs"
    TOOLTIP = (
        "Only active when Agent Approval is set to Allow all MCP agent actions. "
        "File roots, external-service permissions, and connection authentication still apply."
    )

    def __init__(self, page):
        self.page = page
        self.checkbox = QCheckBox(page)
        page._translation_binder.register(self.checkbox.setText, page._ts(self.LABEL))
        page._translation_binder.register(self.checkbox.setToolTip, page._ts(self.TOOLTIP))
        page.confirmation_policy_combo.currentIndexChanged.connect(self.sync_enabled)

    def load(self):
        self.checkbox.setChecked(
            self.page.settings.get_bool(MCP_SKIP_HARD_CONFIRMATIONS_KEY, False)
        )
        self.sync_enabled()

    def sync_enabled(self, _index=None):
        self.checkbox.setEnabled(
            self.page.confirmation_policy_combo.currentData()
            == MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW
        )

    def collect_values(self):
        return {MCP_SKIP_HARD_CONFIRMATIONS_KEY: self.checkbox.isChecked()}
