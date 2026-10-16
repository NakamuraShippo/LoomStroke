"""Candidate-aware Undo/Redo action presentation (CT-05 Phase H5c).

Turns the routed ``HistoryRouteCandidate`` (already selected by the H5a
router, surfaced through ``MinimalHistoryService``'s internal peeks)
into localized QAction presentation: text, statusTip, toolTip, and
whatsThis, e.g. ``&Undo Pen Stroke — Page 12``.

Boundaries:

- MainWindow never touches ``DocumentHistoryRouter`` directly — all
  candidate metadata flows through ``window._host_api.history``'s
  internal read-only surface, with graceful degradation to the generic
  ``&Undo`` / ``&Redo`` when anything is unavailable.
- Only stable source metadata is kept (label keys, source labels, page
  numbers, custom page names); every refresh re-translates through the
  MainWindow domain, so a runtime language switch just needs a refresh.
- Labels are applied through ``window._set_action_label`` so the custom
  shortcut badge, shortcut, and tooltip metadata stay intact, and the
  Edit-menu / canvas-context-menu shared QAction picks the text up
  automatically.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from PySide6.QtWidgets import QToolButton

from loomstroke.ui.inline_text_history_target import inline_text_history_target
from loomstroke.ui.translations import translate_with_fallback

logger = logging.getLogger(__name__)

_DOMAIN = "MainWindow"

# Stable book label_key -> translatable operation source text.  Unknown
# keys fall back to a localized generic instead of leaking the raw key.
_BOOK_OPERATION_LABELS: dict[str, str] = {
    "page.add": "Add Page",
    "page.duplicate": "Duplicate Page",
    "page.delete": "Delete Page",
    "page.rename": "Rename Page",
    "page.move": "Move Page",
    "book.layout_changed": "Book Layout",
    "page.book_attributes_changed": "Page Attributes",
}
_UNKNOWN_OPERATION_LABEL = "Page Operation"

_SCOPE_LABELS: dict[str, str] = {
    "content": "Page Content",
    "book": "Page Structure",
}

_ACTION_TEMPLATES = {
    "undo": "&Undo {operation} — {page}",
    "redo": "&Redo {operation} — {page}",
}
_GENERIC_ACTION_TEXT = {"undo": "&Undo", "redo": "&Redo"}
_TIP_TEMPLATES = {
    "undo": "Undo {operation} on {page} ({scope})",
    "redo": "Redo {operation} on {page} ({scope})",
}

# Built-in content history group labels shipped by core/built-in tools.
# Used only to seed the translation catalogs — formatting passes any
# source label through the same lookup, and unknown labels simply render
# as their own source text.
KNOWN_CONTENT_LABELS: tuple[str, ...] = (
    "Pen Stroke",
    "Eraser Stroke",
    "Paint Stroke",
    "Selection",
    "Fill",
    "Pattern Fill",
    "Gradient",
    "Move",
    "Transform",
    "Cut",
    "Paste",
    "Merge Down",
    "Merge Visible",
    "Flatten All",
    "Image Size",
    "Create Screentone",
    "Layer Visibility",
    "Text",
    "Shape",
)


def _translate(source_text: str) -> str:
    return translate_with_fallback(_DOMAIN, source_text)


def _escape_mnemonic(text: str) -> str:
    """Escape ``&`` so user strings never become accidental mnemonics."""
    return text.replace("&", "&&")


# Qt-style parenthetical mnemonic token, e.g. ``(&U)`` — used by the JA
# templates so the mnemonic letter doesn't collide with kanji/kana. Only
# matches a single non-"&"/non-whitespace/non-")" character between the
# ampersand and the closing paren, so ordinary parenthetical text (no
# "&" immediately inside) is never touched.
_MNEMONIC_PAREN_RE = re.compile(r"\(&[^&\s)]\)")


def _plain_text(text: str) -> str:
    """Strip Qt mnemonic markers for accessibility names.

    ``&&`` is an escaped literal ampersand (from user strings via
    ``_escape_mnemonic``) and collapses to a single ``&``. A
    parenthetical mnemonic token, e.g. ``(&U)``, is removed as a whole
    unit. A bare mnemonic marker, e.g. ``&Undo``, has only the ``&``
    stripped, keeping the letter.
    """
    placeholder = "\x00"
    escaped = text.replace("&&", placeholder)
    without_paren_mnemonic = _MNEMONIC_PAREN_RE.sub("", escaped)
    without_bare_mnemonic = without_paren_mnemonic.replace("&", "")
    return without_bare_mnemonic.replace(placeholder, "&")


class HistoryActionPresenter:
    """Apply candidate-aware presentation to the shared Undo/Redo actions."""

    def __init__(self, window: Any) -> None:
        self._window = window

    # ------------------------------------------------------------------
    # Candidate access (via HostAPI.history internal surface only)
    def _history_service(self) -> Any | None:
        host = getattr(self._window, "_host_api", None)
        return getattr(host, "history", None) if host is not None else None

    @staticmethod
    def _peek_candidate(history: Any | None, method_name: str) -> Any | None:
        peek = getattr(history, method_name, None)
        if not callable(peek):
            return None
        try:
            return peek()
        except Exception:
            logger.debug("History candidate peek failed (%s)", method_name, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Label building (stable metadata -> translated presentation)
    def _operation_label(self, candidate: Any) -> str:
        if getattr(candidate, "scope", None) == "book":
            source = _BOOK_OPERATION_LABELS.get(
                getattr(candidate, "label_key", None) or "",
                _UNKNOWN_OPERATION_LABEL,
            )
            return _translate(source)
        source_label = str(getattr(candidate, "source_label", "") or "").strip()
        if not source_label:
            return _translate(_UNKNOWN_OPERATION_LABEL)
        # Known built-in labels translate; unknown plugin labels come
        # back unchanged from the catalog lookup and render as-is.
        return _translate(source_label)

    def _page_label(self, candidate: Any, history: Any | None) -> str:
        if getattr(candidate, "scope", None) == "book":
            affected = getattr(candidate, "affected_page_ids", ()) or ()
            if not affected:
                return _translate("Document")
            page_id = affected[0]
        else:
            page_id = getattr(candidate, "active_page_id", None)
        context = None
        resolve = getattr(history, "_resolve_history_page_context", None)
        if page_id and callable(resolve):
            try:
                context = resolve(page_id)
            except Exception:
                logger.debug("History page context lookup failed", exc_info=True)
        if context is not None:
            custom_name = getattr(context, "custom_name", None)
            if custom_name:
                return str(custom_name)  # user string: never translated
            number = getattr(context, "one_based_index", None)
            if isinstance(number, int) and number > 0:
                return _translate("Page {number}").format(number=number)
        # Raw page ids never reach the UI.
        return _translate("Page")

    # ------------------------------------------------------------------
    # Application
    def refresh_labels(self) -> None:
        """Re-derive and apply Undo/Redo presentation from current state.

        Called on every history state change, after menu rebuilds, and
        after runtime language switches.  Fully defensive: windows
        without the shortcut-label surface (lightweight test doubles)
        are left untouched.
        """
        window = self._window
        set_label = getattr(window, "_set_action_label", None)
        if not callable(set_label):
            return
        undo_action = getattr(window, "_undo_action", None)
        redo_action = getattr(window, "_redo_action", None)
        if undo_action is None and redo_action is None:
            return
        history = self._history_service()
        inline_editing = inline_text_history_target(window) is not None
        if undo_action is not None:
            # While a tool-specific Undo is available it wins the actual
            # dispatch, and ToolManager exposes no operation metadata —
            # showing the document candidate would mislabel the action.
            if inline_editing or bool(getattr(window, "_tool_can_undo", False)):
                candidate = None
            else:
                candidate = self._peek_candidate(history, "_peek_undo_candidate")
            self._apply(undo_action, "edit.undo", "undo", candidate, history)
        if redo_action is not None:
            candidate = (
                None if inline_editing else self._peek_candidate(history, "_peek_redo_candidate")
            )
            self._apply(redo_action, "edit.redo", "redo", candidate, history)

    def _apply(
        self,
        action: Any,
        action_id: str,
        direction: str,
        candidate: Any | None,
        history: Any | None,
    ) -> None:
        window = self._window
        if candidate is None:
            # Generic fallback — and no stale candidate metadata on the
            # (possibly disabled) action.
            text = _translate(_GENERIC_ACTION_TEXT[direction])
            tip = ""
        else:
            operation = self._operation_label(candidate)
            page = self._page_label(candidate, history)
            text = _translate(_ACTION_TEMPLATES[direction]).format(
                operation=_escape_mnemonic(operation),
                page=_escape_mnemonic(page),
            )
            scope_source = _SCOPE_LABELS.get(
                getattr(candidate, "scope", None) or "", _UNKNOWN_OPERATION_LABEL
            )
            tip = _translate(_TIP_TEMPLATES[direction]).format(
                operation=operation,
                page=page,
                scope=_translate(scope_source),
            )
        # _set_action_label stores the base label so the custom shortcut
        # badge and later metadata refreshes stay correct.
        window._set_action_label(action, action_id, text)
        action.setStatusTip(tip)
        action.setWhatsThis(tip)
        self._refresh_tooltip(action, action_id)
        self._sync_accessible_widgets(action, text, tip)

    def _refresh_tooltip(self, action: Any, action_id: str) -> None:
        """Rebuild the tooltip (statusTip + shortcut + action id lines).

        ``_update_action_tooltip`` regenerates the whole tooltip, so
        repeated refreshes never accumulate duplicate lines.
        """
        window = self._window
        update_tooltip = getattr(window, "_update_action_tooltip", None)
        if not callable(update_tooltip):
            return
        sequence = None
        manager = getattr(window, "_shortcut_manager", None)
        get_sequence = getattr(manager, "get_sequence", None)
        if callable(get_sequence):
            try:
                sequence = get_sequence(action_id)
            except Exception:
                logger.debug("Shortcut sequence lookup failed", exc_info=True)
        default = getattr(window, "_shortcut_defaults", {}).get(action_id)
        update_tooltip(action, action_id, sequence, default)

    @staticmethod
    def _sync_accessible_widgets(action: Any, text: str, tip: str) -> None:
        """Mirror the presentation onto real buttons, if any exist.

        ``QAction`` itself has no accessible-name API; menus expose the
        action text directly.  Only when the shared action is hosted by
        an actual ``QToolButton`` (e.g. a future toolbar) do we set its
        accessible name/description — no widgets are ever created here.
        """
        associated = getattr(action, "associatedObjects", None)
        if not callable(associated):
            return
        try:
            objects = associated()
        except Exception:
            return
        plain = _plain_text(text)
        for widget in objects or ():
            if isinstance(widget, QToolButton):
                widget.setAccessibleName(plain)
                widget.setAccessibleDescription(tip or plain)


__all__ = ["KNOWN_CONTENT_LABELS", "HistoryActionPresenter"]
