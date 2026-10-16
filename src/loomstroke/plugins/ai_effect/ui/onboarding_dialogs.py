"""One-shot onboarding dialogs for the AI Effect Layer (Phase 6-d).

Scope
-----
Three small informational dialogs backed by :class:`OnboardingState`:

1. **No gateway** — the user opened AI Effect but no backend is registered.
   Shown every time until a gateway registers (state doesn't need to track
   this; the caller decides when to invoke).
2. **No preset** — the preset library is empty. We offer to open the preset
   folder so the user can drop in a ``.lsfx`` file.
3. **First-gen banner** — a congratulations toast after the user's very
   first successful generation. Shown at most once per OnboardingState;
   :meth:`OnboardingState.mark_first_gen_done` gates this.

All dialogs route through :class:`QMessageBox` (simple, translatable,
no custom-widget complexity required at this stage). Helpers accept a
callable ``on_save`` so they stay decoupled from the exact persistence
path — the host wires this to :meth:`OnboardingState.save`.

ComfyUI-detection and API-key onboarding dialogs (per the design spec)
are deliberately out of scope for Phase 6-d: they require backend probes
and Gateway-specific flows that land with Phase 8 (NanoBanana etc.).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from loomstroke.plugins.ai_effect.state import OnboardingState
from loomstroke.ui.translations import translate_with_fallback

logger = logging.getLogger(__name__)

SaveCallback = Callable[[OnboardingState], None]


def show_no_gateway_dialog(parent: QWidget | None = None) -> None:
    """Inform the user that no AI backend Gateway is configured.

    Safe to call repeatedly; no state flag gates it. The message points the
    user at the Preferences dialog's AI Effect tab.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(_tr("AI Effect — no gateway"))
    box.setText(_tr("No AI backend is configured."))
    box.setInformativeText(
        _tr(
            "Open Preferences → AI Effect and register a backend (e.g. a "
            "running ComfyUI instance) before generating."
        )
    )
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


def show_no_preset_dialog(
    preset_dir: Path,
    parent: QWidget | None = None,
) -> None:
    """Inform the user that no ``.lsfx`` preset is installed.

    If the user accepts the "Open folder" action we attempt to reveal the
    preset directory in the OS file browser. Failure to open is logged and
    swallowed — the dialog is informational either way.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(_tr("AI Effect — no presets"))
    box.setText(_tr("No AI Effect presets are installed."))
    box.setInformativeText(
        _tr("Drop a .lsfx file into the preset folder, then restart the " "panel to pick it up.")
    )
    open_button = box.addButton(_tr("Open preset folder"), QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Ok)
    box.exec()

    if box.clickedButton() is open_button:
        _open_folder(preset_dir)


def maybe_show_first_gen_banner(
    state: OnboardingState,
    on_save: SaveCallback,
    parent: QWidget | None = None,
) -> bool:
    """Show the first-generation congratulation banner, at most once.

    Returns True if the banner was shown (and state mutated + persisted),
    False when it had already fired previously.
    """
    if state.onboarding.first_gen_done:
        return False
    state.mark_first_gen_done()
    try:
        on_save(state)
    except Exception:
        # Persistence failure is recoverable: worst case the banner shows
        # again next time. Log and keep going.
        logger.exception("Failed to persist OnboardingState after first-gen banner")

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(_tr("AI Effect"))
    box.setText(_tr("Your first AI Effect generation completed."))
    box.setInformativeText(
        _tr(
            "Tip: the Preferences → AI Effect tab controls retries, cache "
            "size and the default trigger."
        )
    )
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
    return True


def _open_folder(path: Path) -> None:
    """Best-effort reveal of ``path`` in the OS file browser."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("Could not create preset folder %s", path, exc_info=True)
    url = QUrl.fromLocalFile(str(path))
    if not QDesktopServices.openUrl(url):
        logger.warning("QDesktopServices failed to open %s", path)


_I18N_DOMAIN = "AIEffectOnboarding"


def _tr(text: str) -> str:
    """Translate via the LoomStroke runtime translator.

    This module's helpers are plain functions (not QWidget subclasses) so
    ``self.tr(...)`` is unavailable. ``translate_with_fallback`` resolves
    the active language via ``QCoreApplication.translate`` and falls back
    to the source text when no catalog entry exists.
    """
    return translate_with_fallback(_I18N_DOMAIN, text)


__all__ = [
    "maybe_show_first_gen_banner",
    "show_no_gateway_dialog",
    "show_no_preset_dialog",
]
