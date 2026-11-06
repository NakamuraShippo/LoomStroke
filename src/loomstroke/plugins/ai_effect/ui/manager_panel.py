"""Dockable AI Effects Manager panel.

Hosts the preset manager and effect editor inside a single tabbed
QWidget so the AI Effect workflow behaves like the rest of the
LoomStroke panels.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QDockWidget, QTabWidget, QVBoxLayout, QWidget
from shiboken6 import isValid

from loomstroke.plugins.ai_effect.editor.import_preset import (
    ImportPresetError,
    load_preset_for_editing,
)
from loomstroke.plugins.ai_effect.ui.preset_manager_window import PresetManagerWindow
from loomstroke.ui.translations import translate_with_fallback

logger = logging.getLogger(__name__)


def _tr(text: str) -> str:
    return translate_with_fallback("AIEffectManagerPanel", text)


class AIEffectManagerPanel(QWidget):
    """Tabbed panel for preset browsing and preset authoring."""

    def __init__(
        self,
        *,
        library: Any,
        applier: Any,
        gateway_registry: Any,
        register_target_dir: Path | None = None,
        preset_exported_handler: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._applier = applier
        self._gateway_registry = gateway_registry
        self._register_target_dir = register_target_dir
        self._preset_exported_handler = preset_exported_handler
        self._editor: Any | None = None
        self._floating_dock: QDockWidget | None = None
        self._floating_sync_pending = False

        self.setObjectName("AIEffectManagerPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget(self)
        self._tabs.setObjectName("AIEffectManagerTabs")
        layout.addWidget(self._tabs)

        self._manager = PresetManagerWindow(
            library,
            applier,
            editor_opener=self.open_editor,
            editor_loader=self.open_editor_with_preset,
            parent=self,
        )
        self._tabs.addTab(self._manager, _tr("Manager"))
        from loomstroke.plugins.ai_effect.native_comfy.author import (  # noqa: PLC0415
            ComfyAuthorPanel,
        )

        self._comfy_author = ComfyAuthorPanel(self)
        self._tabs.addTab(self._comfy_author, "ComfyUI")

    def refresh(self) -> None:
        """Refresh the manager grid; used by the app export-sync hook."""
        self._manager.refresh()

    def show_manager(self) -> None:
        """Switch to the preset manager tab and refresh its grid."""
        self._tabs.setCurrentWidget(self._manager)
        self.refresh()

    def open_editor(self) -> Any | None:
        """Ensure the editor tab exists, then switch to it."""
        editor = self._ensure_editor()
        if editor is None:
            return None
        self._tabs.setCurrentWidget(editor)
        return editor

    def editor(self) -> Any | None:
        return self._editor

    def open_editor_with_preset(self, preset_id: str) -> None:
        """Load a user preset into the editor tab."""
        if self._library is None or self._gateway_registry is None:
            logger.warning(
                "AIEffectManagerPanel: cannot edit preset %s; subsystem unavailable",
                preset_id,
            )
            return
        try:
            preset = self._library.get(preset_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AIEffectManagerPanel: preset not found %s: %s", preset_id, exc)
            return
        lsfx_path = getattr(preset, "lsfx_path", None)
        if lsfx_path is None:
            logger.warning("AIEffectManagerPanel: preset %s has no lsfx_path", preset_id)
            return
        editor = self.open_editor()
        if editor is None:
            return
        try:
            state = load_preset_for_editing(lsfx_path, self._gateway_registry)
            editor.apply_imported_preset(state)
        except ImportPresetError as exc:
            logger.warning("AIEffectManagerPanel: import failed for %s: %s", preset_id, exc)
        except Exception:
            logger.exception("AIEffectManagerPanel: unexpected import failure for %s", preset_id)

    def _ensure_editor(self) -> Any | None:
        if self._editor is not None:
            return self._editor
        if self._gateway_registry is None:
            logger.warning("AIEffectManagerPanel: gateway registry unavailable")
            return None

        from loomstroke.plugins.ai_effect.editor.window import (  # noqa: PLC0415
            EffectEditorWindow,
        )

        editor = EffectEditorWindow(
            self._gateway_registry,
            parent=self,
            register_target_dir=self._register_target_dir,
        )
        workflow_button = getattr(editor, "_native_workflow_button", None)
        if workflow_button is not None:
            workflow_button.clicked.connect(self.open_comfy_workflow)
            workflow_button.setVisible(True)
        preset_exported = getattr(editor, "preset_exported", None)
        if (
            self._preset_exported_handler is not None
            and preset_exported is not None
            and hasattr(preset_exported, "connect")
        ):
            preset_exported.connect(self._preset_exported_handler)
        self._tabs.addTab(editor, _tr("Editor"))
        self._editor = editor
        return editor

    def open_comfy_workflow(self) -> None:
        """Load the Editor's current preset directly into the native workflow tab."""
        if self._comfy_author._busy:
            return
        self._tabs.setCurrentWidget(self._comfy_author)
        self._comfy_author.run(self._comfy_author.open_current())

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        self._track_floating_dock()
        self.refresh()
        super().showEvent(event)

    def _track_floating_dock(self) -> None:
        dock = self.parentWidget()
        if not isinstance(dock, QDockWidget) or dock is self._floating_dock:
            return
        if self._floating_dock is not None and isValid(self._floating_dock):
            self._floating_dock.removeEventFilter(self)
            self._floating_dock.topLevelChanged.disconnect(self._on_dock_top_level_changed)
        dock.installEventFilter(self)
        dock.topLevelChanged.connect(self._on_dock_top_level_changed)
        self._floating_dock = dock

    def _on_dock_top_level_changed(self, floating: bool) -> None:
        if not floating:
            QTimer.singleShot(0, self._raise_redocked_panel)

    def _raise_redocked_panel(self) -> None:
        dock = self._floating_dock
        if dock is not None and isValid(dock) and not dock.isFloating() and dock.widget() is self:
            dock.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self._floating_dock
            and event.type() == QEvent.Type.Resize
            and self._floating_dock.isFloating()
            and not self._floating_sync_pending
        ):
            self._floating_sync_pending = True
            QTimer.singleShot(0, self._sync_floating_geometry)
        return super().eventFilter(watched, event)

    def _sync_floating_geometry(self) -> None:
        self._floating_sync_pending = False
        dock = self._floating_dock
        if dock is None or not isValid(dock) or not dock.isFloating() or dock.widget() is not self:
            return
        target = dock.contentsRect()
        if self.geometry() != target:
            self.setGeometry(target)

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        self._comfy_author.dispose()
        super().closeEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt naming
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._refresh_translations()

    def _refresh_translations(self) -> None:
        self._tabs.setTabText(self._tabs.indexOf(self._manager), _tr("Manager"))
        if self._editor is not None:
            self._tabs.setTabText(self._tabs.indexOf(self._editor), _tr("Editor"))


__all__ = ["AIEffectManagerPanel"]
