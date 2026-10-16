"""Menu construction coordination for MainWindow."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMenuBar

from .menu_catalog import MENU_DEFINITIONS
from .selection_delete_shortcut import install_selection_delete_shortcut


class MainWindowMenuBuilderCoordinator:
    """Build MainWindow menubar and top-level menu contents."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def initialize_state(self) -> None:
        window = self._window
        window._window_menu = None
        window._tools_menu = None
        window._view_menu = None

    def menu_enabled(self, menu_id: str) -> bool:
        """Return True if the given taxonomy menu should be visible."""
        window = self._window
        definition = MENU_DEFINITIONS.get(menu_id)
        if definition is None:
            return True
        if window._show_experimental_menus:
            return True
        stored = window._settings.get(definition.setting_key)
        if stored is None:
            legacy_key = definition.setting_key.rsplit("/", 1)[0]
            stored = window._settings.get(legacy_key, definition.enabled_by_default)
        return bool(stored)

    def build_optional_menu(self, bar: QMenuBar, menu_id: str) -> QMenu | None:
        window = self._window
        definition = MENU_DEFINITIONS[menu_id]
        if not window._menu_enabled(menu_id):
            if menu_id == "tools":
                window._tools_menu = None
            window._menu_handles[menu_id] = None
            return None
        menu = cast(QMenu, window._menu(bar, definition.title))
        builder = getattr(window, definition.builder)
        builder(menu)
        if menu_id == "tools":
            window._tools_menu = menu
        if menu_id == "view":
            window._view_menu = menu
        window._menu_handles[menu_id] = menu
        return menu

    def build_view_menu(self, menu: QMenu) -> None:
        window = self._window
        menu.addAction(
            window._mk_action(
                "Zoom &In",
                window._on_zoom_in,
                "Ctrl+=",
                action_id="view.zoom_in",
                shortcut_category="View",
            )
        )
        menu.addAction(
            window._mk_action(
                "Zoom &Out",
                window._on_zoom_out,
                "Ctrl+-",
                action_id="view.zoom_out",
                shortcut_category="View",
            )
        )
        menu.addAction(
            window._mk_action(
                "&Reset Zoom",
                window._on_zoom_reset,
                "Ctrl+0",
                action_id="view.zoom_reset",
                shortcut_category="View",
            )
        )
        menu.addAction(
            window._mk_action(
                "&Fit Spread",
                window._on_fit_spread,
                # Not Ctrl+Shift+<digit>: Windows/IME input-mode hotkeys eat
                # those, and the key never reaches the app (reported 2026-07-28).
                "Ctrl+Shift+F",
                action_id="view.fit_spread",
                shortcut_category="View",
            )
        )
        menu.addSeparator()
        menu.addAction(
            window._mk_action(
                "Toggle &Grid",
                window._on_toggle_grid,
                "G",
                checkable=True,
                action_id="view.toggle_grid",
                shortcut_category="View",
            )
        )
        menu.addSeparator()
        # Session-only soft proof (CT-11 P2c): never persisted, never in export.
        menu.addAction(
            window._mk_action(
                "&Soft Proof",
                window._on_toggle_soft_proof,
                None,
                checkable=True,
                action_id="view.soft_proof",
                shortcut_category="View",
            )
        )
        menu.addAction(
            window._mk_action(
                "Choose Proof &Profile...",
                window._on_choose_proof_profile,
                None,
                action_id="view.soft_proof_profile",
                shortcut_category="View",
            )
        )
        panels_submenu = window._submenu(menu, "&Panels")
        self.populate_panels_menu(panels_submenu)

    def build_layer_menu(self, menu: QMenu) -> None:
        window = self._window
        menu.addAction(
            window._mk_action(
                "&New Layer",
                window._on_layer_new,
                "Ctrl+Shift+N",
                action_id="layer.new",
                shortcut_category="Layer",
            )
        )
        # CT-02: carrier raster + manga_screentone child effect from the selection.
        menu.addAction(
            window._mk_action(
                "New &Screentone Layer...",
                window._on_layer_new_screentone,
                action_id="layer.new_screentone",
                shortcut_category="Layer",
            )
        )
        menu.addAction(
            window._mk_action(
                "&Duplicate",
                window._on_layer_dup,
                "Ctrl+J",
                action_id="layer.duplicate",
                shortcut_category="Layer",
            )
        )
        delete_action = window._mk_action(
            "&Delete",
            window._on_layer_del,
            "Del",
            action_id="layer.delete",
            shortcut_category="Layer",
        )
        install_selection_delete_shortcut(delete_action, window)
        menu.addAction(delete_action)
        menu.addSeparator()
        menu.addAction(
            window._mk_action(
                "&Merge Down",
                window._on_layer_merge_down,
                "Ctrl+E",
                action_id="layer.merge_down",
                shortcut_category="Layer",
            )
        )
        menu.addAction(
            window._mk_action(
                "Merge &Visible",
                window._on_layer_merge_visible,
                "Ctrl+Shift+E",
                action_id="layer.merge_visible",
                shortcut_category="Layer",
            )
        )
        menu.addAction(
            window._mk_action(
                "&Flatten All",
                window._on_layer_flatten_all,
                "Ctrl+Alt+E",
                action_id="layer.flatten_all",
                shortcut_category="Layer",
            )
        )
        menu.addAction(
            window._mk_action(
                "Selection to &Mask",
                window._on_selection_to_mask,
                "",
                action_id="layer.selection_to_mask",
                shortcut_category="Layer",
            )
        )
        menu.addAction(
            window._mk_action(
                "&Group Layers",
                window._on_layer_group,
                "Ctrl+G",
                action_id="layer.group",
                shortcut_category="Layer",
            )
        )
        if group_action := menu.actions()[-1] if menu.actions() else None:
            group_action.setEnabled(False)
            group_action.setToolTip(window._ts("Not implemented"))
        arrange = window._submenu(menu, "&Arrange")
        arrange.addAction(window._mk_action("Bring to &Front", window._on_layer_arrange_to_front))
        arrange.addAction(window._mk_action("Bring &Forward", window._on_layer_arrange_forward))
        arrange.addAction(window._mk_action("Send &Backward", window._on_layer_arrange_backward))
        arrange.addAction(window._mk_action("Send to &Back", window._on_layer_arrange_to_back))
        menu.addSeparator()
        menu.addAction(
            window._mk_action(
                "&Rasterize",
                window._on_layer_rasterize,
                "Shift+R",
                action_id="layer.rasterize",
                shortcut_category="Layer",
            )
        )

    def build_filter_menu(self, menu: QMenu) -> None:
        window = self._window
        menu.addAction(
            window._mk_action(
                "&Last Filter",
                window._on_last_filter,
                "Ctrl+Alt+F",
                action_id="filter.last",
                shortcut_category="Filter",
            )
        )
        menu.addSeparator()
        self.populate_filter_menu(menu)
        # Phase 7-h-A: Installed AI Presets submenu — appears only when
        # the AI Effect subsystem has wired both the library and the
        # applier. Placed after the generic filter plugins so authored
        # presets sit below the built-in filters, matching the
        # preset_manager_final mockup (L831-833).
        window._populate_ai_presets_submenu(menu)
        # AI Effects Manager launcher — opens the Preset Manager
        # window which in turn provides "+ New" (Effect Editor) /
        # Import / Apply actions. Single entry point for preset CRUD
        # after the AI menu retirement (previously this lived under
        # a now-removed top-level AI menu).
        window._populate_ai_preset_manager_entry(menu)

    def build_tools_menu(self, menu: QMenu) -> None:
        window = self._window
        self.populate_tools_menu(menu)
        menu.addSeparator()
        menu.addAction(
            window._mk_action(
                "&Manage Tools...",
                window._open_tool_settings,
                action_id="tools.manage",
                shortcut_category="Tools",
            )
        )

    def build_plugins_menu(self, menu: QMenu) -> None:
        window = self._window
        menu.addAction(
            window._mk_action(
                "&Plugin Manager...",
                window._open_plugin_manager,
                action_id="plugins.manager",
                shortcut_category="Plugins",
            )
        )
        menu.addSeparator()
        window._populate_plugin_extra_menus(menu)

    def build_menubar(self) -> None:
        """Build comprehensive menu bar."""
        window = self._window
        bar = window.menuBar()
        if not bar:
            bar = QMenuBar(window)
            window.setMenuBar(bar)
        bar.clear()
        bar.setNativeMenuBar(False)
        bar.setVisible(True)

        # reset stored actions so rebuild picks up fresh QAction instances
        window._undo_action = None
        window._redo_action = None

        window._build_file_menu(bar)
        window._build_edit_menu(bar)
        window._build_image_menu(bar)
        window._build_window_and_view_menus(bar)
        window._build_help_menu(bar)

        window._update_history_actions()
        window._sync_tool_shortcut_bindings()

    def build_file_menu(self, bar: QMenuBar) -> None:
        window = self._window
        m_file = window._menu(bar, "&File")
        m_file.addAction(
            window._mk_action(
                "&New…",
                window._on_new,
                "Ctrl+N",
                action_id="file.new",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "&Open…",
                window._on_open,
                "Ctrl+O",
                action_id="file.open",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "&Import…",
                window._on_import,
                "Ctrl+Shift+I",
                action_id="file.import",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "Place Image…",
                window._on_place_image,
                None,
                action_id="file.place_image",
                shortcut_category="File",
            )
        )
        m_recent = window._submenu(m_file, "Open &Recent")
        self.populate_recent_menu(m_recent)
        m_file.addAction(
            window._mk_action(
                "&Close",
                window._on_close,
                "Ctrl+W",
                action_id="file.close",
                shortcut_category="File",
            )
        )
        m_file.addSeparator()
        m_file.addAction(
            window._mk_action(
                "&Save",
                window._on_save,
                "Ctrl+S",
                action_id="file.save",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "Save &As…",
                window._on_save_as,
                "Ctrl+Shift+S",
                action_id="file.save_as",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "&Export…",
                window._on_export,
                "Ctrl+E",
                action_id="file.export",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "Export Pages…",
                window._on_export_pages,
                None,
                action_id="file.export_pages",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "Export for Print…",
                window._on_export_for_print,
                None,
                action_id="file.export_for_print",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "Export Webtoon…",
                window._on_export_webtoon,
                None,
                action_id="file.export_webtoon",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "Export Digital Comic Package…",
                window._on_export_digital_package,
                None,
                action_id="file.export_digital_package",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "Manga Preflight…",
                window._on_manga_preflight,
                None,
                action_id="file.manga_preflight",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "Font Diagnostics…",
                window._on_font_diagnostics,
                None,
                action_id="file.font_diagnostics",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "Package Project…",
                window._on_package_project,
                None,
                action_id="file.package_project",
                shortcut_category="File",
            )
        )
        m_file.addAction(
            window._mk_action(
                "Document Mode…",
                window._on_document_mode,
                None,
                action_id="file.document_mode",
                shortcut_category="File",
            )
        )

        def _do_exit() -> None:
            window.close()

        m_file.addAction(
            window._mk_action(
                "E&xit",
                _do_exit,
                "Ctrl+Q",
                action_id="file.exit",
                shortcut_category="File",
            )
        )

    def build_window_and_view_menus(
        self,
        bar: QMenuBar,
    ) -> None:
        window = self._window
        window._view_menu = None
        for menu_id in ("view", "layer", "filter", "tools", "plugins"):
            window._build_optional_menu(bar, menu_id)

        window._window_menu = window._menu(bar, "&Window")
        window._build_window_menu()  # Ensure existing panel actions stay visible

    def build_edit_menu(self, bar: QMenuBar) -> None:
        window = self._window
        m_edit = window._menu(bar, "&Edit")
        window._undo_action = window._mk_action(
            "&Undo",
            window._on_undo,
            "Ctrl+Z",
            action_id="edit.undo",
            shortcut_category="Edit",
        )
        window._undo_action.setEnabled(False)
        m_edit.addAction(window._undo_action)
        window._redo_action = window._mk_action(
            "&Redo",
            window._on_redo,
            "Ctrl+Y",
            action_id="edit.redo",
            shortcut_category="Edit",
        )
        window._redo_action.setEnabled(False)
        m_edit.addAction(window._redo_action)
        m_edit.addSeparator()
        cut_action = window._mk_action(
            "Cu&t",
            window._on_cut,
            "Ctrl+X",
            action_id="edit.cut",
            shortcut_category="Edit",
        )
        cut_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        m_edit.addAction(cut_action)

        copy_action = window._mk_action(
            "&Copy",
            window._on_copy,
            "Ctrl+C",
            action_id="edit.copy",
            shortcut_category="Edit",
        )
        copy_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        m_edit.addAction(copy_action)

        paste_action = window._mk_action(
            "&Paste",
            window._on_paste,
            "Ctrl+V",
            action_id="edit.paste",
            shortcut_category="Edit",
        )
        paste_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        m_edit.addAction(paste_action)
        paste_system_action = window._mk_action(
            "Paste from &System Clipboard",
            window._on_paste_from_system_clipboard,
            "Ctrl+Shift+V",
            action_id="edit.paste_system",
            shortcut_category="Edit",
        )
        paste_system_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        m_edit.addAction(paste_system_action)
        m_edit.addSeparator()
        m_edit.addAction(
            window._mk_action(
                "Select &All",
                window._on_select_all,
                "Ctrl+A",
                action_id="edit.select_all",
                shortcut_category="Edit",
            )
        )
        m_edit.addAction(
            window._mk_action(
                "Select &All Vectors",
                window._on_select_all_vectors,
                "Ctrl+L",
                action_id="edit.select_all_vectors",
                shortcut_category="Edit",
            )
        )
        m_edit.addAction(
            window._mk_action(
                "&Deselect",
                window._on_deselect,
                "Ctrl+D",
                action_id="edit.deselect",
                shortcut_category="Edit",
            )
        )
        m_edit.addSeparator()
        m_edit.addAction(
            window._mk_action(
                "Canvas &Size...",
                window._on_canvas_resize,
                action_id="edit.canvas_size",
                shortcut_category="Edit",
            )
        )
        m_edit.addSeparator()
        m_edit.addAction(
            window._mk_action(
                "&Preferences…",
                window._on_preferences,
                "Ctrl+,",
                action_id="file.preferences",
                shortcut_category="File",
            )
        )

    def build_image_menu(self, bar: QMenuBar) -> None:
        """Build the Image menu (Flip / Rotate — Phase A Wave 1)."""
        window = self._window
        m_image = window._menu(bar, "&Image")
        m_image.addAction(
            window._mk_action(
                "Flip Canvas &Horizontal",
                window._on_image_flip_horizontal,
                action_id="image.flip_horizontal",
                shortcut_category="Image",
            )
        )
        m_image.addAction(
            window._mk_action(
                "Flip Canvas &Vertical",
                window._on_image_flip_vertical,
                action_id="image.flip_vertical",
                shortcut_category="Image",
            )
        )
        m_image.addSeparator()
        m_image.addAction(
            window._mk_action(
                "Rotate Canvas 90° &CW",
                window._on_image_rotate_cw,
                action_id="image.rotate_cw",
                shortcut_category="Image",
            )
        )
        m_image.addAction(
            window._mk_action(
                "Rotate Canvas 90° C&CW",
                window._on_image_rotate_ccw,
                action_id="image.rotate_ccw",
                shortcut_category="Image",
            )
        )
        m_image.addAction(
            window._mk_action(
                "Rotate Canvas &180°",
                window._on_image_rotate_180,
                action_id="image.rotate_180",
                shortcut_category="Image",
            )
        )
        m_image.addSeparator()
        # CT-13: pixel resample / DPI change in one dialog.  Distinct from
        # Edit > Canvas Size (crop / extend) and Image Properties (DPI only).
        m_image.addAction(
            window._mk_action(
                "Image Si&ze...",
                window._on_image_size,
                action_id="image.image_size",
                shortcut_category="Image",
            )
        )
        m_image.addAction(
            window._mk_action(
                "Image &Properties...",
                window._on_image_properties,
                action_id="image.properties",
                shortcut_category="Image",
            )
        )
        # CT-06 §6.1 wants this under a Document menu, but the app has no
        # Document menu; Image is where the other document-level dialog
        # (Image Properties) already lives.
        m_image.addAction(
            window._mk_action(
                "Comic &Book Settings...",
                window._on_comic_book_settings,
                action_id="image.comic_book_settings",
                shortcut_category="Image",
            )
        )

    def build_help_menu(self, bar: QMenuBar) -> None:
        window = self._window
        m_help = window._menu(bar, "&Help")
        m_help.addAction(window._mk_action("&About LoomStroke", window._about))

    def populate_recent_menu(self, menu: QMenu) -> None:
        """Populate recent files menu."""
        window = self._window
        menu.clear()
        if not window._recent_files:
            disabled = QAction("(No Recent Files)", window)
            window._translation_binder.register(disabled.setText, "(No Recent Files)")
            disabled.setEnabled(False)
            menu.addAction(disabled)
            return
        for path in window._recent_files[:10]:
            menu.addAction(
                window._mk_action(
                    path,
                    lambda checked=False, path=path: window._open_recent_file(path),
                )
            )

    def populate_panels_menu(self, menu: QMenu) -> None:
        """Populate panels menu from plugins (kept for View > Panels submenu)."""
        window = self._window
        menu.clear()
        info_action = QAction("(Panels are in Window menu)", window)
        window._translation_binder.register(
            info_action.setText,
            "(Panels are in Window menu)",
        )
        info_action.setEnabled(False)
        menu.addAction(info_action)

    def populate_filter_menu(self, menu: QMenu) -> None:
        """Populate filter menu from plugins."""
        window = self._window
        if not window._plugin_registry:
            return

        for plugin in window._plugin_registry.all():
            if plugin.__class__.__name__.endswith("Filter") or hasattr(plugin, "apply"):
                display_getter = getattr(plugin, "display_name", None)
                if callable(display_getter):
                    display_name = display_getter()
                else:
                    display_name = plugin.__class__.__name__
                menu.addAction(
                    window._mk_action(
                        display_name,
                        lambda chk=False, plugin=plugin: window._apply_filter(plugin),
                    )
                )

    def populate_tools_menu(self, menu: QMenu) -> None:
        """Populate tools menu from plugins."""
        window = self._window
        if not window._plugin_registry:
            return

        for plugin in window._plugin_registry.all():
            if hasattr(plugin, "descriptor") and hasattr(plugin, "build_commands"):
                desc = plugin.descriptor()
                owner = desc.shortcut_owner
                if not owner and "." in desc.id:
                    owner = desc.id.split(".", 1)[0]
                menu.addAction(
                    window._mk_action(
                        desc.display_name,
                        lambda chk=False, desc=desc: window._activate_tool(desc),
                        shortcut=desc.shortcut,
                        action_id=f"tools.{desc.id}",
                        shortcut_category=desc.shortcut_category or "Tools",
                        shortcut_description=desc.shortcut_description
                        or f"Activate {desc.display_name}",
                        shortcut_owner=owner,
                    )
                )
