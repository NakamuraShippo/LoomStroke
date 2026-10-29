"""Application-wide preferences dialog."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QT_TRANSLATE_NOOP, QEvent, Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...config.secret_provider import SecretProvider
from ...platform.settings_manager import SettingsManager
from ...platform.settings_schema import (
    AI_SELECT_ASSETS_MAX_CACHE_MB_KEY,
    DEFAULT_AI_SELECT_ASSETS_MAX_CACHE_MB,
    DEFAULT_MCP_LIVE_HTTP_PORT,
    MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW,
    MCP_CONFIRMATION_POLICY_ALWAYS_ASK,
    MCP_CONFIRMATION_POLICY_EXTERNAL_ONLY,
    MCP_CONFIRMATION_POLICY_KEY,
    MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY,
    MCP_LIVE_HTTP_ENABLED_KEY,
    MCP_LIVE_HTTP_PORT_KEY,
    MCP_LIVE_HTTP_ROOTS_KEY,
    MCP_LIVE_HTTP_START_ON_LAUNCH_KEY,
)
from ...platform.shortcut_manager import ShortcutAction, ShortcutManager
from ...platform.user_dirs import default_layout_directory
from ...plugins.gptimage2.key_settings import (
    GPTIMAGE2_API_KEY_SERVICE,
    GPTIMAGE2_API_KEY_USERNAME,
    GPTIMAGE2_USE_OPENAI_API_KEY_SETTING,
    OPENAI_API_KEY_SERVICE,
    OPENAI_API_KEY_USERNAME,
)
from ..i18n import available_language_options
from ..menu_catalog import MENU_DEFINITIONS

if TYPE_CHECKING:
    from ..menu_catalog import MenuDefinition

from ...platform.launch_options import AI_PREFERENCES_CATEGORIES, ai_features_enabled
from ..translations import TranslationBinder, translate_with_fallback
from .preferences_shortcuts import (
    MouseBindingHelper,
    ShortcutConflictDetector,
    ShortcutFilterController,
    ShortcutTableModel,
)


class PreferencesDialog(QDialog):
    """Tabbed preferences dialog grouping core configuration areas."""

    categories: tuple[tuple[str, str], ...] = (
        (
            cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "Performance")),
            cast(
                str,
                QT_TRANSLATE_NOOP(
                    "PreferencesDialog",
                    "Tune render and hardware options",
                ),
            ),
        ),
        (
            cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "Interface")),
            cast(
                str,
                QT_TRANSLATE_NOOP(
                    "PreferencesDialog",
                    "UI appearance and layout",
                ),
            ),
        ),
        (
            cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "File I/O")),
            cast(
                str,
                QT_TRANSLATE_NOOP(
                    "PreferencesDialog",
                    "Autosave and formats",
                ),
            ),
        ),
        (
            cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "LoomStroke MCP")),
            cast(
                str,
                QT_TRANSLATE_NOOP(
                    "PreferencesDialog",
                    "Local MCP server, origins and file roots",
                ),
            ),
        ),
        (
            cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "SAM 3.1")),
            cast(
                str,
                QT_TRANSLATE_NOOP(
                    "PreferencesDialog",
                    "SAM 3.1 runtime and one-click setup for AI Select",
                ),
            ),
        ),
        (
            cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "Shortcuts")),
            cast(
                str,
                QT_TRANSLATE_NOOP(
                    "PreferencesDialog",
                    "Keyboard shortcut mapping",
                ),
            ),
        ),
        (
            cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "AI Effect")),
            cast(
                str,
                QT_TRANSLATE_NOOP(
                    "PreferencesDialog",
                    "Defaults, cache and retry for AI Effect layers",
                ),
            ),
        ),
    )

    def __init__(
        self,
        settings: SettingsManager,
        apply_callback: Callable[[dict[str, Any]], None],
        shortcut_manager: ShortcutManager,
        parent: QWidget | None = None,
        mcp_endpoint_info_provider: Callable[[], Any] | None = None,
        mcp_session_token_refresh_callback: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._apply_callback = apply_callback
        self._shortcut_manager = shortcut_manager
        self._mcp_endpoint_info_provider = mcp_endpoint_info_provider
        self._mcp_session_token_refresh_callback = mcp_session_token_refresh_callback
        self._pages: list[PreferencesPage] = []
        self._page_map: dict[str, int] = {}
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._buttons: QDialogButtonBox | None = None
        if not ai_features_enabled():
            self.categories = tuple(
                category
                for category in self.categories
                if category[0] not in AI_PREFERENCES_CATEGORIES
            )

        self._translation_binder.register(self.setWindowTitle, self._ts("Preferences"))
        self.resize(960, 520)
        self._build_ui()

    def _ts(self, text: str) -> str:
        """Mark a literal for translation while keeping its source form."""

        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(self._translate_runtime, text)

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("PreferencesDialog", text)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        content = QHBoxLayout()
        self._category_list = QListWidget()
        self._category_list.setFixedWidth(180)
        self._stack = QStackedWidget()

        content.addWidget(self._category_list)
        content.addWidget(self._stack, 1)
        layout.addLayout(content)

        for idx, (title, subtitle) in enumerate(self.categories):
            page = self._create_page(title)
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, subtitle)
            self._translation_binder.register(item.setText, title)
            self._category_list.addItem(item)
            self._stack.addWidget(self._make_scroll_page(page))
            self._pages.append(page)
            self._page_map[title] = idx

        self._category_list.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._category_list.setCurrentRow(0)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        self._buttons = buttons
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            self._translation_binder.register(ok_button.setText, self._ts("OK"))
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            self._translation_binder.register(cancel_button.setText, self._ts("Cancel"))
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button:
            self._translation_binder.register(apply_button.setText, self._ts("Apply"))
            apply_button.clicked.connect(self._apply_changes)
        layout.addWidget(buttons)

    def _refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        for page in self._pages:
            page.refresh_translations()

    def _make_scroll_page(self, page: PreferencesPage) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(page)
        return scroll

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._refresh_translations()

    def _create_page(self, title: str) -> PreferencesPage:
        page_factories: dict[str, Callable[[], PreferencesPage]] = {
            "Performance": lambda: PerformancePage(self._settings),
            "Interface": lambda: InterfacePage(self._settings),
            "File I/O": lambda: FileIOPage(self._settings),
            "LoomStroke MCP": lambda: LoomStrokeMcpPage(
                self._settings,
                endpoint_info_provider=self._mcp_endpoint_info_provider,
                session_token_refresh_callback=self._mcp_session_token_refresh_callback,
            ),
            "SAM 3.1": lambda: Sam3RuntimePage(self._settings),
            "Shortcuts": lambda: ShortcutsPage(self._settings, self._shortcut_manager),
            "AI Effect": lambda: AIEffectPage(self._settings),
        }
        page_factory = page_factories.get(title)
        if page_factory is None:
            raise ValueError(f"Unknown preferences category: {title}")
        return page_factory()

    # ------------------------------------------------------------------
    def _apply_changes(self) -> None:
        changes: dict[str, Any] = {}
        for page in self._pages:
            changes.update(page.collect_values())
            # Pages that own their own persistence (e.g. AIEffectPage writes
            # its own JSON file instead of going through SettingsManager)
            # override ``save_settings`` to perform the write here. Default
            # base-class implementation is a no-op.
            page.save_settings()
        if not changes:
            return
        self._apply_callback(changes)

    def _on_accept(self) -> None:
        self._apply_changes()
        self.accept()

    def show_shortcuts_page(self, action_id: str | None = None) -> None:
        """Switch to the Shortcuts tab and optionally focus a specific action."""

        index = self._page_map.get("Shortcuts")
        if index is None:
            return
        self._category_list.setCurrentRow(index)
        self._stack.setCurrentIndex(index)
        try:
            page = self._pages[index]
        except IndexError:
            return
        focus = getattr(page, "focus_action", None)
        if callable(focus) and action_id:
            focus(action_id)


class PreferencesPage(QWidget):
    """Base class for preference tabs."""

    def __init__(
        self,
        settings: SettingsManager,
        shortcut_manager: ShortcutManager | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.shortcut_manager = shortcut_manager
        self._translation_binder = TranslationBinder(self._translate_runtime)

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(self._translate_runtime, text)

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback(type(self).__name__, text)

    @staticmethod
    def _reset_combo_items(
        combo: QComboBox,
        items: Sequence[tuple[str, str]],
        *,
        current_data: str | None = None,
    ) -> None:
        selected = current_data if current_data is not None else combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for label, value in items:
            combo.addItem(label, value)
        index = combo.findData(selected)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _add_labeled_row(self, layout: QFormLayout, text: str, widget: QWidget) -> None:
        label = QLabel()
        label.setWordWrap(True)
        self._translation_binder.register(label.setText, text)
        layout.addRow(label, widget)

    def collect_values(self) -> dict[str, Any]:  # pragma: no cover - implemented in subclasses
        raise NotImplementedError

    def save_settings(self) -> None:
        """Persist page-owned settings that are NOT routed through
        :class:`SettingsManager`.

        The default implementation is a no-op — pages that go entirely
        through ``collect_values`` → ``apply_callback`` don't need to
        override this. Pages that manage their own file (e.g. JSON-backed
        per-plugin configs) override ``save_settings`` to perform the write
        and keep ``collect_values`` a pure accessor.
        """


class PerformancePage(PreferencesPage):
    def __init__(self, settings: SettingsManager) -> None:
        super().__init__(settings)
        form = QFormLayout(self)

        self.use_numba_check = QCheckBox()
        self._translation_binder.register(
            self.use_numba_check.setText,
            self._ts("Enable Numba optimizations"),
        )
        self.cache_size_spin = QSpinBox()
        self.cache_size_spin.setRange(10, 10_000)
        self.cache_size_spin.setSuffix(self._ts(" items"))
        self.undo_steps_spin = QSpinBox()
        self.undo_steps_spin.setRange(10, 1_000)
        self.memmap_threshold_spin = QSpinBox()
        self.memmap_threshold_spin.setRange(1, 1_000)
        self.memmap_threshold_spin.setSuffix(self._ts(" MB"))
        self.recompose_fps_spin = QSpinBox()
        self.recompose_fps_spin.setRange(10, 120)
        self.recompose_fps_spin.setSuffix(self._ts(" fps"))
        self.recompose_fps_spin.setToolTip(
            self._ts("Lower values reduce CPU load on large canvases")
        )

        # View-resolution compositing (replaces the retired SSO overlay row)
        self.sharp_vector_check = QCheckBox()
        self._translation_binder.register(
            self.sharp_vector_check.setText,
            self._ts("Enable sharp vector rendering (zoom)"),
        )
        self.sharp_vector_check.setToolTip(
            self._ts(
                "Composites the display at view resolution so vector and text "
                "layers stay sharp when zoomed in, regardless of layer order."
            )
        )

        # Spread viewport: quality of the page facing the one being edited.
        self.spread_quality_combo = QComboBox()
        self._populate_spread_quality_combo()
        self.spread_quality_combo.setToolTip(
            self._ts(
                "Lowers the composite resolution of the facing page in a spread. "
                "Full keeps it as sharp as the page being edited; the lower steps "
                "trade sharpness for GPU memory on large, many-layered pages."
            )
        )

        form.addRow(self.use_numba_check)
        self._add_labeled_row(
            form,
            self._ts("Cache Size"),
            self.cache_size_spin,
        )
        self._add_labeled_row(
            form,
            self._ts("Undo Steps"),
            self.undo_steps_spin,
        )
        self._add_labeled_row(
            form,
            self._ts("Memmap Threshold"),
            self.memmap_threshold_spin,
        )
        self._add_labeled_row(
            form,
            self._ts("Recompose FPS Limit"),
            self.recompose_fps_spin,
        )
        form.addRow(self.sharp_vector_check)
        self._add_labeled_row(
            form,
            self._ts("Spread Facing Page Quality"),
            self.spread_quality_combo,
        )
        from .brush_size_acceleration_preferences import (  # noqa: PLC0415
            BrushSizeAccelerationControl,
        )

        self.brush_size_acceleration = BrushSizeAccelerationControl(self)
        self.brush_size_acceleration.add_to(form)

        self._load()

    def _load(self) -> None:
        self.use_numba_check.setChecked(self.settings.get_bool("performance/use_numba", True))
        self.cache_size_spin.setValue(self.settings.get_int("performance/cache_size", 100))
        self.undo_steps_spin.setValue(self.settings.get_int("performance/max_undo_steps", 50))
        threshold_bytes = self.settings.get_int("canvas/use_memmap_threshold", 10_000_000)
        self.memmap_threshold_spin.setValue(threshold_bytes // 1_000_000 or 1)
        self.recompose_fps_spin.setValue(self.settings.get_int("performance/recompose_fps", 60))
        self.sharp_vector_check.setChecked(
            self.settings.get_bool("performance/view_resolution_compositing_enabled", True)
        )
        self._populate_spread_quality_combo(
            current_data=str(self.settings.get("performance/spread_inactive_page_quality", "full"))
        )
        self.brush_size_acceleration.load()

    def collect_values(self) -> dict[str, Any]:
        threshold_bytes = self.memmap_threshold_spin.value() * 1_000_000
        return {
            "performance/use_numba": self.use_numba_check.isChecked(),
            "performance/cache_size": self.cache_size_spin.value(),
            "performance/max_undo_steps": self.undo_steps_spin.value(),
            "canvas/use_memmap_threshold": threshold_bytes,
            "performance/recompose_fps": self.recompose_fps_spin.value(),
            "performance/view_resolution_compositing_enabled": (
                self.sharp_vector_check.isChecked()
            ),
            "performance/spread_inactive_page_quality": (self.spread_quality_combo.currentData()),
            **self.brush_size_acceleration.collect_values(),
        }

    def _populate_spread_quality_combo(self, current_data: str | None = None) -> None:
        items = [
            (self._ts("Full"), "full"),
            (self._ts("Balanced"), "balanced"),
            (self._ts("Low"), "low"),
        ]
        self._reset_combo_items(self.spread_quality_combo, items, current_data=current_data)

    def refresh_translations(self) -> None:
        super().refresh_translations()
        self.cache_size_spin.setSuffix(self._ts(" items"))
        self.memmap_threshold_spin.setSuffix(self._ts(" MB"))
        self.recompose_fps_spin.setSuffix(self._ts(" fps"))
        self._populate_spread_quality_combo()
        self.brush_size_acceleration.refresh_value_label()


class InterfacePage(PreferencesPage):
    def __init__(  # noqa: PLR0915
        self,
        settings: SettingsManager,
    ) -> None:
        super().__init__(settings)
        form = QFormLayout(self)

        self.language_combo = QComboBox()
        self._populate_language_combo()

        self.theme_combo = QComboBox()
        self._populate_theme_combo()
        self.toolbar_check = QCheckBox()
        self._translation_binder.register(
            self.toolbar_check.setText,
            self._ts("Show toolbar"),
        )
        self.statusbar_check = QCheckBox()
        self._translation_binder.register(
            self.statusbar_check.setText,
            self._ts("Show status bar"),
        )
        self.grid_size_spin = QSpinBox()
        self.grid_size_spin.setRange(1, 5000)
        self.grid_size_spin.setSingleStep(1)
        self.grid_size_spin.setSuffix(self._ts(" px"))
        self.per_page_zoom_check = QCheckBox()
        self._translation_binder.register(
            self.per_page_zoom_check.setText,
            self._ts("Remember zoom and pan per page"),
        )
        self.per_page_zoom_check.setToolTip(
            self._ts(
                "Each page keeps the zoom and position it was left at. Off keeps "
                "one view for the whole document."
            )
        )
        self.external_services_check = QCheckBox()
        self._translation_binder.register(
            self.external_services_check.setText,
            self._ts("Allow external AI services (ComfyUI, LLM Gateway, NanoBanana)"),
        )
        self.external_services_help = QLabel(
            self._ts(
                "When disabled, network access to ComfyUI, LLM Gateway, and NanoBanana is blocked."
            ),
            self,
        )
        self.external_services_help.setWordWrap(True)
        self.external_services_help.setStyleSheet("color: #666666;")
        self._translation_binder.register(
            self.external_services_help.setText,
            self._ts(
                "When disabled, network access to ComfyUI, LLM Gateway, and NanoBanana is blocked."
            ),
        )

        self._menu_checkboxes: dict[str, QCheckBox] = {}
        self._menu_group = QGroupBox()
        self._translation_binder.register(
            self._menu_group.setTitle,
            self._ts("Menus"),
        )
        menu_layout = QVBoxLayout(self._menu_group)
        for menu_id, definition in MENU_DEFINITIONS.items():
            checkbox = self._create_menu_checkbox(definition)
            menu_layout.addWidget(checkbox)
            self._menu_checkboxes[menu_id] = checkbox

        mask_color_row = self._build_mask_overlay_widgets()

        self._add_labeled_row(form, self._ts("Language"), self.language_combo)
        self._add_labeled_row(form, self._ts("Color Theme"), self.theme_combo)
        form.addRow(self.toolbar_check)
        form.addRow(self.statusbar_check)
        self._add_labeled_row(form, self._ts("Grid interval"), self.grid_size_spin)
        form.addRow(self.per_page_zoom_check)
        form.addRow(self.external_services_check)
        form.addRow(self.external_services_help)
        self.external_services_check.setVisible(ai_features_enabled())
        self.external_services_help.setVisible(ai_features_enabled())
        form.addRow(self._menu_group)
        self._add_labeled_row(form, self._ts("Mask overlay color"), mask_color_row)
        self._add_labeled_row(form, self._ts("Mask overlay opacity"), self._mask_opacity_spin)

        self._load()

    def _load(self) -> None:
        theme_value = self.settings.get("ui/theme", "dark")
        if not isinstance(theme_value, str):
            theme_value = "dark"
        theme_index = self.theme_combo.findData(theme_value)
        self.theme_combo.setCurrentIndex(theme_index if theme_index >= 0 else 0)
        language_value = self.settings.get("ui/language", "system")
        if not isinstance(language_value, str):
            language_value = "system"
        language_index = self.language_combo.findData(language_value)
        self.language_combo.setCurrentIndex(language_index if language_index >= 0 else 0)
        self.toolbar_check.setChecked(self.settings.get_bool("ui/show_toolbar", True))
        self.statusbar_check.setChecked(self.settings.get_bool("ui/show_statusbar", True))
        self.grid_size_spin.setValue(self.settings.get_int("view/grid_size", 32))
        self.per_page_zoom_check.setChecked(
            self.settings.get_bool("view/per_page_zoom_enabled", False)
        )
        self.external_services_check.setChecked(
            self.settings.get_bool("privacy/allow_external_services", False)
        )
        for menu_id, checkbox in self._menu_checkboxes.items():
            definition = MENU_DEFINITIONS[menu_id]
            checkbox.setChecked(
                bool(
                    self.settings.get(
                        definition.setting_key,
                        definition.enabled_by_default,
                    )
                )
            )
        # Mask overlay
        color_hex = self.settings.get("ui/mask_overlay_color", "#00FFFF")
        if isinstance(color_hex, str):
            self._mask_overlay_color = QColor(color_hex)
        self._mask_opacity_spin.setValue(self.settings.get_int("ui/mask_overlay_opacity", 128))
        self._update_mask_color_preview()

    def _build_mask_overlay_widgets(self) -> QWidget:
        self._mask_overlay_color = QColor(0, 255, 255)
        self._mask_color_btn = QPushButton()
        self._translation_binder.register(self._mask_color_btn.setText, self._ts("Pick Color..."))
        self._mask_color_preview = QLabel(" ")
        self._mask_color_preview.setFixedHeight(18)
        self._mask_color_preview.setFixedWidth(80)
        self._mask_color_btn.clicked.connect(self._pick_mask_overlay_color)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self._mask_color_btn)
        row_layout.addWidget(self._mask_color_preview)
        self._mask_opacity_spin = QSpinBox()
        self._mask_opacity_spin.setRange(0, 255)
        self._mask_opacity_spin.setSingleStep(1)
        return row

    def _pick_mask_overlay_color(self) -> None:
        color = QColorDialog.getColor(self._mask_overlay_color, self)
        if color.isValid():
            self._mask_overlay_color = color
            self._update_mask_color_preview()

    # ------------------------------------------------------------------
    # Phase 8a: API Keys section (paid Gateway credentials)
    # ------------------------------------------------------------------

    def _update_mask_color_preview(self) -> None:
        c = self._mask_overlay_color
        self._mask_color_preview.setStyleSheet(
            f"border: 1px solid #999; background-color: rgb({c.red()}, {c.green()}, {c.blue()});"
        )

    def _populate_language_combo(self, current_data: str | None = None) -> None:
        items = [(self._ts("System Default"), "system")]
        items.extend((label, code) for code, label in available_language_options())
        self._reset_combo_items(self.language_combo, items, current_data=current_data)

    def _populate_theme_combo(self, current_data: str | None = None) -> None:
        items = [
            (self._ts("Dark"), "dark"),
            (self._ts("Light"), "light"),
            (self._ts("System"), "system"),
        ]
        self._reset_combo_items(self.theme_combo, items, current_data=current_data)

    def _create_menu_checkbox(self, definition: MenuDefinition) -> QCheckBox:
        checkbox = QCheckBox()
        label = definition.checkbox_label()
        if definition.preference_label:
            self._translation_binder.register_domain("PreferencesDialog", checkbox.setText, label)
        else:
            self._translation_binder.register(checkbox.setText, label)
        checkbox.setProperty("menu_id", definition.menu_id)
        return checkbox

    def collect_values(self) -> dict[str, Any]:
        language = self.language_combo.currentData()
        theme_value = self.theme_combo.currentData()
        return {
            "ui/language": language if isinstance(language, str) else "system",
            "ui/theme": theme_value if isinstance(theme_value, str) else "dark",
            "ui/show_toolbar": self.toolbar_check.isChecked(),
            "ui/show_statusbar": self.statusbar_check.isChecked(),
            "view/grid_size": self.grid_size_spin.value(),
            "view/per_page_zoom_enabled": self.per_page_zoom_check.isChecked(),
            **(
                {"privacy/allow_external_services": self.external_services_check.isChecked()}
                if ai_features_enabled()
                else {}
            ),
            "ui/mask_overlay_color": self._mask_overlay_color.name(),
            "ui/mask_overlay_opacity": self._mask_opacity_spin.value(),
            **{
                MENU_DEFINITIONS[menu_id].setting_key: checkbox.isChecked()
                for menu_id, checkbox in self._menu_checkboxes.items()
            },
        }

    def refresh_translations(self) -> None:
        current_language = self.language_combo.currentData()
        current_theme = self.theme_combo.currentData()
        super().refresh_translations()
        self._populate_language_combo(
            current_data=current_language if isinstance(current_language, str) else None
        )
        self._populate_theme_combo(
            current_data=current_theme if isinstance(current_theme, str) else None
        )
        self.grid_size_spin.setSuffix(self._ts(" px"))


class FileIOPage(PreferencesPage):
    def __init__(self, settings: SettingsManager) -> None:
        super().__init__(settings)
        form = QFormLayout(self)

        self.autosave_check = QCheckBox()
        self._translation_binder.register(
            self.autosave_check.setText,
            self._ts("Enable autosave (effective after restart)"),
        )
        autosave_recovery_note_text = self._ts(
            "Crash recovery requires autosave. If autosave is off, unsaved changes "
            "cannot be restored after a forced quit or crash."
        )
        self._translation_binder.register(
            self.autosave_check.setToolTip,
            autosave_recovery_note_text,
        )
        self.autosave_recovery_note = QLabel()
        self.autosave_recovery_note.setWordWrap(True)
        self._translation_binder.register(
            self.autosave_recovery_note.setText,
            autosave_recovery_note_text,
        )
        self._translation_binder.register(
            self.autosave_recovery_note.setToolTip,
            autosave_recovery_note_text,
        )
        self.autosave_interval_spin = QSpinBox()
        self.autosave_interval_spin.setRange(30, 3_600)
        self.autosave_interval_spin.setSuffix(self._ts(" sec"))
        self.compression_slider = QSlider(Qt.Orientation.Horizontal)
        self.compression_slider.setRange(1, 100)
        self.compression_value = QLabel("95")
        compression_row = QHBoxLayout()
        compression_row.addWidget(self.compression_slider)
        compression_row.addWidget(self.compression_value)
        compression_row_widget = QWidget()
        compression_row_widget.setLayout(compression_row)

        self.layout_dir_edit = QLineEdit()
        self.layout_dir_edit.setPlaceholderText(str(default_layout_directory(create=False)))
        browse_btn = QPushButton()
        self._translation_binder.register(browse_btn.setText, self._ts("Browse…"))
        browse_btn.clicked.connect(self._choose_layout_directory)
        layout_dir_row = QHBoxLayout()
        layout_dir_row.addWidget(self.layout_dir_edit)
        layout_dir_row.addWidget(browse_btn)
        layout_dir_row_widget = QWidget()
        layout_dir_row_widget.setLayout(layout_dir_row)

        form.addRow(self.autosave_check)
        form.addRow(self.autosave_recovery_note)
        self._add_labeled_row(
            form,
            self._ts("Autosave interval (effective after restart)"),
            self.autosave_interval_spin,
        )
        self._add_labeled_row(
            form,
            self._ts("Compression quality"),
            compression_row_widget,
        )
        self._add_labeled_row(
            form,
            self._ts("Layout directory"),
            layout_dir_row_widget,
        )

        self.compression_slider.valueChanged.connect(
            lambda value: self.compression_value.setText(str(value))
        )

        self._load()

    def _load(self) -> None:
        self.autosave_check.setChecked(self.settings.get_bool("io/autosave_enabled", True))
        self.autosave_interval_spin.setValue(self.settings.get_int("io/autosave_interval", 300))
        quality = self.settings.get_int("io/compression_quality", 95)
        self.compression_slider.setValue(quality)
        self.compression_value.setText(str(quality))
        layout_dir = self.settings.get(
            "io/layout_directory",
            str(default_layout_directory()),
        )
        self.layout_dir_edit.setText(str(layout_dir))

    def collect_values(self) -> dict[str, Any]:
        return {
            "io/autosave_enabled": self.autosave_check.isChecked(),
            "io/autosave_interval": self.autosave_interval_spin.value(),
            "io/compression_quality": self.compression_slider.value(),
            "io/layout_directory": self.layout_dir_edit.text().strip(),
        }

    def refresh_translations(self) -> None:
        super().refresh_translations()
        self.autosave_interval_spin.setSuffix(self._ts(" sec"))

    def _choose_layout_directory(self) -> None:
        start_dir = self.layout_dir_edit.text().strip()
        if not start_dir:
            start_dir = str(default_layout_directory())
        directory = QFileDialog.getExistingDirectory(
            self,
            self._ts("Select Layout Directory"),
            start_dir,
        )
        if directory:
            self.layout_dir_edit.setText(directory)


class LoomStrokeMcpPage(PreferencesPage):
    """Preferences for the local LoomStroke MCP runtime."""

    def __init__(
        self,
        settings: SettingsManager,
        *,
        endpoint_info_provider: Callable[[], Any] | None = None,
        session_token_refresh_callback: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(settings)
        self._endpoint_info_provider = endpoint_info_provider
        self._session_token_refresh_callback = session_token_refresh_callback
        form = QFormLayout(self)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.enable_http_check = QCheckBox()
        self._translation_binder.register(
            self.enable_http_check.setText,
            self._ts("Enable local LoomStroke MCP server"),
        )
        self.enable_http_check.setToolTip(
            self._ts("Opens a Streamable HTTP MCP endpoint on 127.0.0.1 only.")
        )

        self.start_on_launch_check = QCheckBox()
        self._translation_binder.register(
            self.start_on_launch_check.setText,
            self._ts("Start on launch"),
        )
        self.start_on_launch_check.setToolTip(
            self._ts("Starts the local endpoint automatically when LoomStroke opens.")
        )

        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setToolTip(self._ts("Use 0 to select an automatic local port."))

        endpoint_row, token_row, refresh_row = self._build_endpoint_widgets()

        self.allowed_origins_edit = QLineEdit()
        self.allowed_origins_edit.setPlaceholderText("http://127.0.0.1:PORT, http://localhost:PORT")
        self.allowed_origins_edit.setToolTip(
            self._ts("Comma-separated HTTP Origins allowed to call the local MCP endpoint.")
        )

        root_row = self._build_root_row()
        self.confirmation_policy_combo = QComboBox()
        self._translation_binder.register(
            self.confirmation_policy_combo.setToolTip,
            self._ts("Controls when LoomStroke asks before allowing MCP agent actions."),
        )
        self._refresh_confirmation_policy_items()
        from .mcp_confirmation_preferences import McpHardConfirmationControl  # noqa: PLC0415

        self._hard_confirmation_control = McpHardConfirmationControl(self)
        self.skip_hard_confirmations_check = self._hard_confirmation_control.checkbox
        self.security_note = self._build_security_note()

        form.addRow(self.enable_http_check)
        form.addRow(self.start_on_launch_check)
        self._add_labeled_row(form, self._ts("Port"), self.port_spin)
        self._add_labeled_row(form, self._ts("Endpoint URL"), endpoint_row)
        self._add_labeled_row(form, self._ts("Session Token"), token_row)
        form.addRow(refresh_row)
        self._add_labeled_row(form, self._ts("Allowed Origins"), self.allowed_origins_edit)
        self._add_labeled_row(form, self._ts("File Roots"), root_row)
        self._add_labeled_row(
            form,
            self._ts("Agent Approval"),
            self.confirmation_policy_combo,
        )
        form.addRow(self.skip_hard_confirmations_check)
        form.addRow(self.security_note)

        self.enable_http_check.toggled.connect(self._sync_enabled_state)
        self._load()

    def _build_endpoint_widgets(self) -> tuple[QWidget, QWidget, QWidget]:
        self.endpoint_url_edit = QLineEdit()
        self.endpoint_url_edit.setReadOnly(True)
        self.endpoint_url_edit.setPlaceholderText(self._ts("Not running"))
        self.copy_endpoint_btn = QPushButton()
        self._translation_binder.register(self.copy_endpoint_btn.setText, self._ts("Copy"))
        self.copy_endpoint_btn.clicked.connect(
            lambda: self._copy_text(self.endpoint_url_edit.text())
        )
        endpoint_row = self._build_copy_row(self.endpoint_url_edit, self.copy_endpoint_btn)

        self.session_token_edit = QLineEdit()
        self.session_token_edit.setReadOnly(True)
        self.session_token_edit.setPlaceholderText(self._ts("Not running"))
        self.copy_token_btn = QPushButton()
        self._translation_binder.register(self.copy_token_btn.setText, self._ts("Copy"))
        self.copy_token_btn.clicked.connect(lambda: self._copy_text(self.session_token_edit.text()))
        self.refresh_token_btn = QPushButton()
        self._translation_binder.register(self.refresh_token_btn.setText, self._ts("Refresh"))
        self.refresh_token_btn.clicked.connect(self._refresh_session_token)
        token_row = self._build_copy_row(
            self.session_token_edit,
            self.copy_token_btn,
            self.refresh_token_btn,
        )

        self.refresh_endpoint_btn = QPushButton()
        self._translation_binder.register(self.refresh_endpoint_btn.setText, self._ts("Refresh"))
        self.refresh_endpoint_btn.clicked.connect(self._refresh_endpoint_info)
        self.endpoint_status_label = QLabel(self)
        self.endpoint_status_label.setStyleSheet("color: #666666;")
        refresh_row = QWidget(self)
        refresh_layout = QHBoxLayout(refresh_row)
        refresh_layout.setContentsMargins(0, 0, 0, 0)
        refresh_layout.setSpacing(8)
        refresh_layout.addWidget(self.refresh_endpoint_btn)
        refresh_layout.addWidget(self.endpoint_status_label, 1)
        return endpoint_row, token_row, refresh_row

    def _build_root_row(self) -> QWidget:
        self.roots_edit = QLineEdit()
        self.roots_edit.setPlaceholderText(str(Path.home()))
        self.roots_edit.setToolTip(
            self._ts("Semicolon-separated directories allowed for MCP file writes.")
        )
        browse_btn = QPushButton()
        self._translation_binder.register(browse_btn.setText, self._ts("Browse…"))
        browse_btn.clicked.connect(self._choose_root_directory)
        root_row = QWidget(self)
        root_layout = QHBoxLayout(root_row)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(8)
        root_layout.addWidget(self.roots_edit, 1)
        root_layout.addWidget(browse_btn)
        return root_row

    def _build_security_note(self) -> QLabel:
        note_text = self._ts(
            "The live endpoint stays local. File writes and external generation still require "
            "the existing LoomStroke safety gates."
        )
        label = QLabel(note_text, self)
        label.setWordWrap(True)
        label.setStyleSheet("color: #666666;")
        self._translation_binder.register(label.setText, note_text)
        return label

    def _load(self) -> None:
        enabled = self.settings.get_bool(MCP_LIVE_HTTP_ENABLED_KEY, False)
        start_on_launch = self.settings.get_bool(MCP_LIVE_HTTP_START_ON_LAUNCH_KEY, False)
        allowed_origins = self._settings_str_list(MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY)
        roots = self._settings_str_list(MCP_LIVE_HTTP_ROOTS_KEY)
        port = self.settings.get_int(MCP_LIVE_HTTP_PORT_KEY, DEFAULT_MCP_LIVE_HTTP_PORT)
        if port == 0 and not enabled and not start_on_launch and not allowed_origins and not roots:
            port = DEFAULT_MCP_LIVE_HTTP_PORT

        self.enable_http_check.setChecked(enabled)
        self.start_on_launch_check.setChecked(start_on_launch)
        self.port_spin.setValue(port)
        self.allowed_origins_edit.setText(", ".join(allowed_origins))
        self.roots_edit.setText("; ".join(roots))
        policy = str(
            self.settings.get(MCP_CONFIRMATION_POLICY_KEY, MCP_CONFIRMATION_POLICY_ALWAYS_ASK)
        )
        if self.confirmation_policy_combo.findData(policy) < 0:
            policy = MCP_CONFIRMATION_POLICY_ALWAYS_ASK
        self.confirmation_policy_combo.setCurrentIndex(
            self.confirmation_policy_combo.findData(policy)
        )
        self._sync_enabled_state(self.enable_http_check.isChecked())
        self._hard_confirmation_control.load()
        self._refresh_endpoint_info()

    def collect_values(self) -> dict[str, Any]:
        return {
            MCP_LIVE_HTTP_ENABLED_KEY: self.enable_http_check.isChecked(),
            **self._hard_confirmation_control.collect_values(),
            MCP_LIVE_HTTP_START_ON_LAUNCH_KEY: self.start_on_launch_check.isChecked(),
            MCP_LIVE_HTTP_PORT_KEY: self.port_spin.value(),
            MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY: self._parse_origins(
                self.allowed_origins_edit.text()
            ),
            MCP_LIVE_HTTP_ROOTS_KEY: self._parse_roots(self.roots_edit.text()),
            MCP_CONFIRMATION_POLICY_KEY: str(
                self.confirmation_policy_combo.currentData() or MCP_CONFIRMATION_POLICY_ALWAYS_ASK
            ),
        }

    def _sync_enabled_state(self, enabled: bool) -> None:
        self.start_on_launch_check.setEnabled(enabled)

    def _choose_root_directory(self) -> None:
        existing = self._parse_roots(self.roots_edit.text())
        start_dir = existing[0] if existing else str(Path.home())
        directory = QFileDialog.getExistingDirectory(
            self,
            self._ts("Select MCP File Root"),
            start_dir,
        )
        if not directory:
            return
        roots = [root for root in existing if root != directory]
        roots.append(directory)
        self.roots_edit.setText("; ".join(roots))

    def _settings_str_list(self, key: str) -> list[str]:
        raw = self.settings.get(key, [])
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                return []
            if stripped[0] == "[":
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    return [stripped]
                if isinstance(parsed, list):
                    return [str(item) for item in parsed if str(item).strip()]
            return [stripped]
        if isinstance(raw, Sequence):
            return [str(item) for item in raw if str(item).strip()]
        return []

    @staticmethod
    def _parse_origins(text: str) -> list[str]:
        return [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]

    @staticmethod
    def _parse_roots(text: str) -> list[str]:
        return [part.strip() for part in text.replace("\n", ";").split(";") if part.strip()]

    def _build_copy_row(self, edit: QLineEdit, *buttons: QPushButton) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(edit, 1)
        for button in buttons:
            layout.addWidget(button)
        return row

    def _refresh_endpoint_info(self) -> None:
        info = self._current_endpoint_info()
        self._apply_endpoint_info(info)

    def _apply_endpoint_info(self, info: Any) -> None:
        running = bool(getattr(info, "running", False)) if info is not None else False
        url = str(getattr(info, "url", "") or "") if running else ""
        token = str(getattr(info, "session_token", "") or "") if info is not None else ""

        self.endpoint_url_edit.setText(url)
        self.session_token_edit.setText(token)
        self.copy_endpoint_btn.setEnabled(bool(url))
        self.copy_token_btn.setEnabled(bool(token))
        self.refresh_token_btn.setEnabled(self._session_token_refresh_callback is not None)
        status_text = self._ts("Running") if running else self._ts("Not running")
        self.endpoint_status_label.setText(status_text)

    def _current_endpoint_info(self) -> Any:
        if self._endpoint_info_provider is None:
            return None
        try:
            return self._endpoint_info_provider()
        except Exception:
            return None

    def _copy_text(self, text: str) -> None:
        if not text:
            return
        QApplication.clipboard().setText(text)

    def _refresh_session_token(self) -> None:
        callback = self._session_token_refresh_callback
        if callback is None:
            return
        response = QMessageBox.question(
            self,
            self._ts("Refresh Session Token"),
            self._ts(
                "Refresh the MCP session token?\n\n"
                "After refreshing the token, any agents registered to this LoomStroke "
                "MCP server must update their connection settings before they can connect again."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        info = callback()
        if info is not None:
            self._apply_endpoint_info(info)
        else:
            self._refresh_endpoint_info()

    def refresh_translations(self) -> None:
        super().refresh_translations()
        self._refresh_confirmation_policy_items()

    def _refresh_confirmation_policy_items(self) -> None:
        self._reset_combo_items(
            self.confirmation_policy_combo,
            [
                (
                    self._ts("Allow all MCP agent actions"),
                    MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW,
                ),
                (
                    self._ts("Ask only before external generation queues"),
                    MCP_CONFIRMATION_POLICY_EXTERNAL_ONLY,
                ),
                (
                    self._ts("Ask before every MCP agent action"),
                    MCP_CONFIRMATION_POLICY_ALWAYS_ASK,
                ),
            ],
        )
        self.endpoint_url_edit.setPlaceholderText(self._ts("Not running"))
        self.session_token_edit.setPlaceholderText(self._ts("Not running"))
        self._refresh_endpoint_info()


class Sam3RuntimePage(PreferencesPage):
    """Preferences page for the SAM 3.1 AI Select runtime."""

    def __init__(self, settings: SettingsManager) -> None:
        super().__init__(settings)
        form = QFormLayout(self)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        from loomstroke.ui.dialogs.sam31_preferences import (  # noqa: PLC0415
            Sam31PreferencesSection,
        )

        self.sam31_section = Sam31PreferencesSection(settings, translate=self._ts)
        form.addRow(self.sam31_section)

        self.asset_cache_spin = QSpinBox()
        self.asset_cache_spin.setRange(64, 262144)
        self.asset_cache_spin.setSuffix(self._ts(" MB"))
        self._add_labeled_row(form, self._ts("Asset cache limit"), self.asset_cache_spin)
        self._load()

    def _load(self) -> None:
        self.sam31_section.load()
        self.asset_cache_spin.setValue(
            self._setting_int(
                AI_SELECT_ASSETS_MAX_CACHE_MB_KEY,
                DEFAULT_AI_SELECT_ASSETS_MAX_CACHE_MB,
            )
        )

    def collect_values(self) -> dict[str, Any]:
        return {
            **self.sam31_section.collect_values(),
            AI_SELECT_ASSETS_MAX_CACHE_MB_KEY: self.asset_cache_spin.value(),
        }

    def _setting_int(self, key: str, default: int) -> int:
        getter = getattr(self.settings, "get_int", None)
        if callable(getter):
            try:
                return int(getter(key, default))
            except (TypeError, ValueError):
                return default
        try:
            value = self.settings.get(key, default)
        except TypeError:
            value = self.settings.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


class ShortcutsPage(PreferencesPage):
    """Interactive shortcut editor backed by :class:`ShortcutManager`."""

    _CATEGORY_ROLE = Qt.ItemDataRole.UserRole
    _OWNER_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(
        self,
        settings: SettingsManager,
        shortcut_manager: ShortcutManager,
    ) -> None:
        super().__init__(settings, shortcut_manager)
        self._shortcuts = shortcut_manager
        self._host_owner_label = self._ts("Host")
        self._mouse_bindings = MouseBindingHelper(self.settings, self._translate_shortcut_text)
        self._conflict_detector = ShortcutConflictDetector(self._translate_shortcut_text)
        self._shortcut_filter = ShortcutFilterController(
            category_role=self._CATEGORY_ROLE,
            owner_role=self._OWNER_ROLE,
        )
        self._shortcut_table_model: ShortcutTableModel | None = None
        self._build_ui()
        self._shortcuts.shortcut_changed.connect(self._on_shortcut_changed)
        self.settings.settings_changed.connect(self._on_setting_changed)
        self._refresh_table()

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(
            lambda source: translate_with_fallback("ShortcutsPage", source),
            text,
        )

    @property
    def _table_model(self) -> ShortcutTableModel:
        if self._shortcut_table_model is None:  # pragma: no cover - build order guard
            raise RuntimeError("Shortcut table model has not been initialized")
        return self._shortcut_table_model

    @property
    def _mouse_binding_defs(self) -> list[dict[str, Any]]:
        return self._mouse_bindings.definitions

    @property
    def _mouse_action_ids(self) -> set[str]:
        return self._mouse_bindings.action_ids

    @property
    def _mouse_setting_keys(self) -> set[str]:
        return self._mouse_bindings.setting_keys

    @property
    def _entries(self) -> list[ShortcutAction]:
        return self._table_model.entries

    @property
    def _visible_entries(self) -> list[ShortcutAction]:
        return self._table_model.visible_entries

    @property
    def _conflicts(self) -> dict[str, list[str]]:
        return self._table_model.conflicts

    @property
    def _table_headers(self) -> list[str]:
        return self._table_model.table_headers

    @property
    def _table(self) -> QTableWidget:
        return self._table_model.table

    @property
    def _summary_template(self) -> str:
        return self._table_model.summary_template

    @_summary_template.setter
    def _summary_template(self, value: str) -> None:
        self._table_model.summary_template = value

    @property
    def _summary_counts(self) -> tuple[int, int]:
        return self._table_model.summary_counts

    @property
    def _filter_edit(self) -> QLineEdit:
        return self._shortcut_filter.filter_edit

    @property
    def _clear_filter_btn(self) -> QPushButton:
        return self._shortcut_filter.clear_filter_btn

    @property
    def _assigned_check(self) -> QCheckBox:
        return self._shortcut_filter.assigned_check

    @property
    def _conflicts_check(self) -> QCheckBox:
        return self._shortcut_filter.conflicts_check

    @property
    def _reset_filters_btn(self) -> QPushButton:
        return self._shortcut_filter.reset_filters_btn

    @property
    def _category_tree(self) -> QTreeWidget:
        return self._shortcut_filter.category_tree

    @property
    def _category_filter(self) -> tuple[str | None, str | None]:
        return self._shortcut_filter.category_filter

    @_category_filter.setter
    def _category_filter(self, value: tuple[str | None, str | None]) -> None:
        self._shortcut_filter.category_filter = value

    def _build_ui(self) -> None:
        outer_layout = QHBoxLayout(self)
        content_layout = self._build_category_column(outer_layout)
        self._build_filter_row(content_layout)
        self._build_toggle_row(content_layout)
        self._build_shortcut_table(content_layout)
        self._build_action_buttons(content_layout)

    def _build_category_column(self, outer_layout: QHBoxLayout) -> QVBoxLayout:
        self._category_tree.currentItemChanged.connect(self._on_category_selection_changed)
        outer_layout.addWidget(self._category_tree, 0)
        content_layout = QVBoxLayout()
        outer_layout.addLayout(content_layout, 1)
        return content_layout

    def _build_filter_row(self, content_layout: QVBoxLayout) -> None:
        filter_layout = QHBoxLayout()
        filter_label = QLabel()
        self._translation_binder.register(filter_label.setText, self._ts("Filter"))
        self._filter_edit.setPlaceholderText(self._ts("Search actions…"))
        self._filter_edit.textChanged.connect(lambda _text: self._apply_filters())
        self._translation_binder.register(self._clear_filter_btn.setText, self._ts("Clear"))
        self._clear_filter_btn.clicked.connect(self._reset_text_filter)
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(self._filter_edit, 1)
        filter_layout.addWidget(self._clear_filter_btn)
        content_layout.addLayout(filter_layout)

    def _build_toggle_row(self, content_layout: QVBoxLayout) -> None:
        toggle_layout = QHBoxLayout()
        self._translation_binder.register(
            self._assigned_check.setText,
            self._ts("Assigned only"),
        )
        self._assigned_check.toggled.connect(lambda _checked: self._apply_filters())
        self._translation_binder.register(
            self._conflicts_check.setText,
            self._ts("Conflicts only"),
        )
        self._conflicts_check.toggled.connect(lambda _checked: self._apply_filters())
        self._translation_binder.register(
            self._reset_filters_btn.setText,
            self._ts("Reset filters"),
        )
        self._reset_filters_btn.clicked.connect(self._reset_filters)
        self._summary_label = QLabel()
        self._summary_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        toggle_layout.addWidget(self._assigned_check)
        toggle_layout.addWidget(self._conflicts_check)
        toggle_layout.addWidget(self._reset_filters_btn)
        toggle_layout.addStretch(1)
        toggle_layout.addWidget(self._summary_label)
        content_layout.addLayout(toggle_layout)

    def _build_shortcut_table(self, content_layout: QVBoxLayout) -> None:
        self._shortcut_table_model = ShortcutTableModel(
            translate_text=self._translate_shortcut_text,
            owner_label=self._owner_label,
            summary_label=self._summary_label,
            summary_template=self._ts("Showing {shown} of {total}"),
        )
        self._table.doubleClicked.connect(self._edit_selected)
        content_layout.addWidget(self._table, 1)

    def _build_action_buttons(self, content_layout: QVBoxLayout) -> None:
        button_row = QHBoxLayout()
        self._edit_button = QPushButton()
        self._translation_binder.register(
            self._edit_button.setText,
            self._ts("Change…"),
        )
        self._edit_button.clicked.connect(self._edit_selected)
        self._reset_button = QPushButton()
        self._translation_binder.register(
            self._reset_button.setText,
            self._ts("Reset"),
        )
        self._reset_button.clicked.connect(self._reset_selected)
        self._reset_all_button = QPushButton()
        self._translation_binder.register(
            self._reset_all_button.setText,
            self._ts("Reset All"),
        )
        self._reset_all_button.clicked.connect(self._reset_all)
        self._import_button = QPushButton()
        self._translation_binder.register(
            self._import_button.setText,
            self._ts("Import…"),
        )
        self._import_button.clicked.connect(self._import_scheme)
        self._export_button = QPushButton()
        self._translation_binder.register(
            self._export_button.setText,
            self._ts("Export…"),
        )
        self._export_button.clicked.connect(self._export_scheme)
        self._validate_button = QPushButton()
        self._translation_binder.register(
            self._validate_button.setText,
            self._ts("Validate…"),
        )
        self._validate_button.clicked.connect(self._validate_scheme_file)
        button_row.addWidget(self._edit_button)
        button_row.addWidget(self._reset_button)
        button_row.addStretch(1)
        button_row.addWidget(self._reset_all_button)
        button_row.addWidget(self._import_button)
        button_row.addWidget(self._export_button)
        button_row.addWidget(self._validate_button)
        content_layout.addLayout(button_row)

    # ------------------------------------------------------------------
    def _build_mouse_binding_defs(self) -> list[dict[str, Any]]:
        return MouseBindingHelper.build_definitions()

    def _mouse_binding_definition(self, action_id: str) -> dict[str, Any] | None:
        return self._mouse_bindings.definition(action_id)

    def _mouse_binding_entries(self) -> list[ShortcutAction]:
        return self._mouse_bindings.entries()

    def _read_mouse_binding_value(self, definition: dict[str, Any]) -> str:
        return self._mouse_bindings.read_value(definition)

    def _mouse_binding_display_label(self, definition: dict[str, Any], value: str) -> str:
        return self._mouse_bindings.display_label(definition, value)

    def _mouse_binding_options(self, definition: dict[str, Any]) -> list[tuple[str, str]]:
        return self._mouse_bindings.options(definition)

    def _translate_shortcut_text(self, text: str) -> str:
        source_text = getattr(text, "source_text", text)
        if not isinstance(source_text, str) or not source_text:
            return ""
        return translate_with_fallback("ShortcutsPage", source_text)

    def _category_sort_key(self, category: str) -> tuple[str, int]:
        normalized = str(category)
        if normalized == "Mouse Operations":
            return ("edit", 0)
        if normalized == "Edit":
            return ("edit", 1)
        return (normalized.lower(), 0)

    def _apply_table_headers(self) -> None:
        self._table_model.apply_headers()

    def refresh_translations(self) -> None:
        selected_action: str | None = None
        current_row = self._table.currentRow()
        if current_row >= 0:
            current_item = self._table.item(current_row, 0)
            current_action = current_item.data(Qt.ItemDataRole.UserRole) if current_item else None
            if isinstance(current_action, str):
                selected_action = current_action
        super().refresh_translations()
        self._filter_edit.setPlaceholderText(self._ts("Search actions…"))
        self._host_owner_label = self._ts("Host")
        self._summary_template = self._ts("Showing {shown} of {total}")
        self._apply_table_headers()
        self._refresh_table()
        if selected_action:
            self.focus_action(selected_action, reset_filters=False)

    # ------------------------------------------------------------------
    def collect_values(self) -> dict[str, Any]:
        """Shortcut edits are applied immediately via ShortcutManager."""

        return {}

    def _reset_text_filter(self) -> None:
        self._shortcut_filter.reset_text_filter()
        self._apply_filters()

    def _reset_filters(self) -> None:
        self._shortcut_filter.reset_filters()
        self._apply_filters()

    # ------------------------------------------------------------------
    def focus_action(self, action_id: str, *, reset_filters: bool = True) -> None:
        if reset_filters:
            self._category_filter = (None, None)
            self._filter_edit.clear()
            self._select_root_category()
            self._apply_filters()
        if not self._visible_entries:
            return
        for row, entry in enumerate(self._visible_entries):
            if entry.action_id == action_id:
                self._table.selectRow(row)
                item = self._table.item(row, 0)
                if item:
                    self._table.scrollToItem(item)
                break

    def _refresh_table(self) -> None:
        entries = self._shortcuts.list_actions()
        entries.extend(self._mouse_binding_entries())
        entries.sort(
            key=lambda entry: (self._category_sort_key(entry.category), entry.label.lower())
        )
        conflicts = self._compute_conflicts(entries)
        self._table_model.set_entries(entries, conflicts)
        self._build_category_tree()
        self._apply_filters()

    def _apply_filters(self) -> None:
        entries = self._shortcut_filter.filter_entries(
            self._entries,
            self._conflicts,
            translate_text=self._translate_shortcut_text,
            owner_label=self._owner_label,
        )
        self._populate_rows(entries)

    def _populate_rows(self, entries: Iterable[ShortcutAction]) -> None:
        self._table_model.populate_rows(entries)

    def _update_summary_label(self) -> None:
        self._table_model.update_summary_label()

    def _owner_label(self, entry: ShortcutAction) -> str:
        owner = entry.owner
        if isinstance(owner, str) and owner:
            return owner
        action_id = entry.action_id
        if isinstance(action_id, str) and "." in action_id:
            return action_id.split(".", 1)[0]
        return self._host_owner_label

    def _compute_conflicts(self, entries: Iterable[ShortcutAction]) -> dict[str, list[str]]:
        return self._conflict_detector.compute(entries)

    def _build_category_tree(self) -> None:
        self._shortcut_filter.build_category_tree(
            self._entries,
            owner_label=self._owner_label,
            translate_text=self._translate_shortcut_text,
            category_sort_key=self._category_sort_key,
            all_shortcuts_label=self._ts("All Shortcuts"),
        )

    def _find_category_item(self, needle: tuple[str | None, str | None]) -> QTreeWidgetItem | None:
        return self._shortcut_filter.find_category_item(needle)

    def _select_root_category(self) -> None:
        self._shortcut_filter.select_root_category()

    def _on_category_selection_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        self._shortcut_filter.update_category_selection(current)
        self._apply_filters()

    def _import_scheme(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._ts("Import Shortcut Scheme"),
            str(Path.home()),
            self._ts("Shortcut Scheme (*.json);;All Files (*.*)"),
        )
        if not path:
            return
        try:
            payload = self._load_scheme_payload(Path(path))
        except (OSError, json.JSONDecodeError) as exc:
            message = self._ts("Could not import scheme: {error}").format(error=exc)
            QMessageBox.critical(self, self._ts("Import Failed"), message)
            return

        validation = self._shortcuts.validate_scheme(payload, require_known=True)
        if not validation.valid or validation.actions is None:
            self._show_validation_messages(
                self._ts("Import Failed"),
                self._ts("Invalid shortcut scheme detected."),
                validation.errors,
                critical=True,
            )
            return

        if validation.warnings:
            self._show_validation_messages(
                self._ts("Import Warnings"),
                self._ts("Scheme is valid but raised the following warnings:"),
                validation.warnings,
                critical=False,
            )

        try:
            self._shortcuts.import_scheme(validation.actions)
        except ValueError as exc:
            QMessageBox.critical(self, self._ts("Import Failed"), str(exc))
            return
        self._refresh_table()

    def _export_scheme(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            self._ts("Export Shortcut Scheme"),
            str(Path.home() / "shortcuts.json"),
            self._ts("Shortcut Scheme (*.json);;All Files (*.*)"),
        )
        if not path:
            return
        payload = {
            "version": 1,
            "actions": self._shortcuts.export_scheme(),
        }
        try:
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            message = self._ts("Could not export scheme: {error}").format(error=exc)
            QMessageBox.critical(self, self._ts("Export Failed"), message)

    def _validate_scheme_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._ts("Validate Shortcut Scheme"),
            str(Path.home()),
            self._ts("Shortcut Scheme (*.json);;All Files (*.*)"),
        )
        if not path:
            return
        try:
            payload = self._load_scheme_payload(Path(path))
        except (OSError, json.JSONDecodeError) as exc:
            message = self._ts("Could not read scheme: {error}").format(error=exc)
            QMessageBox.critical(self, self._ts("Validation Failed"), message)
            return

        validation = self._shortcuts.validate_scheme(payload, require_known=False)
        if validation.valid:
            summary = self._ts("Shortcut scheme is valid.")
            if validation.warnings:
                summary += "\n\n" + self._ts("Warnings:") + "\n"
                summary += self._format_messages(validation.warnings)
            QMessageBox.information(self, self._ts("Validation Result"), summary)
        else:
            summary = (
                self._ts("Shortcut scheme is invalid:")
                + "\n"
                + self._format_messages(validation.errors)
            )
            if validation.warnings:
                summary += "\n\n" + self._ts("Warnings:") + "\n"
                summary += self._format_messages(validation.warnings)
            QMessageBox.critical(self, self._ts("Validation Failed"), summary)

    @staticmethod
    def _load_scheme_payload(path: Path) -> Any:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)

    @staticmethod
    def _format_messages(messages: Sequence[str]) -> str:
        return "\n".join(f"- {message}" for message in messages)

    def _show_validation_messages(
        self,
        title: str,
        prefix: str,
        messages: Sequence[str],
        *,
        critical: bool,
    ) -> None:
        if not messages:
            QMessageBox.information(self, title, prefix)
            return
        body = f"{prefix}\n" + self._format_messages(messages)
        if critical:
            QMessageBox.critical(self, title, body)
        else:
            QMessageBox.information(self, title, body)

    def _selected_action(self) -> ShortcutAction | None:
        selected = self._table.currentItem()
        if not selected:
            return None
        action_id = selected.data(Qt.ItemDataRole.UserRole)
        if not isinstance(action_id, str):
            # Try the first column for the id
            action_item = self._table.item(selected.row(), 0)
            if not action_item:
                return None
            action_id = action_item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(action_id, str):
                return None
        for entry in self._visible_entries:
            if entry.action_id == action_id:
                return entry
        return None

    def _edit_selected(self) -> None:
        entry = self._selected_action()
        if entry is None:
            return
        mouse_definition = self._mouse_binding_definition(entry.action_id)
        if mouse_definition is not None:
            current_value = self._read_mouse_binding_value(mouse_definition)
            mouse_dialog = MouseBindingEditDialog(
                self._translate_shortcut_text(mouse_definition["label"]),
                self._mouse_binding_options(mouse_definition),
                current_value,
                self,
            )
            if mouse_dialog.exec() != QDialog.DialogCode.Accepted:
                return
            value = mouse_dialog.value() or mouse_definition["default"]
            self.settings.set(mouse_definition["setting_key"], value)
            self._refresh_table()
            return
        shortcut_dialog = ShortcutEditDialog(entry.label, entry.current, self)
        if shortcut_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        sequence = shortcut_dialog.sequence()
        self._shortcuts.set_shortcut(entry.action_id, sequence)

    def _reset_selected(self) -> None:
        entry = self._selected_action()
        if entry is None:
            return
        mouse_definition = self._mouse_binding_definition(entry.action_id)
        if mouse_definition is not None:
            self.settings.set(
                mouse_definition["setting_key"],
                mouse_definition["default"],
            )
            self._refresh_table()
            return
        self._shortcuts.reset_shortcut(entry.action_id)

    def _reset_all(self) -> None:
        if not self._entries:
            return
        confirm = QMessageBox.question(
            self,
            self._ts("Reset Shortcuts"),
            self._ts("Reset all shortcuts to their defaults?"),
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        # Block signals during batch reset to avoid N x _refresh_table()
        self._shortcuts.shortcut_changed.disconnect(self._on_shortcut_changed)
        try:
            for entry in self._entries:
                mouse_definition = self._mouse_binding_definition(entry.action_id)
                if mouse_definition is not None:
                    self.settings.set(
                        mouse_definition["setting_key"],
                        mouse_definition["default"],
                    )
                else:
                    self._shortcuts.reset_shortcut(entry.action_id)
        finally:
            self._shortcuts.shortcut_changed.connect(self._on_shortcut_changed)
        self._refresh_table()

    def _on_setting_changed(self, key: str, _value: object) -> None:
        if key in self._mouse_setting_keys:
            self._refresh_table()

    def _on_shortcut_changed(self, action_id: str, sequence: str) -> None:
        del sequence
        self._refresh_table()
        self.focus_action(action_id, reset_filters=False)


class ShortcutEditDialog(QDialog):
    """Small helper dialog that captures a key sequence."""

    _TITLE_TEMPLATE = cast(
        str,
        QT_TRANSLATE_NOOP("ShortcutEditDialog", "Change Shortcut - {label}"),
    )
    _PROMPT_TEMPLATE = cast(
        str,
        QT_TRANSLATE_NOOP(
            "ShortcutEditDialog",
            "Press the new shortcut for <b>{label}</b>.",
        ),
    )

    def __init__(
        self,
        label: str,
        current: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        layout = QVBoxLayout(self)
        self._prompt = QLabel()
        layout.addWidget(self._prompt)
        self._editor = QKeySequenceEdit()
        if current:
            self._editor.setKeySequence(
                QKeySequence.fromString(
                    current,
                    QKeySequence.SequenceFormat.PortableText,
                )
            )
        layout.addWidget(self._editor)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(
            translate_with_fallback("ShortcutEditDialog", self._TITLE_TEMPLATE).format(
                label=self._label
            )
        )
        self._prompt.setText(
            translate_with_fallback("ShortcutEditDialog", self._PROMPT_TEMPLATE).format(
                label=self._label
            )
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()

    def sequence(self) -> str | None:
        seq = self._editor.keySequence()
        text = seq.toString(QKeySequence.SequenceFormat.PortableText)
        return text or None


class MouseBindingEditDialog(QDialog):
    """Dialog for selecting a mouse gesture binding."""

    _TITLE_TEMPLATE = cast(
        str,
        QT_TRANSLATE_NOOP("MouseBindingEditDialog", "Change Binding - {label}"),
    )
    _PROMPT_TEMPLATE = cast(
        str,
        QT_TRANSLATE_NOOP(
            "MouseBindingEditDialog",
            "Select the new binding for <b>{label}</b>.",
        ),
    )

    def __init__(
        self,
        label: str,
        options: Sequence[tuple[str, str]],
        current_value: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        layout = QVBoxLayout(self)
        self._prompt = QLabel()
        layout.addWidget(self._prompt)
        self._combo = QComboBox()
        for option_label, option_value in options:
            self._combo.addItem(option_label, option_value)
        if current_value:
            index = self._combo.findData(current_value)
            if index >= 0:
                self._combo.setCurrentIndex(index)
        layout.addWidget(self._combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(
            translate_with_fallback("MouseBindingEditDialog", self._TITLE_TEMPLATE).format(
                label=self._label
            )
        )
        self._prompt.setText(
            translate_with_fallback("MouseBindingEditDialog", self._PROMPT_TEMPLATE).format(
                label=self._label
            )
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()

    def value(self) -> str:
        data = self._combo.currentData()
        return str(data) if data is not None else ""


# ======================================================================
# AI Effect Layer preferences tab
# ======================================================================


class AIEffectPage(PreferencesPage):
    """Preferences tab for AI Effect Layer defaults (Phase 6-f).

    Unlike the other pages this one does NOT write through
    :class:`SettingsManager`. AI Effect settings live in
    ``user_data/ai_effect/preferences.json`` and are loaded / saved via
    :class:`AIEffectPreferences`. ``collect_values()`` returns an empty dict
    so ``apply_callback`` receives no keys for this page; the side effect of
    :meth:`collect_values` is writing the preferences file directly.

    Changes take effect on the next app launch (the running
    ``AIEffectGenerationController`` caches its preferences reference at
    startup). Live application can be added later if needed.
    """

    _TRIGGER_CHOICES: tuple[tuple[str, str], ...] = (
        ("Manual", "manual"),
        ("On change", "on_change"),
        ("Debounced", "debounced"),
    )

    def __init__(self, settings: SettingsManager) -> None:
        super().__init__(settings)
        # Lazy import — keeps ai_effect plugin off the critical path when the
        # dialog is imported but never opened.
        from loomstroke.plugins.ai_effect.settings import (  # noqa: PLC0415
            AIEffectPreferences,
            default_preferences_path,
        )

        self._prefs_path = default_preferences_path()
        self._prefs: AIEffectPreferences = AIEffectPreferences.load(self._prefs_path)

        form = QFormLayout(self)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.trigger_combo = QComboBox()
        for label, value in self._TRIGGER_CHOICES:
            self.trigger_combo.addItem(self._ts(label), value)

        self.debounce_spin = QSpinBox()
        self.debounce_spin.setRange(0, 60_000)
        self.debounce_spin.setSuffix(self._ts(" ms"))

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 24 * 60 * 60)
        self.timeout_spin.setSuffix(self._ts(" sec"))

        self.cache_cap_spin = QSpinBox()
        self.cache_cap_spin.setRange(1, 500)
        self.cache_cap_spin.setSuffix(self._ts(" entries"))

        self.disable_cache_check = QCheckBox()
        self.disable_cache_row = self._build_checkbox_row(
            self.disable_cache_check,
            self._ts("Disable cache for manual regenerate"),
        )

        self.retry_on_change_spin = QSpinBox()
        self.retry_on_change_spin.setRange(0, 10)

        self.retry_delay_spin = QSpinBox()
        self.retry_delay_spin.setRange(0, 60_000)
        self.retry_delay_spin.setSuffix(self._ts(" ms"))

        self.first_gen_banner_check = QCheckBox()
        self.first_gen_banner_row = self._build_checkbox_row(
            self.first_gen_banner_check,
            self._ts("Show first-generation banner"),
        )

        # Phase 6-i: opt-in to record auto-fire generations in the
        # Gallery. Off by default — debounce/on_change typically
        # produce many probe runs the user does not want archived.
        self.register_auto_trigger_check = QCheckBox()
        self.register_auto_trigger_row = self._build_checkbox_row(
            self.register_auto_trigger_check,
            self._ts("Register on-change / debounced generations in Gallery"),
        )
        self.register_auto_trigger_check.setToolTip(
            self._ts(
                "When unchecked, only manually-triggered generations are saved to the "
                "Gallery. Auto-fired runs (on_change / debounced) skip Gallery registration "
                "to avoid cluttering history with intermediate probes."
            )
        )

        self._add_labeled_row(form, self._ts("Default trigger"), self.trigger_combo)
        self._add_labeled_row(form, self._ts("Debounce delay"), self.debounce_spin)
        self._add_labeled_row(form, self._ts("Generation timeout"), self.timeout_spin)
        self._add_labeled_row(form, self._ts("Cache entries per layer"), self.cache_cap_spin)
        form.addRow(self.disable_cache_row)
        self._add_labeled_row(
            form, self._ts("Auto-retries on 'on change' trigger"), self.retry_on_change_spin
        )
        self._add_labeled_row(form, self._ts("Retry delay"), self.retry_delay_spin)
        form.addRow(self.first_gen_banner_row)
        form.addRow(self.register_auto_trigger_row)

        from .comfyui_folders import ComfyUIFolders  # noqa: PLC0415

        self._comfyui_folders = ComfyUIFolders(self.settings, self)
        form.addRow(self._comfyui_folders)

        # Phase 10c-ui: ComfyUI Servers section. Preferences is the
        # single URL editor; panels only select from this list.
        self._comfyui_servers_group = self._build_comfyui_servers_section()
        form.addRow(self._comfyui_servers_group)

        # Phase 8a: API Keys section. Status indicator + Change /
        # Clear actions for paid Gateways and the LLM Gateway
        # providers (OpenAI / Anthropic / Gemini). Read-on-demand:
        # each row uses ``SecretProvider.has`` (bool only — never
        # decrypts the value) so opening Preferences does not pull
        # any key into memory.
        self._api_keys_group = self._build_api_keys_section()
        form.addRow(self._api_keys_group)

        self._load()

    # ------------------------------------------------------------------
    # ComfyUI Servers section (Phase 10c-ui)
    # ------------------------------------------------------------------

    def _build_comfyui_servers_section(self) -> QGroupBox:
        """Build the Preferences-owned ComfyUI server URL list."""
        group = QGroupBox()
        self._translation_binder.register(group.setTitle, self._ts("ComfyUI Servers"))
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(6)

        self._local_url_input = QLineEdit()
        self._local_url_input.setPlaceholderText("http://127.0.0.1:8188")
        self._translation_binder.register(
            self._local_url_input.setToolTip,
            self._ts(
                "URL of the ComfyUI server running on this machine. "
                "Leave blank to use http://127.0.0.1:8188."
            ),
        )

        local_row = QWidget(self)
        local_row_layout = QHBoxLayout(local_row)
        local_row_layout.setContentsMargins(0, 0, 0, 0)
        local_row_layout.setSpacing(8)
        local_label = QLabel(self._ts("Local URL"), local_row)
        self._translation_binder.register(local_label.setText, self._ts("Local URL"))
        local_label.setMinimumWidth(90)
        local_row_layout.addWidget(local_label)
        local_row_layout.addWidget(self._local_url_input, 1)
        layout.addWidget(local_row)

        self._external_servers_layout = QVBoxLayout()
        self._external_servers_layout.setContentsMargins(0, 0, 0, 0)
        self._external_servers_layout.setSpacing(6)
        layout.addLayout(self._external_servers_layout)
        self._external_url_rows: list[tuple[QWidget, QLineEdit, QLineEdit]] = []

        return group

    def _build_checkbox_row(self, checkbox: QCheckBox, text: str) -> QWidget:
        checkbox.setText("")
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignTop)
        label = QLabel(row)
        label.setWordWrap(True)
        self._translation_binder.register(label.setText, text)
        layout.addWidget(label, 1)
        return row

    def _add_external_url_row(self, name: str = "", url: str = "", server_id: str = "") -> None:
        row = QWidget(self)
        row.setProperty("server_id", server_id)
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        external_label = QLabel(self._ts("External URL"), row)
        self._translation_binder.register(external_label.setText, self._ts("External URL"))
        header_layout.addWidget(external_label)
        header_layout.addStretch(1)

        add_button = QPushButton("+", row)
        add_button.setFixedWidth(28)
        self._translation_binder.register(
            add_button.setToolTip, self._ts("Add external ComfyUI server")
        )
        add_button.clicked.connect(lambda: self._add_external_url_row())
        header_layout.addWidget(add_button)
        row_layout.addLayout(header_layout)

        name_input = QLineEdit(row)
        self._translation_binder.register(name_input.setPlaceholderText, self._ts("Name"))
        name_input.setText(name)
        row_layout.addWidget(name_input)

        url_input = QLineEdit(row)
        url_input.setPlaceholderText("http://192.168.0.10:8188")
        url_input.setText(url)
        self._translation_binder.register(
            url_input.setToolTip,
            self._ts("Optional URL of a remote ComfyUI server. Blank rows are ignored."),
        )
        row_layout.addWidget(url_input)

        self._external_servers_layout.addWidget(row)
        self._external_url_rows.append((row, name_input, url_input))

    # ------------------------------------------------------------------
    # API Keys section (paid Gateway credentials)
    # ------------------------------------------------------------------

    def _build_api_keys_section(self) -> QGroupBox:
        """Build a group box listing paid-Gateway API key credentials.

        Wires NanoBanana plus the three LLM Gateway providers
        (OpenAI / Anthropic / Gemini). All rows share the same
        ``SecretProvider`` entry point — only the ``(service,
        username)`` pair differs. Provider-specific help URLs link
        out to each vendor's "Get API key" page so the user does
        not have to hunt for it.
        """
        group = QGroupBox()
        self._translation_binder.register(group.setTitle, self._ts("API Keys"))
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(6)

        # Row identity is ``(service, username)``: NanoBanana stores
        # under ``("nanobanana", "api")``; LLM Gateway distinguishes
        # the three providers via the username, all under the
        # ``"llm_gateway"`` service.
        self._api_key_rows: dict[tuple[str, str], dict[str, Any]] = {}
        for service, username, display, help_url in self._api_key_row_specs():
            self._add_api_key_row(
                layout,
                service_name=service,
                username=username,
                display_name=display,
                help_url=help_url,
            )
        self.use_openai_key_for_gptimage2_check = QCheckBox()
        self.use_openai_key_for_gptimage2_row = self._build_checkbox_row(
            self.use_openai_key_for_gptimage2_check,
            self._ts("Use the OpenAI API key for GPT Image 2"),
        )
        self._translation_binder.register(
            self.use_openai_key_for_gptimage2_check.setToolTip,
            self._ts(
                "When enabled, GPT Image 2 generation uses the OpenAI API key row "
                "instead of the dedicated GPT Image 2 key."
            ),
        )
        self.use_openai_key_for_gptimage2_check.toggled.connect(
            lambda _checked: self._refresh_api_key_status(
                GPTIMAGE2_API_KEY_SERVICE,
                GPTIMAGE2_API_KEY_USERNAME,
            )
        )
        layout.addWidget(self.use_openai_key_for_gptimage2_row)
        return group

    @staticmethod
    def _api_key_row_specs() -> list[tuple[str, str, str, str]]:
        """``(service, username, display_name, help_url)``.

        Centralised so adding a future paid Gateway is a one-tuple
        change. ``service`` matches the keyring service name used
        by the corresponding Gateway plugin; ``username`` is the
        per-provider sub-key (``"api"`` when the Gateway only has
        one credential).
        """
        return [
            (
                "nanobanana",
                "api",
                "NanoBanana (Google Gemini)",
                "https://aistudio.google.com/app/api-keys",
            ),
            (
                "gptimage2",
                "api",
                "GPT Image 2 (OpenAI)",
                "https://platform.openai.com/api-keys",
            ),
            (
                "llm_gateway",
                "openai",
                "OpenAI",
                "https://platform.openai.com/api-keys",
            ),
            (
                "llm_gateway",
                "anthropic",
                "Anthropic (Claude)",
                "https://console.anthropic.com/settings/keys",
            ),
            (
                "llm_gateway",
                "gemini",
                "Google Gemini",
                "https://aistudio.google.com/app/api-keys",
            ),
        ]

    def _add_api_key_row(
        self,
        layout: QVBoxLayout,
        *,
        service_name: str,
        display_name: str,
        username: str = "api",
        help_url: str = "",
    ) -> None:
        row = QWidget(self)
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        info_layout = QHBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(8)

        name_label = QLabel(display_name, row)
        name_label.setWordWrap(True)
        info_layout.addWidget(name_label, 1)

        status_label = QLabel(row)
        status_label.setStyleSheet("color: #888888;")
        info_layout.addWidget(status_label)
        row_layout.addLayout(info_layout)

        change_btn = QPushButton(row)
        clear_btn = QPushButton(row)
        self._translation_binder.register(change_btn.setText, self._ts("Change…"))
        self._translation_binder.register(clear_btn.setText, self._ts("Clear"))
        change_btn.clicked.connect(lambda: self._on_change_api_key_clicked(service_name, username))
        clear_btn.clicked.connect(lambda: self._on_clear_api_key_clicked(service_name, username))
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addStretch(1)
        button_layout.addWidget(change_btn)
        button_layout.addWidget(clear_btn)
        row_layout.addLayout(button_layout)

        layout.addWidget(row)
        self._api_key_rows[(service_name, username)] = {
            "display_name": display_name,
            "help_url": help_url,
            "status_label": status_label,
            "change_btn": change_btn,
            "clear_btn": clear_btn,
        }
        self._refresh_api_key_status(service_name, username)

    def _gptimage2_uses_openai_key(self) -> bool:
        checkbox = getattr(self, "use_openai_key_for_gptimage2_check", None)
        return bool(checkbox is not None and checkbox.isChecked())

    def _refresh_api_key_status(self, service_name: str, username: str = "api") -> None:
        """Re-query ``SecretProvider.has`` and update the status
        label. Reads bool only — never decrypts the value."""
        info = self._api_key_rows.get((service_name, username))
        if info is None:
            return
        use_openai_for_gptimage2 = (
            service_name == GPTIMAGE2_API_KEY_SERVICE
            and username == GPTIMAGE2_API_KEY_USERNAME
            and self._gptimage2_uses_openai_key()
        )
        status_service = OPENAI_API_KEY_SERVICE if use_openai_for_gptimage2 else service_name
        status_username = OPENAI_API_KEY_USERNAME if use_openai_for_gptimage2 else username
        try:
            configured = SecretProvider().has(status_service, username=status_username)
        except Exception:
            configured = False
        change_btn = info.get("change_btn")
        clear_btn = info.get("clear_btn")
        if use_openai_for_gptimage2:
            if configured:
                info["status_label"].setText(self._ts("Using OpenAI key"))
                info["status_label"].setStyleSheet("color: #3dbb7e;")
            else:
                info["status_label"].setText(self._ts("OpenAI key not set"))
                info["status_label"].setStyleSheet("color: #b8872e;")
            if change_btn is not None:
                change_btn.setEnabled(False)
            if clear_btn is not None:
                clear_btn.setEnabled(False)
            return
        if change_btn is not None:
            change_btn.setEnabled(True)
        if configured:
            info["status_label"].setText(self._ts("✓ Configured"))
            info["status_label"].setStyleSheet("color: #3dbb7e;")
            if clear_btn is not None:
                clear_btn.setEnabled(True)
        else:
            info["status_label"].setText(self._ts("Not set"))
            info["status_label"].setStyleSheet("color: #888888;")
            if clear_btn is not None:
                clear_btn.setEnabled(False)

    def _on_change_api_key_clicked(self, service_name: str, username: str = "api") -> None:
        """Open the Set API key dialog and refresh status on close."""
        info = self._api_key_rows.get((service_name, username))
        if info is None:
            return
        from loomstroke.plugins.nanobanana.ui.api_key_dialog import (  # noqa: PLC0415
            show_set_api_key_dialog,
        )

        show_set_api_key_dialog(
            service_name=service_name,
            display_name=info["display_name"],
            help_url=info.get("help_url", ""),
            username=username,
            parent=self,
        )
        self._refresh_api_key_status(service_name, username)

    def _on_clear_api_key_clicked(self, service_name: str, username: str = "api") -> None:
        """Confirm + delete the credential, then refresh status."""
        info = self._api_key_rows.get((service_name, username))
        if info is None:
            return
        from loomstroke.plugins.nanobanana.ui.api_key_dialog import (  # noqa: PLC0415
            confirm_and_clear_api_key,
        )

        confirm_and_clear_api_key(
            service_name=service_name,
            display_name=info["display_name"],
            username=username,
            parent=self,
        )
        self._refresh_api_key_status(service_name, username)

    def _load(self) -> None:
        trigger_index = self.trigger_combo.findData(self._prefs.defaults.trigger)
        self.trigger_combo.setCurrentIndex(trigger_index if trigger_index >= 0 else 0)
        self.debounce_spin.setValue(int(self._prefs.defaults.debounce_ms))
        self.timeout_spin.setValue(int(self._prefs.defaults.timeout_sec))
        self.cache_cap_spin.setValue(int(self._prefs.cache.per_layer_cap))
        self.disable_cache_check.setChecked(bool(self._prefs.cache.disable_cache_for_manual_regen))
        self.retry_on_change_spin.setValue(int(self._prefs.retry.max_retries_on_change))
        self.retry_delay_spin.setValue(int(self._prefs.retry.retry_delay_ms))
        self.first_gen_banner_check.setChecked(bool(self._prefs.ui.show_first_gen_banner))
        self.register_auto_trigger_check.setChecked(
            bool(self._prefs.gallery.register_auto_trigger_results)
        )
        # Phase 10c-ui: ComfyUI Servers. Read from SettingsManager
        # rather than AIEffectPreferences. Local may stay blank in the
        # field; runtime falls back to the default ComfyUI URL.
        local_url = str(self._settings_get("comfyui.local_url", "") or "")
        self._local_url_input.setText(local_url)
        self._set_external_url_rows(self._load_external_server_entries())
        self.use_openai_key_for_gptimage2_check.setChecked(
            bool(self._settings_get(GPTIMAGE2_USE_OPENAI_API_KEY_SETTING, False))
        )
        self._refresh_api_key_status(GPTIMAGE2_API_KEY_SERVICE, GPTIMAGE2_API_KEY_USERNAME)

    def _settings_get(self, key: str, default: Any = None) -> Any:
        getter = getattr(getattr(self, "settings", None), "get", None)
        if not callable(getter):
            return default
        return getter(key, default)

    def _settings_set(self, key: str, value: Any) -> None:
        setter = getattr(getattr(self, "settings", None), "set", None)
        if callable(setter):
            setter(key, value)

    def _load_external_server_entries(self) -> list[dict[str, str]]:
        raw_entries = self._settings_get("comfyui.external_servers", [])
        entries: list[dict[str, str]] = []
        if isinstance(raw_entries, list):
            for entry in raw_entries:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "") or "")
                url = str(entry.get("url", "") or "")
                server_id = str(entry.get("id", "") or "")
                entries.append({"id": server_id, "name": name, "url": url})
        if not entries:
            legacy_url = str(self._settings_get("comfyui.external_url", "") or "")
            if legacy_url:
                entries.append({"id": "external", "name": "External", "url": legacy_url})
        return entries or [{"name": "", "url": ""}]

    def _set_external_url_rows(self, entries: list[dict[str, str]]) -> None:
        for row, _name_input, _url_input in self._external_url_rows:
            self._external_servers_layout.removeWidget(row)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._external_url_rows.clear()
        for entry in entries:
            self._add_external_url_row(
                entry.get("name", ""),
                entry.get("url", ""),
                entry.get("id", ""),
            )

    def _collect_external_server_entries(self) -> list[dict[str, str]]:
        from ...plugins.comfyui.server_registry import (  # noqa: PLC0415
            _allocate_external_server_id,
            _reserve_external_server_ids,
        )

        reserved = _reserve_external_server_ids(
            (row.property("server_id"), bool(url_input.text().strip()))
            for row, _name, url_input in self._external_url_rows
        )
        entries: list[dict[str, str]] = []
        for _row, name_input, url_input in self._external_url_rows:
            url = url_input.text().strip()
            if not url:
                continue
            index = len(entries)
            fallback_name = "External" if index == 0 else f"External {index + 1}"
            raw_server_id = _row.property("server_id")
            server_id = str(raw_server_id).strip() if raw_server_id else ""
            if not server_id:
                server_id = _allocate_external_server_id(index, reserved)
                _row.setProperty("server_id", server_id)
            entries.append(
                {
                    "id": server_id,
                    "name": name_input.text().strip() or fallback_name,
                    "url": url,
                }
            )
        return entries

    def _compose_legacy_local_url(self) -> str:
        """Build a URL from the legacy ``comfyui.host`` / ``port`` /
        ``use_ssl`` keys so users upgrading from pre-Phase-10c-ui see
        their existing endpoint in the new Local URL field on first
        open instead of an empty placeholder.
        """
        host = str(self._settings_get("comfyui.host", "127.0.0.1") or "127.0.0.1")
        port = int(self._settings_get("comfyui.port", 8188) or 8188)
        scheme = "https" if bool(self._settings_get("comfyui.use_ssl", False)) else "http"
        return f"{scheme}://{host}:{port}"

    def collect_values(self) -> dict[str, Any]:
        """AI Effect preferences are NOT routed through ``SettingsManager``;
        they're owned by :class:`AIEffectPreferences` and persisted by
        :meth:`save_settings`. Returning an empty dict keeps
        ``apply_callback`` a no-op for this page.
        """
        return {}

    def save_settings(self) -> None:
        """Copy widget values back into ``self._prefs`` and atomically
        persist the JSON file. Called by the dialog's ``_apply_changes``
        on every Apply / OK click."""
        trigger = self.trigger_combo.currentData() or "manual"
        self._prefs.defaults.trigger = str(trigger)
        self._prefs.defaults.debounce_ms = int(self.debounce_spin.value())
        self._prefs.defaults.timeout_sec = int(self.timeout_spin.value())
        self._prefs.cache.per_layer_cap = int(self.cache_cap_spin.value())
        self._prefs.cache.disable_cache_for_manual_regen = bool(
            self.disable_cache_check.isChecked()
        )
        self._prefs.retry.max_retries_on_change = int(self.retry_on_change_spin.value())
        self._prefs.retry.retry_delay_ms = int(self.retry_delay_spin.value())
        self._prefs.ui.show_first_gen_banner = bool(self.first_gen_banner_check.isChecked())
        self._prefs.gallery.register_auto_trigger_results = bool(
            self.register_auto_trigger_check.isChecked()
        )
        self._prefs_path.parent.mkdir(parents=True, exist_ok=True)
        self._prefs.save(self._prefs_path)
        # Phase 10c-ui: persist the ComfyUI Servers URLs through
        # SettingsManager. Whitespace-only inputs normalise to "" so
        # the registry seed at next launch treats them as "absent".
        self._settings_set("comfyui.local_url", self._local_url_input.text().strip())
        self._comfyui_folders.save()
        self._settings_set(
            GPTIMAGE2_USE_OPENAI_API_KEY_SETTING,
            bool(self.use_openai_key_for_gptimage2_check.isChecked()),
        )
        external_entries = self._collect_external_server_entries()
        self._settings_set("comfyui.external_servers", external_entries)
        self._settings_set(
            "comfyui.external_url",
            external_entries[0]["url"] if external_entries else "",
        )
        from loomstroke.plugins.comfyui.server_registry import (  # noqa: PLC0415
            sync_registry_from_settings,
        )

        sync_registry_from_settings(self.settings)

    def refresh_translations(self) -> None:
        super().refresh_translations()
        self.debounce_spin.setSuffix(self._ts(" ms"))
        self.timeout_spin.setSuffix(self._ts(" sec"))
        self.cache_cap_spin.setSuffix(self._ts(" entries"))
        self.retry_delay_spin.setSuffix(self._ts(" ms"))
        for i, (label, _value) in enumerate(self._TRIGGER_CHOICES):
            self.trigger_combo.setItemText(i, self._ts(label))
        for service_name, username in self._api_key_rows:
            self._refresh_api_key_status(service_name, username)
