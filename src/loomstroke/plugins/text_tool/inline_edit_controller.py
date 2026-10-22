"""Inline text editing state owner for ``TextTool``."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QInputMethodEvent, QKeySequence, QMouseEvent, QTextCursor, QTransform
from PySide6.QtWidgets import QMenu, QPlainTextEdit, QWidget

from loomstroke.core.text_edit_geometry import (
    TextEditGeometry,
    build_text_edit_geometry,
    python_index_from_utf16,
    utf16_index_from_python,
)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("TextTool", text, use_fallback=True)


@dataclass(slots=True)
class InlineEditState:
    layer_id: str | None
    object_id: str | None
    object_ref: Any | None
    original_text: str
    rect: tuple[float, float, float, float]
    word_wrap: bool
    rotation_deg: float
    is_new: bool
    created_layer_id: str | None = None
    metadata_history: Any | None = None


class InlineTextEditor(QPlainTextEdit):
    commit_requested = Signal()
    cancel_requested = Signal()
    preedit_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("loomstroke_inline_text_editor", True)
        self._preedit_text = ""
        self._preedit_start = 0
        self._preedit_replace_length = 0
        self._preedit_cursor_offset = 0
        self._vertical_layout_enabled = False
        self._vertical_flow = "left"
        self._vertical_align = "left"
        self._vertical_line_height = 1.0
        self._vertical_drag_anchor = None
        self._vertical_dragging = False
        self._h_drag_anchor: int | None = None
        self._h_dragging = False
        self._handle_hit_test: Any = None
        self._hover_leave_callback: Callable[[], None] | None = None
        self._forwarding_to_canvas = False
        self._text_layout_payload: dict[str, Any] | None = None
        self._text_layout_zoom = 1.0
        self._text_layout_origin = (0.0, 0.0)
        self._frame_to_editor: QTransform | None = None
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def set_vertical_text_layout(
        self,
        *,
        enabled: bool,
        vertical_flow: str = "left",
        align: str = "left",
        line_height: float = 1.0,
    ) -> None:
        self._vertical_layout_enabled = bool(enabled)
        self._vertical_flow = str(vertical_flow or "left")
        self._vertical_align = str(align or "left")
        try:
            self._vertical_line_height = max(0.5, float(line_height))
        except (TypeError, ValueError):
            self._vertical_line_height = 1.0
        if not self._vertical_layout_enabled:
            self._vertical_drag_anchor = None
            self._vertical_dragging = False

    def set_text_layout_payload(
        self,
        payload: Mapping[str, Any],
        *,
        zoom: float = 1.0,
        origin_offset: tuple[float, float] | None = None,
        frame_to_editor: QTransform | None = None,
    ) -> None:
        """Accept canvas-pixel metadata; DPR is handled by Qt, not text layout."""
        self._text_layout_payload = dict(payload)
        self._text_layout_zoom = max(0.01, float(zoom))
        if origin_offset is not None:
            self._text_layout_origin = origin_offset
        if frame_to_editor is not None:
            self._frame_to_editor = QTransform(frame_to_editor)

    def _edit_geometry(self, content: str | None = None) -> TextEditGeometry:
        payload = self._text_layout_payload
        fallback_font = None
        if payload is None:
            rect = self.contentsRect()
            fallback_font = self.font()
            payload = {
                "frame": {"x": 0.0, "y": 0.0, "width": rect.width(), "height": rect.height()},
                "vertical": self._vertical_layout_enabled,
                "vertical_flow": self._vertical_flow,
                "align": self._vertical_align,
                "line_height": self._vertical_line_height,
                "tracking": fallback_font.letterSpacing(),
                "layout": {
                    "wrap_mode": (
                        "none"
                        if self.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
                        else "word"
                    )
                },
            }
        return build_text_edit_geometry(
            payload,
            self.toPlainText() if content is None else content,
            zoom=self._text_layout_zoom,
            font=fallback_font,
            origin_offset=self._text_layout_origin,
            frame_to_editor=self._frame_to_editor,
        )

    def _canvas_text_index_from_point(self, point: Any) -> int:
        content = self.toPlainText()
        index = self._edit_geometry(content).index_at(QPointF(point.x(), point.y()))
        return utf16_index_from_python(content, index)

    def _horizontal_text_index_from_point(self, point: Any) -> int:
        return self._canvas_text_index_from_point(point)

    def _vertical_text_index_from_point(self, point: Any) -> int:
        return self._canvas_text_index_from_point(point)

    def _forward_event_to_parent(self, event) -> None:  # type: ignore[override]
        """Re-post a mouse event to the parent widget in parent-local coords."""
        parent = self.parentWidget()
        if parent is None:
            return
        mapped = self.mapToParent(event.position())
        forwarded = QMouseEvent(
            event.type(),
            QPointF(mapped.x(), mapped.y()),
            event.globalPosition(),
            event.button(),
            event.buttons(),
            event.modifiers(),
        )
        QCoreApplication.sendEvent(parent, forwarded)

    def _index_from_point(self, point: Any) -> int:
        """Map a widget-local click to a text index using canvas-compatible metrics."""
        if self._vertical_layout_enabled:
            return self._vertical_text_index_from_point(point)
        return self._horizontal_text_index_from_point(point)

    def _is_near_edge(self, pos: Any, margin: float = 8.0) -> bool:
        """Return True if *pos* is within *margin* px of any widget edge."""
        px = float(getattr(pos, "x", lambda: 0.0)())
        py = float(getattr(pos, "y", lambda: 0.0)())
        w = float(self.width())
        h = float(self.height())
        return px < margin or py < margin or px > w - margin or py > h - margin

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and callable(self._handle_hit_test):
            pos = event.position()
            if self._handle_hit_test(pos.x(), pos.y()):
                self._forwarding_to_canvas = True
                self._forward_event_to_parent(event)
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._index_from_point(event.position())
            self._h_drag_anchor = idx
            self._h_dragging = True
            cursor = self.textCursor()
            cursor.setPosition(idx)
            self.setTextCursor(cursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._forwarding_to_canvas:
            self._forward_event_to_parent(event)
            event.accept()
            return
        if self._h_dragging and event.buttons() & Qt.MouseButton.LeftButton:
            anchor = self._h_drag_anchor
            if anchor is None:
                anchor = self._index_from_point(event.position())
                self._h_drag_anchor = anchor
            target = self._index_from_point(event.position())
            cursor = self.textCursor()
            cursor.setPosition(anchor)
            cursor.setPosition(target, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
            event.accept()
            return
        if event.buttons() == Qt.MouseButton.NoButton:
            self._forward_event_to_parent(event)
            parent = self.parentWidget()
            if parent is not None and hasattr(parent, "viewport"):
                self.viewport().setCursor(parent.cursor())
        super().mouseMoveEvent(event)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        super().enterEvent(event)
        if self._h_dragging or self._forwarding_to_canvas:
            return
        hover = QMouseEvent(
            QEvent.Type.MouseMove,
            self.mapFromGlobal(event.globalPosition()),
            event.globalPosition(),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self._forward_event_to_parent(hover)
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "viewport"):
            self.viewport().setCursor(parent.cursor())

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._forwarding_to_canvas:
            self._forwarding_to_canvas = False
            self._forward_event_to_parent(event)
            event.accept()
            return
        if self._h_dragging and event.button() == Qt.MouseButton.LeftButton:
            self._h_dragging = False
            self._h_drag_anchor = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self._hover_leave_callback is not None:
            self._hover_leave_callback()
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.StandardKey.Undo) or event.matches(
            QKeySequence.StandardKey.Redo
        ):
            super().keyPressEvent(event)
            return
        key = event.key()
        mods = event.modifiers()
        if key == int(Qt.Key.Key_Escape):
            self.commit_requested.emit()
            event.accept()
            return
        if key in (int(Qt.Key.Key_Return), int(Qt.Key.Key_Enter)) and mods & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        ):
            self.commit_requested.emit()
            event.accept()
            return
        if self._vertical_layout_enabled and key in (
            int(Qt.Key.Key_Up),
            int(Qt.Key.Key_Down),
            int(Qt.Key.Key_Left),
            int(Qt.Key.Key_Right),
        ):
            if not mods & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            ):
                cursor = self.textCursor()
                move_mode = (
                    QTextCursor.MoveMode.KeepAnchor
                    if mods & Qt.KeyboardModifier.ShiftModifier
                    else QTextCursor.MoveMode.MoveAnchor
                )
                if key in (int(Qt.Key.Key_Up), int(Qt.Key.Key_Down)):
                    operation = (
                        QTextCursor.MoveOperation.PreviousCharacter
                        if key == int(Qt.Key.Key_Up)
                        else QTextCursor.MoveOperation.NextCharacter
                    )
                    cursor.movePosition(operation, move_mode)
                else:
                    content = self.toPlainText()
                    index = python_index_from_utf16(content, cursor.position())
                    direction = -1 if key == int(Qt.Key.Key_Left) else 1
                    target = self._edit_geometry(content).adjacent_column_index(index, direction)
                    cursor.setPosition(utf16_index_from_python(content, target), move_mode)
                self.setTextCursor(cursor)
                event.accept()
                return
            from PySide6.QtGui import QKeyEvent  # noqa: PLC0415

            remap = {
                int(Qt.Key.Key_Up): Qt.Key.Key_Left,
                int(Qt.Key.Key_Down): Qt.Key.Key_Right,
                int(Qt.Key.Key_Left): Qt.Key.Key_Down,
                int(Qt.Key.Key_Right): Qt.Key.Key_Up,
            }
            new_key = remap[key]
            remapped = QKeyEvent(event.type(), int(new_key), mods, event.text())
            super().keyPressEvent(remapped)
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            super().keyPressEvent(event)
        finally:
            cursor.endEditBlock()

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        super().focusOutEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = QMenu(self)
        menu.addAction(_ts("Cut"), self.cut)
        menu.addAction(_ts("Copy"), self.copy)
        menu.addAction(_ts("Paste"), self.paste)
        menu.addSeparator()
        menu.addAction(_ts("Select All"), self.selectAll)
        menu.exec(event.globalPos())
        event.accept()

    @staticmethod
    def _preedit_cursor_from_event(event: QInputMethodEvent, fallback: int) -> int:
        with suppress(Exception):
            for attribute in event.attributes():
                attr_type = getattr(attribute, "type", None)
                if attr_type != QInputMethodEvent.AttributeType.Cursor:
                    continue
                return int(getattr(attribute, "start", fallback))
        return int(fallback)

    def inputMethodEvent(self, event) -> None:  # type: ignore[override]
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            super().inputMethodEvent(event)
        finally:
            cursor.endEditBlock()
        self.record_preedit(event)

    def record_preedit(self, event):
        """Share IME preview state with another view of this text document."""
        content = self.toPlainText()
        cursor_pos = python_index_from_utf16(content, self.textCursor().position())
        preedit_text = str(event.preeditString() or "")
        if preedit_text:
            preedit_cursor = self._preedit_cursor_from_event(event, fallback=2 * len(preedit_text))
            self._preedit_text = preedit_text
            self._preedit_start = cursor_pos
            # QPlainTextEdit has already applied commit/replacement to its document.
            self._preedit_replace_length = 0
            self._preedit_cursor_offset = python_index_from_utf16(preedit_text, preedit_cursor)
        else:
            self._preedit_text = ""
            self._preedit_start = cursor_pos
            self._preedit_replace_length = 0
            self._preedit_cursor_offset = 0
        self.preedit_changed.emit()

    def inputMethodQuery(self, query):  # type: ignore[override]
        if query == Qt.InputMethodQuery.ImEnabled:
            return True
        if query == Qt.InputMethodQuery.ImCursorPosition:
            return int(self.textCursor().position())
        if query == Qt.InputMethodQuery.ImAnchorPosition:
            return int(self.textCursor().anchor())
        if query == Qt.InputMethodQuery.ImCursorRectangle:
            content = self.toPlainText()
            index = python_index_from_utf16(content, self.textCursor().position())
            if self._preedit_text:
                start = max(0, min(len(content), self._preedit_start))
                content = content[:start] + self._preedit_text + content[start:]
                index = start + self._preedit_cursor_offset
            return self._edit_geometry(content).caret_rect(index).toAlignedRect()
        return super().inputMethodQuery(query)

    def preedit_text(self) -> str:
        return str(self._preedit_text)

    def preedit_start(self) -> int:
        return int(self._preedit_start)

    def preedit_replace_length(self) -> int:
        return int(self._preedit_replace_length)

    def preedit_cursor_offset(self) -> int:
        return int(self._preedit_cursor_offset)


@dataclass(slots=True)
class InlineFinishContext:
    editor: Any
    state: Any
    text_value: str


class InlineTextEditController:
    """Own the active inline editor, inline state, and signal boundary."""

    def __init__(
        self,
        *,
        editor_factory: Callable[[QWidget], Any],
        state_factory: Callable[..., Any],
        force_transparent_editor: Callable[[Any], None],
        logger: logging.Logger,
    ) -> None:
        self._editor_factory = editor_factory
        self._state_factory = state_factory
        self._force_transparent_editor = force_transparent_editor
        self._logger = logger
        self.editor: Any | None = None
        self.state: Any | None = None
        self.canvas_widget: QWidget | None = None
        self.finishing = False
        self.shape_preview_active = False
        self.hover_handles = False
        self._sync_geometry: Callable[..., None] | None = None

    def start(
        self,
        *,
        initial_text: str,
        rect: tuple[float, float, float, float],
        word_wrap: bool,
        layer_id: str | None,
        object_id: str | None,
        object_ref: Any | None,
        rotation_deg: float,
        is_new: bool,
        canvas_widget: QWidget,
        on_text_changed: Callable[[], None],
        on_cursor_changed: Callable[[], None],
        on_preedit_changed: Callable[[], None],
        finish_requested: Callable[[bool], None],
        sync_geometry: Callable[..., None],
        make_handle_hit_test: Callable[[QWidget], Any],
        update_selection_overlay: Callable[..., None],
        update_text_preview: Callable[[], None],
        request_ime_update: Callable[[], None],
        begin_edit_transaction: Callable[[], None],
        emit_text_editing: Callable[[], None],
    ) -> bool:
        self.shape_preview_active = False
        self.hover_handles = False
        self._sync_geometry = sync_geometry

        editor = self._editor_factory(canvas_widget)
        editor.setPlainText(initial_text)
        editor.setPlaceholderText("")
        editor.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        editor.setTabChangesFocus(False)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setFrameStyle(0)
        self._force_transparent_editor(editor)
        editor.textChanged.connect(on_text_changed)
        editor.cursorPositionChanged.connect(on_cursor_changed)
        editor.selectionChanged.connect(on_cursor_changed)
        editor.preedit_changed.connect(on_preedit_changed)
        editor.commit_requested.connect(lambda: finish_requested(True))
        editor.cancel_requested.connect(lambda: finish_requested(False))
        editor.undoAvailable.connect(self._refresh_history_actions)
        editor.redoAvailable.connect(self._refresh_history_actions)

        self.editor = editor
        self.canvas_widget = canvas_widget
        self.state = self._state_factory(
            layer_id=layer_id,
            object_id=object_id,
            object_ref=object_ref,
            original_text=initial_text,
            rect=rect,
            word_wrap=bool(word_wrap),
            rotation_deg=float(rotation_deg),
            is_new=bool(is_new),
        )

        self._connect_canvas_signals(canvas_widget)
        editor._handle_hit_test = make_handle_hit_test(canvas_widget)
        sync_geometry()
        update_selection_overlay(preview=True)
        update_text_preview()
        editor.show()
        # Floating context toolbars are separate windows. setFocus alone only
        # records the editor's future focus while the toolbar stays active.
        editor.window().activateWindow()
        editor.setFocus()
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        editor.setTextCursor(cursor)
        request_ime_update()
        begin_edit_transaction()
        emit_text_editing()
        self._refresh_history_actions()
        return True

    def _refresh_history_actions(self, *_args) -> None:
        if self.canvas_widget is None:
            return
        refresh = getattr(self.canvas_widget.window(), "_update_history_actions", None)
        if callable(refresh):
            refresh()

    def take_finish_context(self) -> InlineFinishContext | None:
        if self.finishing:
            return None
        editor = self.editor
        state = self.state
        if editor is None or state is None:
            self.shape_preview_active = False
            return None

        self.finishing = True
        text_value = editor.toPlainText()
        self.disconnect_canvas_signals()
        try:
            editor.hide()
            editor.deleteLater()
        except Exception:
            self._logger.debug("Inline text editor dispose failed", exc_info=True)
        self.editor = None
        self._refresh_history_actions()
        self.canvas_widget = None
        self.state = None
        self.finishing = False
        self.hover_handles = False
        return InlineFinishContext(editor=editor, state=state, text_value=text_value)

    def set_shape_preview_active(self, active: bool) -> bool:
        active_flag = bool(active)
        if self.shape_preview_active == active_flag:
            return False
        self.shape_preview_active = active_flag
        return True

    def disconnect_canvas_signals(self) -> None:
        canvas_widget = self.canvas_widget
        sync_geometry = self._sync_geometry
        if canvas_widget is None or sync_geometry is None:
            return
        viewport_changed = getattr(canvas_widget, "viewport_changed", None)
        if hasattr(viewport_changed, "disconnect"):
            with suppress(Exception):
                viewport_changed.disconnect(sync_geometry)
        canvas_resized = getattr(canvas_widget, "canvas_resized", None)
        if hasattr(canvas_resized, "disconnect"):
            with suppress(Exception):
                canvas_resized.disconnect(sync_geometry)
        self._sync_geometry = None

    def _connect_canvas_signals(self, canvas_widget: QWidget) -> None:
        sync_geometry = self._sync_geometry
        if sync_geometry is None:
            return
        viewport_changed = getattr(canvas_widget, "viewport_changed", None)
        if hasattr(viewport_changed, "connect"):
            try:
                viewport_changed.connect(sync_geometry)
            except Exception:
                self._logger.debug("TextTool viewport_changed connect failed", exc_info=True)
        canvas_resized = getattr(canvas_widget, "canvas_resized", None)
        if hasattr(canvas_resized, "connect"):
            try:
                canvas_resized.connect(sync_geometry)
            except Exception:
                self._logger.debug("TextTool canvas_resized connect failed", exc_info=True)

    def discard_empty_created_layer(self, canvas: Any, layer_id: str | None) -> None:
        """Release only this edit's unused provisional text layer."""
        if canvas is None or layer_id is None:
            return
        getter = getattr(canvas, "get_vector_objects", None)
        remover = getattr(canvas, "remove_layer", None)
        if not callable(getter) or not callable(remover):
            return
        try:
            if not any(lid == layer_id for lid, _ in getter(include_hidden=True)):
                remover(layer_id)
        except Exception:
            self._logger.exception("Could not release provisional text layer %s", layer_id)
