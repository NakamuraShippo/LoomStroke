"""Application bootstrap coordinator for :mod:`loomstroke.app`."""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import logging.handlers
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

from PySide6 import QtCore
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from . import __version__

logger = logging.getLogger(__name__)


class ApplicationBootstrapper:
    """Own Qt app, event-loop, theme, and process-level hook setup."""

    def __init__(self, app: Any) -> None:
        object.__setattr__(self, "_app", app)

    @property
    def app(self) -> QApplication | None:
        return cast("QApplication | None", getattr(self._app, "app", None))

    @app.setter
    def app(self, value: QApplication | None) -> None:
        self._app.app = value

    @property
    def event_loop(self) -> QEventLoop | None:
        return getattr(self._app, "event_loop", None)

    @event_loop.setter
    def event_loop(self, value: QEventLoop | None) -> None:
        self._app.event_loop = value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._app, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._app, name, value)

    def _setup_crash_logging(self) -> None:
        """
        Set up crash logging to capture segmentation faults and Qt errors.

        This must be called BEFORE any Qt initialization to capture all crashes.
        """
        # Write crash logs to user_data/logs/ instead of world-readable temp dir
        try:
            from .platform.user_dirs import default_user_root  # noqa: PLC0415

            crash_log_dir = default_user_root() / "logs"
            crash_log_dir.mkdir(parents=True, exist_ok=True)
            log_path = str(crash_log_dir / "loomstroke_crash.log")
        except Exception:
            log_path = os.path.join(tempfile.gettempdir(), "loomstroke_crash.log")
        try:
            # Keep file handle open for the lifetime of the process;
            # faulthandler needs a writable fd at crash time.
            self._crash_log_file = open(log_path, "w")  # noqa: SIM115
            faulthandler.enable(self._crash_log_file)
            print(f"Crash logging enabled: {log_path}")
        except Exception as e:  # pragma: no cover - best effort
            print(f"WARNING: Could not enable crash logging: {e}")

        if os.environ.get("LOOMSTROKE_QT_DEBUG"):
            os.environ["QT_LOGGING_RULES"] = "*.debug=true;qt.qpa.*=true;qt.widgets.*=true"
        else:
            os.environ["QT_LOGGING_RULES"] = "*.warning=true"

    def _setup_logging(self) -> None:
        """Set up application logging with file persistence."""
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

        logging.basicConfig(
            level=logging.INFO,
            format=log_format,
        )

        # Q-2: Persist logs to user_data/logs/ for user support diagnostics
        try:
            from .platform.user_dirs import default_user_root  # noqa: PLC0415

            log_dir = default_user_root() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / "loomstroke.log",
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter(log_format))
            file_handler.setLevel(logging.INFO)
            logging.getLogger().addHandler(file_handler)
        except Exception:  # pragma: no cover - best effort
            print("WARNING: Could not set up file logging")

        logging.getLogger("qasync").setLevel(logging.WARNING)
        logging.getLogger("PySide6").setLevel(logging.WARNING)

        # Q-3: Install global exception hooks to prevent silent crashes
        self._install_exception_hooks()

    @staticmethod
    def _install_exception_hooks() -> None:
        """Install global exception hooks so unhandled errors are always logged."""
        _exc_logger = logging.getLogger("loomstroke.unhandled")

        _original_excepthook = sys.excepthook

        def _excepthook(exc_type, exc_value, exc_tb):  # type: ignore[no-untyped-def]  # sys.excepthook signature
            _exc_logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
            _original_excepthook(exc_type, exc_value, exc_tb)

        sys.excepthook = _excepthook

        def _threading_excepthook(args):  # type: ignore[no-untyped-def]  # threading.excepthook signature
            _exc_logger.critical(
                "Unhandled exception in thread %s",
                args.thread.name if args.thread else "<unknown>",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = _threading_excepthook

    def create_app(self) -> QApplication:
        """
        Create Qt application.

        Returns:
            Qt application instance
        """
        app = self.app
        if app is None:
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )

            app = QApplication(sys.argv)
            self.app = app
            app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
            app.setApplicationName("LoomStroke")
            app.setApplicationVersion(__version__)
            app.setOrganizationName("LoomStroke")
            icon_path = Path(__file__).resolve().parents[2] / "img" / "favicon.png"
            if icon_path.exists():
                app.setWindowIcon(QIcon(str(icon_path)))

            from .ui.theme import apply_loomstroke_theme  # noqa: PLC0415

            apply_loomstroke_theme(app, "dark")
            self._install_qt_message_handler()
            self._install_dock_animation_blocker()

            logger.info("Qt application created")

        return app

    def _install_qt_message_handler(self) -> None:
        """Install Qt message handler to route Qt messages to Python logging."""
        qtlog = logging.getLogger("qt")

        def qt_message_handler(mode: Any, context: Any, message: str) -> None:
            level_map = {
                QtCore.QtMsgType.QtDebugMsg: logging.DEBUG,
                QtCore.QtMsgType.QtInfoMsg: logging.INFO,
                QtCore.QtMsgType.QtWarningMsg: logging.WARNING,
                QtCore.QtMsgType.QtCriticalMsg: logging.ERROR,
                QtCore.QtMsgType.QtFatalMsg: logging.CRITICAL,
            }

            level = level_map.get(mode, logging.ERROR)
            location = f"{context.file}:{context.line}" if context.file else "unknown"
            qtlog.log(level, f"[QT {location}] {message}")

            if mode in (QtCore.QtMsgType.QtCriticalMsg, QtCore.QtMsgType.QtFatalMsg):
                crash_log_path = os.path.join(tempfile.gettempdir(), "loomstroke_crash.log")
                try:
                    with open(crash_log_path, "a") as f:
                        f.write(f"\n[QT CRITICAL {location}] {message}\n")
                except Exception:
                    logger.debug("crash log write failed", exc_info=True)

        QtCore.qInstallMessageHandler(qt_message_handler)
        logger.info("Qt message handler installed")

    def _install_dock_animation_blocker(self) -> None:
        """Install global dock animation blocker to prevent crashes."""
        try:
            from .ui.dock_animation_blocker import install_animation_blocker  # noqa: PLC0415

            self._animation_blocker = install_animation_blocker(self.app)
            logger.info("Dock animation blocker installed")
        except Exception as e:
            logger.warning(f"Could not install dock animation blocker: {e}")

    def create_event_loop(self) -> QEventLoop:
        """
        Create async event loop.

        Returns:
            qasync event loop
        """
        event_loop = self.event_loop
        if event_loop is None:
            event_loop = QEventLoop(cast(QApplication, self.app))
            self.event_loop = event_loop
            asyncio.set_event_loop(event_loop)
            logger.info("Async event loop created")
        else:
            asyncio.set_event_loop(event_loop)
        return event_loop

    def _apply_theme_setting(self) -> None:
        """Apply the persisted UI theme after settings are available."""
        if self.app is None or self.settings is None:
            return
        theme = self.settings.get("ui/theme", "dark")
        from .ui.theme import apply_loomstroke_theme  # noqa: PLC0415

        apply_loomstroke_theme(self.app, theme if isinstance(theme, str) else "dark")
