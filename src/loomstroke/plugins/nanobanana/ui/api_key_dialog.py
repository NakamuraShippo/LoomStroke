"""Modal dialog for setting / clearing the NanoBanana API key.

Phase 8a "read-on-demand" policy in action:
- The QLineEdit is always password-mode; no reveal toggle.
- The dialog never shows the existing key — opening it for an
  already-configured account starts with an empty field.
- On [Save] the value is written through ``SecretProvider.set``
  (which routes to the OS keyring), the QLineEdit is cleared, the
  local binding is ``del``-ed, and the dialog closes. The key never
  lives on ``self.*`` of the dialog.
- On [Cancel] the field is cleared first so the typed-but-unsaved
  value is dropped before the dialog goes away.
- "Get your API key" routes the user to
  ``https://aistudio.google.com/app/api-keys`` via
  ``QDesktopServices`` — purely a browser hand-off, no keyring read.

Designed to be reusable across future paid Gateways (OpenAI,
Stability, etc.) by parameterising ``service_name`` /
``display_name`` / ``help_url``.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.config.secret_provider import SecretProvider

logger = logging.getLogger(__name__)


def _secrets() -> SecretProvider:
    """Build a fresh ``SecretProvider`` for credential I/O.

    The dialog is short-lived and has no DI plumbing, so it builds a
    local provider on each call. The provider holds no secret state
    on its instance attributes, so the throw-away pattern is safe.
    """
    return SecretProvider()


NANOBANANA_API_KEY_URL = "https://aistudio.google.com/app/api-keys"


class APIKeyDialog(QDialog):
    """Modal "Set API key" dialog for paid AI Gateways.

    Closes with ``Accepted`` after a successful save and ``Rejected``
    on cancel / save failure. Callers should re-query
    ``SecretProvider.has`` after ``exec()`` to refresh
    their "Configured ✓" indicator.
    """

    def __init__(
        self,
        *,
        service_name: str,
        display_name: str,
        help_url: str,
        username: str = "api",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service_name = service_name
        self._username = username
        self._help_url = help_url
        # NEVER store the key on ``self``. The QLineEdit's text is
        # the only carrier and gets cleared on save / cancel.
        self.setWindowTitle(self.tr("Set {name} API key").format(name=display_name))
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel(
            self.tr(
                "{name} uses Google Gemini's API. The key is stored encrypted "
                "in your OS keyring and is read only when you press Generate."
            ).format(name=display_name),
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        help_btn = QPushButton(self.tr("Get your API key…"), self)
        help_btn.setObjectName("apiKeyHelpButton")
        help_btn.clicked.connect(self._open_help_url)
        layout.addWidget(help_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        # Password-mode field. No reveal toggle — by design.
        # Always starts empty even if a key is already saved (the
        # caller must trust the "Configured ✓" indicator + this
        # dialog's purpose, NOT round-trip the existing value).
        field_label = QLabel(self.tr("API key:"), self)
        layout.addWidget(field_label)
        self._key_edit = QLineEdit(self)
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setPlaceholderText(self.tr("Paste your API key"))
        # Defensive: disable clipboard "auto-paste from password manager"
        # browser autofill is not relevant for QLineEdit but the
        # placeholder text + password echo cover the common case.
        layout.addWidget(self._key_edit)

        notice = QLabel(
            self.tr(
                "After saving, the key is no longer kept in this dialog. "
                "To replace it later, enter a new value here. To remove it "
                "entirely, click Clear in Preferences."
            ),
            self,
        )
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(notice)

        button_row = QDialogButtonBox(self)
        save_btn = button_row.addButton(self.tr("Save"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = button_row.addButton(QDialogButtonBox.StandardButton.Cancel)
        save_btn.clicked.connect(self._on_save_clicked)
        cancel_btn.clicked.connect(self._on_cancel_clicked)
        layout.addWidget(button_row)

    def _open_help_url(self) -> None:
        """Open the provider's API key page in the default browser."""
        try:
            QDesktopServices.openUrl(QUrl(self._help_url))
        except Exception:
            logger.warning("Failed to open API key help URL %s", self._help_url, exc_info=True)

    def _on_save_clicked(self) -> None:
        """Persist the key to the OS keyring and close the dialog.

        Empty input → warning, dialog stays open. Successful save →
        clear field + ``del`` local + accept. Failed save → warning,
        keep dialog open so the user can retry.
        """
        key = self._key_edit.text().strip()
        if not key:
            QMessageBox.warning(
                self,
                self.tr("API key required"),
                self.tr("Please enter your API key before saving."),
            )
            return
        try:
            success = bool(_secrets().set(self._service_name, key, username=self._username))
        except Exception as exc:
            logger.error("Failed to save API key: %s", exc, exc_info=True)
            success = False
        # Drop the local plaintext NOW regardless of outcome — even
        # the failure path must not leave the value in scope longer
        # than the function body.
        self._key_edit.clear()
        del key
        if not success:
            QMessageBox.warning(
                self,
                self.tr("Could not save API key"),
                self.tr(
                    "The API key could not be saved to your keyring. Check the "
                    "log for details and try again."
                ),
            )
            return
        self.accept()

    def _on_cancel_clicked(self) -> None:
        """Discard typed value before closing.

        Without this the QLineEdit's internal buffer would still hold
        the typed text until widget destruction. Clearing first
        shortens the window in which the unsaved value lives in
        memory.
        """
        self._key_edit.clear()
        self.reject()

    def reject(self) -> None:  # type: ignore[override]
        """Override Esc / window-close to also clear the field first."""
        self._key_edit.clear()
        super().reject()


def show_set_api_key_dialog(
    *,
    service_name: str,
    display_name: str,
    help_url: str,
    username: str = "api",
    parent: QWidget | None = None,
) -> bool:
    """Convenience wrapper: build + show + return saved-or-not.

    Tests can swap this for a stub; production code (Preferences /
    AUTH-error toast action) calls it directly.
    """
    dialog = APIKeyDialog(
        service_name=service_name,
        display_name=display_name,
        help_url=help_url,
        username=username,
        parent=parent,
    )
    return dialog.exec() == QDialog.DialogCode.Accepted


def show_set_nanobanana_api_key(parent: QWidget | None = None) -> bool:
    """Phase 1 helper: open the dialog pre-configured for NanoBanana."""
    return show_set_api_key_dialog(
        service_name="nanobanana",
        display_name="NanoBanana",
        help_url=NANOBANANA_API_KEY_URL,
        parent=parent,
    )


def confirm_and_clear_api_key(
    *,
    service_name: str,
    display_name: str,
    username: str = "api",
    parent: QWidget | None = None,
) -> bool:
    """Confirm and delete a stored API key.

    Returns True iff the user confirmed AND the deletion succeeded.
    Callers should re-query ``SecretProvider.has`` to refresh UI state.
    """
    reply = QMessageBox.question(
        parent,
        QMessageBox.tr("Clear {name} API key?").format(name=display_name),
        QMessageBox.tr(
            "Remove the saved {name} API key from your keyring?\n\n"
            "You will need to enter it again before generating."
        ).format(name=display_name),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return False
    try:
        cleared = bool(_secrets().clear(service_name, username=username))
    except Exception:
        logger.error("Failed to clear API key", exc_info=True)
        return False
    return cleared


__all__ = [
    "NANOBANANA_API_KEY_URL",
    "APIKeyDialog",
    "confirm_and_clear_api_key",
    "show_set_api_key_dialog",
    "show_set_nanobanana_api_key",
]
