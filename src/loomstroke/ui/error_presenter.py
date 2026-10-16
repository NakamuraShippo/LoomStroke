"""
Error presenter for actionable error handling.

Per Master.md A4 specifications, provides user-actionable error messages
with retry and reconnection options.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import cache
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorAction(Enum):
    """Possible user actions for errors."""

    RETRY = "retry"
    RECONNECT = "reconnect"
    IGNORE = "ignore"
    CANCEL = "cancel"
    SETTINGS = "settings"
    REPORT = "report"


@dataclass
class ErrorContext:
    """Context information for an error."""

    title: str
    message: str
    details: str | None = None
    severity: ErrorSeverity = ErrorSeverity.ERROR
    actions: list[ErrorAction] = field(default_factory=lambda: [ErrorAction.CANCEL])
    retry_callback: Callable[..., Any] | None = None
    reconnect_callback: Callable[..., Any] | None = None


class ErrorDialog(QDialog):
    """
    Custom error dialog with actionable buttons.

    Provides better UX than standard message boxes.
    """

    action_selected = Signal(ErrorAction)

    def __init__(self, context: ErrorContext, parent: Any | None = None):
        """
        Initialize error dialog.

        Args:
            context: Error context with details
            parent: Parent widget
        """
        super().__init__(parent)

        self.context = context
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        self.setWindowTitle(self.context.title)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Icon and message
        message_layout = QHBoxLayout()

        # Add icon based on severity
        icon_label = QLabel()
        icon_label.setText(self._icon_text())
        icon_style = self._get_icon_style()
        icon_label.setStyleSheet(icon_style)
        message_layout.addWidget(icon_label)

        # Message
        message_label = QLabel(self.context.message)
        message_label.setWordWrap(True)
        message_layout.addWidget(message_label, 1)

        layout.addLayout(message_layout)

        # Details section (if provided)
        if self.context.details:
            details_edit = QTextEdit()
            details_edit.setPlainText(self.context.details)
            details_edit.setReadOnly(True)
            details_edit.setMaximumHeight(150)
            layout.addWidget(details_edit)

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        for action in self.context.actions:
            button = self._create_action_button(action)
            button_layout.addWidget(button)

        layout.addLayout(button_layout)

    def _get_icon_style(self) -> str:
        """Get icon style based on severity."""
        colors = {
            ErrorSeverity.INFO: "#2E86AB",
            ErrorSeverity.WARNING: "#F2C037",
            ErrorSeverity.ERROR: "#C0392B",
            ErrorSeverity.CRITICAL: "#922B21",
        }
        color = colors.get(self.context.severity, "#2E2E2E")
        return f"font-size: 18px; font-weight: bold; padding: 8px; color: {color};"

    def _icon_text(self) -> str:
        """Return a text marker for the current severity."""
        markers = {
            ErrorSeverity.INFO: "[i]",
            ErrorSeverity.WARNING: "[!]",
            ErrorSeverity.ERROR: "[x]",
            ErrorSeverity.CRITICAL: "[!!]",
        }
        return markers.get(self.context.severity, "[!]")

    def _create_action_button(self, action: ErrorAction) -> QPushButton:
        """
        Create button for an action.

        Args:
            action: Error action type

        Returns:
            Configured button
        """
        labels = {
            ErrorAction.RETRY: "🔄 Retry",
            ErrorAction.RECONNECT: "🔌 Reconnect",
            ErrorAction.IGNORE: "Skip",
            ErrorAction.CANCEL: "Cancel",
            ErrorAction.SETTINGS: "⚙️ Settings",
            ErrorAction.REPORT: "📝 Report Issue",
        }

        button = QPushButton(labels.get(action, str(action)))

        # Style primary action
        if action == ErrorAction.RETRY:
            button.setObjectName("primary")

        # Connect to handler
        button.clicked.connect(lambda: self._on_action(action))

        return button

    @Slot()
    def _on_action(self, action: ErrorAction) -> None:
        """
        Handle action button click.

        Args:
            action: Selected action
        """
        self.action_selected.emit(action)

        # Execute callback if provided
        if action == ErrorAction.RETRY and self.context.retry_callback:
            self.context.retry_callback()
        elif action == ErrorAction.RECONNECT and self.context.reconnect_callback:
            self.context.reconnect_callback()

        self.accept()


class ErrorPresenter(QObject):
    """
    Central error presenter for the application.

    Provides consistent, actionable error handling.
    """

    # Signals
    error_occurred = Signal(ErrorContext)
    action_taken = Signal(ErrorAction, str)  # action, error_id

    def __init__(self, parent: QObject | None = None):
        """Initialize error presenter."""
        super().__init__(parent)

        from collections import deque  # noqa: PLC0415

        self.error_history: deque[ErrorContext] = deque(maxlen=100)
        self.suppressed_errors: set[str] = set()

    def present_error(
        self,
        title: str,
        message: str,
        details: str | None = None,
        severity: ErrorSeverity = ErrorSeverity.ERROR,
        actions: list[ErrorAction] | None = None,
        retry_callback: Callable[..., Any] | None = None,
        reconnect_callback: Callable[..., Any] | None = None,
        parent: Any | None = None,
    ) -> ErrorAction | None:
        """
        Present an error to the user.

        Args:
            title: Error title
            message: User-friendly message
            details: Technical details
            severity: Error severity
            actions: Available actions
            retry_callback: Callback for retry action
            reconnect_callback: Callback for reconnect action
            parent: Parent widget for dialog

        Returns:
            Selected action or None
        """
        # Create context
        context = ErrorContext(
            title=title,
            message=message,
            details=details,
            severity=severity,
            actions=actions or self._default_actions(severity),
            retry_callback=retry_callback,
            reconnect_callback=reconnect_callback,
        )

        # Check if suppressed
        error_id = f"{title}:{message}"
        if error_id in self.suppressed_errors:
            logger.debug(f"Suppressed error: {error_id}")
            return None

        # Add to history
        self.error_history.append(context)
        self.error_occurred.emit(context)

        # Show dialog
        dialog = ErrorDialog(context, parent)

        selected_action = None

        def on_action(action: ErrorAction) -> None:
            nonlocal selected_action
            selected_action = action
            self.action_taken.emit(action, error_id)

        dialog.action_selected.connect(on_action)
        dialog.exec()

        return selected_action

    def _default_actions(self, severity: ErrorSeverity) -> list[ErrorAction]:
        """
        Get default actions based on severity.

        Args:
            severity: Error severity

        Returns:
            List of appropriate actions
        """
        if severity == ErrorSeverity.INFO:
            return [ErrorAction.IGNORE]
        elif severity == ErrorSeverity.WARNING:
            return [ErrorAction.IGNORE, ErrorAction.CANCEL]
        elif severity == ErrorSeverity.ERROR:
            return [ErrorAction.RETRY, ErrorAction.CANCEL]
        else:  # CRITICAL
            return [ErrorAction.RETRY, ErrorAction.SETTINGS, ErrorAction.CANCEL]

    def present_connection_error(
        self, error: Exception, reconnect_callback: Callable[..., Any], parent: Any | None = None
    ) -> ErrorAction | None:
        """
        Present a connection error.

        Args:
            error: Connection exception
            reconnect_callback: Callback to reconnect
            parent: Parent widget

        Returns:
            Selected action
        """
        return self.present_error(
            title="Connection Error",
            message="Failed to connect to ComfyUI server.",
            details=str(error),
            severity=ErrorSeverity.ERROR,
            actions=[ErrorAction.RECONNECT, ErrorAction.SETTINGS, ErrorAction.CANCEL],
            reconnect_callback=reconnect_callback,
            parent=parent,
        )

    def present_generation_error(
        self, error: Exception, retry_callback: Callable[..., Any], parent: Any | None = None
    ) -> ErrorAction | None:
        """
        Present a generation error.

        Args:
            error: Generation exception
            retry_callback: Callback to retry
            parent: Parent widget

        Returns:
            Selected action
        """
        return self.present_error(
            title="Generation Failed",
            message="Failed to generate image.",
            details=str(error),
            severity=ErrorSeverity.WARNING,
            actions=[ErrorAction.RETRY, ErrorAction.IGNORE, ErrorAction.CANCEL],
            retry_callback=retry_callback,
            parent=parent,
        )

    def suppress_error(self, title: str, message: str) -> None:
        """
        Suppress future occurrences of an error.

        Args:
            title: Error title
            message: Error message
        """
        error_id = f"{title}:{message}"
        self.suppressed_errors.add(error_id)
        logger.info(f"Suppressed error: {error_id}")

    def clear_suppressed(self) -> None:
        """Clear all suppressed errors."""
        self.suppressed_errors.clear()
        logger.info("Cleared suppressed errors")

    def get_error_history(self) -> list[ErrorContext]:
        """
        Get error history.

        Returns:
            List of error contexts
        """
        return list(self.error_history)


@cache
def get_error_presenter() -> ErrorPresenter:
    """Get the shared error presenter instance."""
    return ErrorPresenter()
