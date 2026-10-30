"""Parameter widgets for AI Effect Layer preset UI.

Spec references:
- ``AIeffect_design/02_file_format/lsfx_specification_v1_1.md`` §manifest.parameters
- ``AIeffect_design/04_python_design/gateway_protocol.py`` ``ParamChoice`` dataclass

A ``manifest.parameters[i]`` entry has the following shape (see
``manifest_validator.py`` for the authoritative validation rules)::

    {
      "internal_id": str,
      "display_name": str,            # optional, falls back to internal_id
      "type": "int" | "float" | "bool" | "string" | "enum",
      "default": Any,
      "group": str | None,
      "order": int,
      "range": {"min": number, "max": number, "step": number} | None,
      "choices": list[Any] | None,    # required when type == "enum"
      "required_text": bool,          # string type only
      "gateway_ref": dict,            # opaque to UI
    }

Design principles
-----------------
1. **Pure widgets, no controller knowledge.** Each widget only knows how to
   render a single parameter entry and emit ``value_changed`` when the user
   edits it. Wiring to :class:`AIEffectGenerationController` lives in Phase
   6-b3.
2. **Manifest is the source of truth.** We do not create a separate spec
   dataclass — the manifest dict already validated by ``validate_manifest``
   is passed straight through. This keeps the widget faithful to the saved
   ``.lsfx`` schema and avoids translation drift.
3. **No reuse of** ``plugins.comfyui.ui.parameter_editor``. That file models
   ComfyUI's ``InputSpec`` (node-level typing). AI Effect parameters come
   from preset manifests and have different semantics (``required_text``,
   explicit ``enum`` type, etc.), so we build a clean parallel hierarchy.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.widget_translations import bind_combo_texts, bind_widget_text
from loomstroke.ui.widgets.spinbox_sizing import fit_spinbox_to_values

logger = logging.getLogger(__name__)

# ``required_text`` strings longer than this render in a multi-line QTextEdit
# instead of a single-line QLineEdit. Matches the Effect Editor convention of
# promoting prompt-like fields to multi-line input.
_MULTILINE_DEFAULT_THRESHOLD = 64

# Safe numeric bounds used when ``range`` is omitted. Wide enough for most
# manifest defaults, narrow enough to keep the spinbox usable.
_INT_MIN_FALLBACK = -1_000_000
_INT_MAX_FALLBACK = 1_000_000
_FLOAT_MIN_FALLBACK = -1_000_000.0
_FLOAT_MAX_FALLBACK = 1_000_000.0

# QSpinBox is backed by a 32-bit signed int. A manifest range that
# exceeds this (ComfyUI's KSampler ``seed`` declares ``max=
# 0xffffffffffffffff`` in its object_info, well past INT32_MAX) causes
# ``setValue`` to raise a libshiboken Overflow warning and clamps to 0,
# silently rewriting the user's seed. We clamp the manifest range into
# QSpinBox-safe bounds before applying — for the rare param that
# legitimately needs uint64 (seed), the runtime ``param_values`` dict
# still carries the original int via ``.set_value``-from-load callers.
_QSPINBOX_INT32_MIN = -2_147_483_648
_QSPINBOX_INT32_MAX = 2_147_483_647


class ParamWidget(QWidget):
    """Base class for one-parameter editors.

    Subclasses wrap a single Qt input widget and expose:

    - :attr:`internal_id` — the manifest-level parameter id (string key used
      in ``param_values`` dicts).
    - :meth:`get_value` / :meth:`set_value` — typed value access.
    - :attr:`value_changed` (Qt Signal) — ``(internal_id, new_value)`` fired
      whenever the user edits the widget programmatically or interactively.

    Subclasses must call :meth:`_emit` (or connect to the Qt widget's change
    signal via :meth:`_connect_change_signal`) to satisfy the signal contract.
    """

    value_changed = Signal(str, object)

    def __init__(self, entry: Mapping[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        internal_id = entry.get("internal_id")
        if not isinstance(internal_id, str) or not internal_id:
            raise ValueError("param entry requires non-empty 'internal_id'")
        self._internal_id: str = internal_id
        self._entry: Mapping[str, Any] = entry

    @property
    def internal_id(self) -> str:
        return self._internal_id

    def get_value(self) -> Any:
        raise NotImplementedError

    def set_value(self, value: Any) -> None:
        raise NotImplementedError

    def get_ui_state(self) -> dict[str, Any] | None:
        """Return optional LoomStroke-only UI state for this parameter."""
        return None

    def set_ui_state(self, state: Mapping[str, Any]) -> None:
        """Restore optional LoomStroke-only UI state for this parameter."""
        del state

    def _emit(self, value: Any) -> None:
        self.value_changed.emit(self._internal_id, value)


class IntParamWidget(ParamWidget):
    """Integer parameter → ``QSlider`` + ``QSpinBox`` compound.

    Layout mirrors ``ComfyUIPlugin.create_input_widget`` for INT inputs:
    a horizontal slider sits next to the spinbox so authors can drag for
    quick exploration *and* type a precise value. The slider is hidden
    when the manifest does not declare a finite range — an unbounded
    spinbox alone is more useful than a slider that spans ±INT32 (every
    drag would jump by millions).

    Bidirectional sync uses ``blockSignals`` to break the
    slider→spinbox→slider feedback loop, mirroring the ComfyUI panel's
    pattern.
    """

    def __init__(self, entry: Mapping[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(entry, parent)
        # Expand horizontally to fill the form column so the spinbox
        # on the right edge isn't clipped by the dock width. Without
        # this, the wrapper widget reports its sizeHint as
        # ``slider.sizeHint + spin.sizeHint`` and refuses to grow,
        # so the QFormLayout assigned a column width that didn't
        # leave room for the spin.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._spin = QSpinBox(self)
        rng = entry.get("range") if isinstance(entry.get("range"), Mapping) else None
        lo = _coerce_int(rng.get("min") if rng else None, _INT_MIN_FALLBACK)
        hi = _coerce_int(rng.get("max") if rng else None, _INT_MAX_FALLBACK)
        if lo > hi:
            logger.warning(
                "IntParamWidget[%s]: manifest range has min (%d) > max (%d); swapping",
                self.internal_id,
                lo,
                hi,
            )
            lo, hi = hi, lo
        # QSpinBox is 32-bit signed; clamp the manifest range so
        # ComfyUI seeds (max = 0xffffffffffffffff) don't overflow the
        # underlying C++ int and trigger libshiboken's "Value ... exceeds
        # limits" RuntimeWarning + silent value reset. The default still
        # round-trips through ``_clamp`` below so a stored seed that was
        # within int64 but past int32 gets pinned to int32_max rather
        # than wrapping.
        clamped_lo = max(lo, _QSPINBOX_INT32_MIN)
        clamped_hi = min(hi, _QSPINBOX_INT32_MAX)
        if clamped_lo != lo or clamped_hi != hi:
            logger.debug(
                "IntParamWidget[%s]: manifest range [%d, %d] clamped to QSpinBox-safe "
                "[%d, %d]; values past INT32_MAX will round-trip via "
                "param_values not the widget",
                self.internal_id,
                lo,
                hi,
                clamped_lo,
                clamped_hi,
            )
        self._spin.setMinimum(clamped_lo)
        self._spin.setMaximum(clamped_hi)
        step = _coerce_int(rng.get("step") if rng else None, 1)
        if step > 0:
            self._spin.setSingleStep(step)
        default = _coerce_int(entry.get("default"), 0)
        self._spin.setValue(_clamp(default, clamped_lo, clamped_hi))
        fit_spinbox_to_values(self._spin, (default,))

        # Slider is only meaningful with a finite, manifest-declared
        # range. Without one we'd be presenting a slider whose step
        # spans 4M values per pixel — useless and visually noisy.
        self._slider: QSlider | None = None
        bounded = (
            rng is not None
            and clamped_lo > _QSPINBOX_INT32_MIN
            and clamped_hi < _QSPINBOX_INT32_MAX
        )
        if bounded:
            self._slider = QSlider(Qt.Orientation.Horizontal, self)
            self._slider.setMinimum(clamped_lo)
            self._slider.setMaximum(clamped_hi)
            if step > 0:
                self._slider.setSingleStep(step)
            self._slider.setValue(_clamp(default, clamped_lo, clamped_hi))
            # Drop the slider's natural ~84px minimum so the dock
            # can shrink narrow. The slider stays usable down to
            # ~30px and ``stretch=1`` lets it expand on wider docks.
            self._slider.setMinimumWidth(30)
            self._slider.valueChanged.connect(self._on_slider_changed)
            layout.addWidget(self._slider, stretch=1)

        self._spin.valueChanged.connect(self._on_spin_changed)
        layout.addWidget(self._spin)

    def _on_slider_changed(self, value: int) -> None:
        if self._slider is None:
            return
        # Block the spin's signal so its setValue doesn't ricochet
        # back through _on_spin_changed → setValue on slider.
        self._spin.blockSignals(True)
        try:
            self._spin.setValue(value)
        finally:
            self._spin.blockSignals(False)
        self._emit(value)

    def _on_spin_changed(self, value: int) -> None:
        if self._slider is not None:
            self._slider.blockSignals(True)
            try:
                self._slider.setValue(value)
            finally:
                self._slider.blockSignals(False)
        self._emit(value)

    def get_value(self) -> int:
        return int(self._spin.value())

    def set_value(self, value: Any) -> None:
        # Clamp to QSpinBox-safe int32 range. Without this, calling
        # ``setValue`` with a uint64-shaped seed (ComfyUI accepts
        # values up to 0xffffffffffffffff) raises libshiboken's
        # "Value ... exceeds limits of type [signed] int (4 bytes)"
        # RuntimeWarning and silently zeroes the widget — losing the
        # exact seed the author wanted to lock in.
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            ivalue = 0
        clamped = _clamp(ivalue, _QSPINBOX_INT32_MIN, _QSPINBOX_INT32_MAX)
        # Use the spin path; slider stays in sync through _on_spin_changed.
        self._spin.setValue(clamped)


class FloatParamWidget(ParamWidget):
    """Float parameter → ``QSlider`` + ``QDoubleSpinBox`` compound.

    The slider operates on integer positions; positions map to floats
    via the same heuristic the ComfyUI panel uses (
    ``plugins/comfyui/ui/node_tabs.py::create_input_widget``):

    - ``range ≤ 1.0``  → step=0.01, decimals=3, slider ticks=range/0.001
    - ``range ≤ 10``   → step=0.1,  decimals=2, slider ticks=range/0.01
    - ``range ≤ 100``  → step=1.0,  decimals=1, slider ticks=range/0.1
    - ``range > 100``  → step≥1.0,  decimals=0, slider ticks≤1000

    Slider is omitted when no finite manifest range is declared (same
    rationale as :class:`IntParamWidget`).
    """

    def __init__(  # noqa: PLR0915 - linear init covers spin + slider + step heuristic
        self, entry: Mapping[str, Any], parent: QWidget | None = None
    ) -> None:
        super().__init__(entry, parent)
        # See IntParamWidget for rationale — Expanding horizontal
        # policy ensures the form column gives this wrapper the full
        # available width so the spin doesn't get clipped on a
        # narrow dock.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._spin = QDoubleSpinBox(self)
        rng = entry.get("range") if isinstance(entry.get("range"), Mapping) else None
        lo = _coerce_float(rng.get("min") if rng else None, _FLOAT_MIN_FALLBACK)
        hi = _coerce_float(rng.get("max") if rng else None, _FLOAT_MAX_FALLBACK)
        if lo > hi:
            logger.warning(
                "FloatParamWidget[%s]: manifest range has min (%g) > max (%g); swapping",
                self.internal_id,
                lo,
                hi,
            )
            lo, hi = hi, lo
        self._spin.setMinimum(lo)
        self._spin.setMaximum(hi)
        # Pick decimals + step from range size, mirroring ComfyUI panel.
        rng_size = max(0.0, hi - lo)
        if rng is not None and rng_size > 0:
            if rng_size <= 1.0:
                default_step, decimals, slider_steps = 0.01, 3, max(1, int(rng_size / 0.001))
            elif rng_size <= 10:
                default_step, decimals, slider_steps = 0.1, 2, max(1, int(rng_size / 0.01))
            elif rng_size <= 100:
                default_step, decimals, slider_steps = 1.0, 1, max(1, int(rng_size / 0.1))
            else:
                default_step = max(1.0, rng_size / 1000.0)
                decimals = 0
                slider_steps = min(1000, max(1, int(rng_size / default_step)))
        else:
            default_step = 0.0
            decimals = 6  # round-trip with manifest's 6-decimal serialisation
            slider_steps = 0
        manifest_step = _coerce_float(rng.get("step") if rng else None, 0.0)
        step = manifest_step if manifest_step > 0 else default_step
        if step > 0:
            self._spin.setSingleStep(step)
        # If the manifest step has a finer decimal granularity than the
        # heuristic (e.g. step=0.001 in a range that would otherwise
        # round to 1 decimal), honour it so authoring-time precision
        # isn't silently lost on display.
        step_decimals = _decimals_from_step(manifest_step) if manifest_step > 0 else 0
        self._spin.setDecimals(max(decimals, step_decimals))
        default = _coerce_float(entry.get("default"), 0.0)
        self._spin.setValue(_clamp_float(default, lo, hi))
        fit_spinbox_to_values(self._spin, (default,))

        # Slider operates on integer positions [0, slider_steps].
        self._slider: QSlider | None = None
        self._slider_min = lo
        self._slider_range = max(0.0, rng_size)
        self._slider_steps = slider_steps
        bounded = rng is not None and slider_steps > 0
        if bounded:
            self._slider = QSlider(Qt.Orientation.Horizontal, self)
            self._slider.setMinimum(0)
            self._slider.setMaximum(slider_steps)
            self._slider.setValue(self._float_to_pos(self._spin.value()))
            # Same narrow-dock rationale as IntParamWidget — let the
            # slider shrink below Qt's ~84px sizeHint so the panel
            # can be docked tightly.
            self._slider.setMinimumWidth(30)
            self._slider.valueChanged.connect(self._on_slider_changed)
            layout.addWidget(self._slider, stretch=1)

        self._spin.valueChanged.connect(self._on_spin_changed)
        layout.addWidget(self._spin)

    def _float_to_pos(self, value: float) -> int:
        if self._slider_steps <= 0 or self._slider_range <= 0:
            return 0
        ratio = (value - self._slider_min) / self._slider_range
        return _clamp(round(ratio * self._slider_steps), 0, self._slider_steps)

    def _pos_to_float(self, pos: int) -> float:
        if self._slider_steps <= 0:
            return self._slider_min
        ratio = pos / self._slider_steps
        return self._slider_min + ratio * self._slider_range

    def _on_slider_changed(self, pos: int) -> None:
        if self._slider is None:
            return
        new_value = self._pos_to_float(pos)
        self._spin.blockSignals(True)
        try:
            self._spin.setValue(new_value)
        finally:
            self._spin.blockSignals(False)
        self._emit(self._spin.value())

    def _on_spin_changed(self, value: float) -> None:
        if self._slider is not None:
            self._slider.blockSignals(True)
            try:
                self._slider.setValue(self._float_to_pos(value))
            finally:
                self._slider.blockSignals(False)
        self._emit(value)

    def get_value(self) -> float:
        return float(self._spin.value())

    def set_value(self, value: Any) -> None:
        # Use the spin path; slider stays in sync through _on_spin_changed.
        self._spin.setValue(float(value))


class BoolParamWidget(ParamWidget):
    """Bool parameter → ``QCheckBox``."""

    def __init__(self, entry: Mapping[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(entry, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._check = QCheckBox(self)
        self._check.setChecked(bool(entry.get("default", False)))
        self._check.toggled.connect(self._emit)
        layout.addWidget(self._check)

    def get_value(self) -> bool:
        return bool(self._check.isChecked())

    def set_value(self, value: Any) -> None:
        self._check.setChecked(bool(value))


class StringParamWidget(ParamWidget):
    """String parameter → ``QLineEdit`` or ``QTextEdit`` + LivePrompt composer.

    Promotes to ``QTextEdit`` when either ``required_text`` is true or the
    default value exceeds :data:`_MULTILINE_DEFAULT_THRESHOLD` characters —
    prompt-style fields render as multiline, short tags stay single-line.

    Multiline mode embeds the same ``PromptComposerWidget`` ComfyUI panel
    and NanoBanana use, so the user can pick a saved Prompt Editor
    composition and tweak chips inline. Composed text feeds into the
    underlying ``QTextEdit`` (the canonical value holder), exactly the
    pattern :class:`NanoBananaPanel` follows. ``host_api`` is forwarded
    to the composer so it can locate the snippet repository; ``None``
    is acceptable for tests / standalone use (the composer falls back
    to flat-text mode and surfaces no compositions).
    """

    def __init__(
        self,
        entry: Mapping[str, Any],
        parent: QWidget | None = None,
        *,
        host_api: Any | None = None,
    ) -> None:
        super().__init__(entry, parent)
        # Fill the form column so a long single-line entry's right
        # edge isn't clipped on a narrow dock.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        default = entry.get("default")
        default_str = str(default) if default is not None else ""
        multiline = bool(entry.get("required_text")) or (
            len(default_str) > _MULTILINE_DEFAULT_THRESHOLD
        )

        self._is_multiline = multiline
        self._prompt_composer: QWidget | None = None
        # Composer-side state lives independent of the QTextEdit. When
        # LivePrompt is producing text, the QTextEdit is hidden and
        # ``_composer_text`` is the canonical value (see ``get_value``).
        # This keeps "hide" meaning "the widget is gone from the form",
        # not "the widget is invisible but still sitting there with
        # mirrored content".
        self._composer_active = False
        self._composer_has_input = False
        self._composer_text = ""
        if multiline:
            # Composer above, canonical text edit below. Vertical layout
            # so the composer's chip flow can grow without pushing the
            # text edit off-screen on narrow docks.
            layout: QHBoxLayout | QVBoxLayout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)

            self._prompt_composer = self._build_prompt_composer(host_api)
            if self._prompt_composer is not None:
                layout.addWidget(self._prompt_composer)

            self._text: QTextEdit | QLineEdit = QTextEdit(self)
            self._text.setPlainText(default_str)  # type: ignore[union-attr]
            self._text.textChanged.connect(self._on_text_changed)
            layout.addWidget(self._text, stretch=1)
        else:
            layout = QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            self._text = QLineEdit(self)
            self._text.setText(default_str)
            self._text.textChanged.connect(self._emit)
            layout.addWidget(self._text, stretch=1)

    def _build_prompt_composer(self, host_api: Any | None) -> QWidget | None:
        """Construct the LivePrompt composer if its plugin is importable.

        Soft import — when the comfyui plugin's prompt composer module
        is unavailable (test stubs, packaging variants without the
        plugin), the multiline mode degrades gracefully to a plain
        QTextEdit. No regressions for callers that don't use the
        composer feature.
        """
        try:
            from loomstroke.plugins.comfyui.ui.prompt_composer import (  # noqa: PLC0415
                PromptComposerWidget,
            )
        except Exception:
            logger.debug(
                "PromptComposerWidget unavailable; multiline string falls back to QTextEdit"
            )
            return None
        composer = PromptComposerWidget(host_api=host_api, parent=self)
        composer.composedTextChanged.connect(self._on_composed_text_changed)
        composer.modeChanged.connect(self._on_prompt_mode_changed)
        return composer

    def _on_composed_text_changed(self, text: str) -> None:
        """Track composer text and hide the QTextEdit if any input arrived.

        Composer text is held in ``_composer_text``; we deliberately
        do NOT write it into the QTextEdit. The widget then either
        appears (flat-text mode, no composer input) or is fully
        removed from the layout — never sitting hidden-but-mirrored,
        which is what the user flagged.
        """
        self._composer_text = text
        self._composer_has_input = bool(text)
        self._update_text_edit_visibility()
        self._emit(self.get_value())

    def _on_prompt_mode_changed(self, composer_active: bool) -> None:
        """Track composer-active mode and re-evaluate visibility."""
        self._composer_active = bool(composer_active)
        self._update_text_edit_visibility()

    def _update_text_edit_visibility(self) -> None:
        """Show the QTextEdit only when LivePrompt is dormant.

        ``setVisible(False)`` removes the widget from layout space
        (the QFormLayout row collapses to the composer's height) so
        the multiline param row visually shrinks when LivePrompt is
        the active source. This is the behaviour the user asked for:
        the widget is gone, not merely emptied.
        """
        if not isinstance(self._text, QTextEdit):
            return
        should_hide = self._composer_active or self._composer_has_input
        self._text.setVisible(not should_hide)

    def _on_text_changed(self) -> None:
        # QTextEdit.textChanged is parameter-less; fetch the value explicitly.
        self._emit(self.get_value())

    def get_value(self) -> str:
        if self._is_multiline:
            assert isinstance(self._text, QTextEdit)
            # LivePrompt wins as the source of truth whenever it is
            # actively producing text — composer mode loaded, or any
            # chip-flow output emitted. The QTextEdit is hidden in
            # that state, so reading from it would return stale
            # content from before the user engaged LivePrompt.
            if self._composer_active or self._composer_has_input:
                return self._composer_text
            return self._text.toPlainText()
        assert isinstance(self._text, QLineEdit)
        return self._text.text()

    def set_value(self, value: Any) -> None:
        text = "" if value is None else str(value)
        if self._is_multiline:
            assert isinstance(self._text, QTextEdit)
            # External writes (state restore, undo, etc.) reset the
            # composer-side tracking so the supplied ``value`` becomes
            # what ``get_value`` returns. Without this, a saved flat
            # text would be invisible to readers as long as a stale
            # ``_composer_has_input`` flag stuck around.
            self._composer_active = False
            self._composer_text = ""
            self._composer_has_input = False
            self._update_text_edit_visibility()
            # Round-trip protection: every keystroke triggers a write
            # through canvas_service which fires LAYER_CHANGED, which
            # the panel handles by re-syncing widget values via this
            # method. ``setPlainText`` is NOT a no-op even when the
            # content matches — it always resets the cursor to
            # position 0 and clears any active selection. That turned
            # "12345" into "54321" because each new char landed at
            # position 0, ahead of the previous one. Bail early when
            # the text already matches and preserve the cursor when
            # it really does need to change.
            if self._text.toPlainText() == text:
                return
            cursor = self._text.textCursor()
            previous_pos = cursor.position()
            self._text.blockSignals(True)
            try:
                self._text.setPlainText(text)
            finally:
                self._text.blockSignals(False)
            # Restore cursor (clamped) so a programmatic content
            # update arriving while the user is typing doesn't
            # teleport the caret away from where they're working.
            new_cursor = self._text.textCursor()
            new_cursor.setPosition(min(previous_pos, len(text)))
            self._text.setTextCursor(new_cursor)
        else:
            assert isinstance(self._text, QLineEdit)
            # QLineEdit.setText is already a no-op when the new text
            # matches, but be explicit so the cursor / selection is
            # demonstrably preserved across the round-trip too.
            if self._text.text() == text:
                return
            cursor_pos = self._text.cursorPosition()
            self._text.blockSignals(True)
            try:
                self._text.setText(text)
            finally:
                self._text.blockSignals(False)
            self._text.setCursorPosition(min(cursor_pos, len(text)))

    def get_ui_state(self) -> dict[str, Any] | None:
        if not self._is_multiline or self._prompt_composer is None:
            return None
        exporter = getattr(self._prompt_composer, "liveprompt_state", None)
        live_state: dict[str, Any] = {}
        if callable(exporter):
            try:
                raw_state = exporter()
            except Exception:
                logger.debug("LivePrompt state export failed", exc_info=True)
            else:
                if isinstance(raw_state, Mapping):
                    live_state.update(dict(raw_state))

        live_state["active"] = self._composer_active
        live_state["has_input"] = self._composer_has_input
        if self._composer_active or self._composer_has_input:
            live_state["composed_text"] = self._composer_text

        has_components = bool(live_state.get("components"))
        if not self._composer_active and not self._composer_has_input and not has_components:
            return None
        return {"live_prompt": live_state}

    def set_ui_state(self, state: Mapping[str, Any]) -> None:
        if not self._is_multiline or self._prompt_composer is None:
            return
        raw_live_state = state.get("live_prompt") if isinstance(state, Mapping) else None
        if not isinstance(raw_live_state, Mapping):
            return

        composed_text = ""
        restorer = getattr(self._prompt_composer, "restore_liveprompt_state", None)
        if callable(restorer):
            try:
                restored = restorer(raw_live_state)
            except Exception:
                logger.debug("LivePrompt state restore failed", exc_info=True)
            else:
                if isinstance(restored, str):
                    composed_text = restored
        if not composed_text:
            fallback = raw_live_state.get("composed_text")
            if isinstance(fallback, str):
                composed_text = fallback

        self._composer_active = bool(raw_live_state.get("active", False))
        self._composer_has_input = bool(raw_live_state.get("has_input", bool(composed_text)))
        self._composer_text = composed_text
        self._update_text_edit_visibility()


class EnumParamWidget(ParamWidget):
    """Enum parameter → ``QComboBox``.

    Choice values are stored verbatim (via ``setItemData``) so the widget
    preserves non-string enum values (ints, bools) exactly as declared in the
    manifest. The display label falls back to ``str(value)``.
    """

    def __init__(self, entry: Mapping[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(entry, parent)
        # Expand horizontally so a long combo (sampler_name with 40+
        # entries) doesn't push the dock width up. The slot combo
        # uses the same trick — Ignored size policy + minimum
        # contents length 0 means the widget shrinks to whatever
        # width the form column gives it; the popup still shows
        # the full text.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._combo = QComboBox(self)
        self._combo.setMinimumContentsLength(0)
        self._combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        choices = entry.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"enum param {self.internal_id!r} requires non-empty 'choices' list")
        for choice in choices:
            self._combo.addItem(str(choice), choice)

        default = entry.get("default")
        default_index = 0
        for i, choice in enumerate(choices):
            if choice == default:
                default_index = i
                break
        self._combo.setCurrentIndex(default_index)

        self._combo.currentIndexChanged.connect(self._on_index_changed)
        layout.addWidget(self._combo, stretch=1)

    def _on_index_changed(self, _index: int) -> None:
        self._emit(self.get_value())

    def get_value(self) -> Any:
        return self._combo.currentData()

    def set_value(self, value: Any) -> None:
        for i in range(self._combo.count()):
            if self._combo.itemData(i) == value:
                self._combo.setCurrentIndex(i)
                return
        # Surface at WARNING level (not DEBUG) so a state.param_values entry
        # left stale by a preset update — where the saved value was dropped
        # from the enum's choices — shows up in the log instead of silently
        # reverting the widget to its default. Include the choices list to
        # make triage faster.
        available = [self._combo.itemData(i) for i in range(self._combo.count())]
        logger.warning(
            "EnumParamWidget[%s]: value %r not in choices %r — keeping current selection",
            self.internal_id,
            value,
            available,
        )


# Mode values stored on SeedParamWidget — matches the ComfyUI panel's
# data values so a future cross-implementation refactor (shared helper)
# wouldn't need to translate strings.
SEED_MODE_FIXED = "fixed"
SEED_MODE_INCREMENT = "increment"
SEED_MODE_DECREMENT = "decrement"
SEED_MODE_RANDOMIZE = "randomize"
_SEED_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    (SEED_MODE_FIXED, "fixed"),
    (SEED_MODE_INCREMENT, "increment (+1)"),
    (SEED_MODE_DECREMENT, "decrement (-1)"),
    (SEED_MODE_RANDOMIZE, "randomize"),
)


class SeedParamWidget(ParamWidget):
    """Seed-shaped int parameter — value box + mode dropdown.

    Mirrors the ComfyUI panel's seed control (
    ``plugins/comfyui/ui/node_tabs.py::_create_seed_value_input`` +
    ``_wrap_seed_mode_widget``):

    - **Top row**: a ``QLineEdit`` accepting a free-form integer. Why
      QLineEdit instead of QSpinBox: ComfyUI's KSampler accepts seeds
      up to ``0xffffffffffffffff`` (uint64) which overflows QSpinBox's
      32-bit backing type. Free-form text sidesteps the overflow and
      matches what authors are used to from the ComfyUI panel.
    - **Bottom row**: a ``QComboBox`` with the four standard modes
      (``fixed`` / ``increment`` / ``decrement`` / ``randomize``).

    The mode is consumed at generate-time by
    :meth:`apply_pre_generate_mutation`, which the Effect Panel calls
    before emitting ``ai_generate_requested``. ``fixed`` is a no-op;
    the other three rewrite the displayed value following the same
    rules the ComfyUI panel applies just before submitting a queue.
    """

    SEED_NAME_HINTS: tuple[str, ...] = ("seed", "noise_seed", "rand_seed")

    def __init__(self, entry: Mapping[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(entry, parent)
        # Fill the form column so the line edit + mode combo span the
        # full available width on any dock size.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._edit = QLineEdit(self)
        default_value = _coerce_int(entry.get("default"), 0)
        self._edit.setText(str(default_value))
        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.editingFinished.connect(self._on_edit_finished)
        layout.addWidget(self._edit)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(4)
        mode_label = bind_widget_text(QLabel(self), "mode:", domain="SeedParamWidget")
        mode_row.addWidget(mode_label)
        self._mode_combo = QComboBox(self)
        for value, label in _SEED_MODE_CHOICES:
            self._mode_combo.addItem(label, value)
        bind_combo_texts(
            self._mode_combo, [label for _, label in _SEED_MODE_CHOICES], domain="SeedParamWidget"
        )
        self._mode_combo.setCurrentIndex(0)
        self._mode_combo.currentIndexChanged.connect(lambda _i: self._on_mode_changed())
        mode_row.addWidget(self._mode_combo, stretch=1)
        layout.addLayout(mode_row)

    def _on_text_changed(self, text: str) -> None:
        stripped = text.strip()
        if stripped in ("", "+", "-"):
            return
        try:
            value = int(stripped)
        except (TypeError, ValueError):
            return
        self._emit(value)

    def _on_edit_finished(self) -> None:
        # Coerce display text to an int when the user commits the edit,
        # so an accidental "1234x" gets reset to a parseable form
        # before downstream consumers read it.
        try:
            value = int(self._edit.text().strip())
        except (TypeError, ValueError):
            value = 0
            self._edit.setText("0")
        self._emit(value)

    def _on_mode_changed(self) -> None:
        # The mode is UI-only metadata, but it must still go through
        # value_changed so EffectPanel collects and writes param_ui_state.
        self._emit(self.get_value())

    def get_value(self) -> int:
        try:
            return int(self._edit.text().strip())
        except (TypeError, ValueError):
            return 0

    def set_value(self, value: Any) -> None:
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            ivalue = 0
        # Skip the round-trip when the displayed value already matches
        # — avoids a redundant value_changed emission on idempotent
        # syncs (cosmetic, but keeps the signal log clean).
        try:
            current = int(self._edit.text().strip())
        except (TypeError, ValueError):
            current = None
        self._edit.blockSignals(True)
        try:
            self._edit.setText(str(ivalue))
        finally:
            self._edit.blockSignals(False)
        # Match IntParamWidget's contract: ``set_value`` emits
        # ``value_changed`` so callers that hook the signal to drive
        # write-back (Effect Panel's ``_on_ai_param_changed``) see
        # programmatic mutations too. The panel's ``_updating`` guard
        # suppresses ricochets during ``_sync_ai_effect_values``.
        if current != ivalue:
            self._emit(ivalue)

    def get_mode(self) -> str:
        """Return the current seed mode (``"fixed"`` / ``"increment"`` /
        ``"decrement"`` / ``"randomize"``)."""
        data = self._mode_combo.currentData()
        return str(data) if isinstance(data, str) else SEED_MODE_FIXED

    def set_mode(self, mode: str) -> None:
        for i in range(self._mode_combo.count()):
            if self._mode_combo.itemData(i) == mode:
                self._mode_combo.setCurrentIndex(i)
                return

    def get_ui_state(self) -> dict[str, Any] | None:
        mode = self.get_mode()
        if mode == SEED_MODE_FIXED:
            return None
        return {"seed_mode": mode}

    def set_ui_state(self, state: Mapping[str, Any]) -> None:
        raw_mode = state.get("seed_mode") if isinstance(state, Mapping) else None
        if not isinstance(raw_mode, str):
            return
        valid_modes = {mode for mode, _label in _SEED_MODE_CHOICES}
        if raw_mode not in valid_modes:
            return
        self._mode_combo.blockSignals(True)
        try:
            self.set_mode(raw_mode)
        finally:
            self._mode_combo.blockSignals(False)

    def apply_pre_generate_mutation(self) -> int:
        """Apply the selected seed mode and return the new value.

        Called by the Effect Panel before each Generate so that
        ``increment`` / ``decrement`` / ``randomize`` modes actually
        change the seed across runs without the author having to
        retype it. ``fixed`` is a no-op. Returns the post-mutation
        integer value (also written to the line edit so the user sees
        what got submitted).

        Random mode keeps the same digit count as the current value so
        runs stay visually consistent — matches ``_adjust_seed_value``
        in :mod:`loomstroke.plugins.comfyui.ui.main_panel`.
        """
        mode = self.get_mode()
        current = self.get_value()
        if mode == SEED_MODE_INCREMENT:
            new_value = current + 1
        elif mode == SEED_MODE_DECREMENT:
            new_value = current - 1
        elif mode == SEED_MODE_RANDOMIZE:
            digits = max(1, len(str(abs(current)) or "0"))
            lo = 10 ** (digits - 1) if digits > 1 else 0
            hi = (10**digits) - 1
            new_value = random.randint(lo, hi)  # noqa: S311 - non-crypto seed RNG
        else:
            return current
        # ``set_value`` emits ``value_changed`` itself so the Effect
        # Panel's ``_on_ai_param_changed`` write-back hook sees the
        # mutated seed in ``state.param_values``.
        self.set_value(new_value)
        return new_value


_WIDGET_BY_TYPE: dict[str, type[ParamWidget]] = {
    "int": IntParamWidget,
    "float": FloatParamWidget,
    "bool": BoolParamWidget,
    "string": StringParamWidget,
    "enum": EnumParamWidget,
}


def _looks_like_seed(internal_id: str) -> bool:
    """Heuristic match for "this int param is a seed".

    Mirrors the ComfyUI panel's ``"seed" in param_name.lower()`` rule
    but adds a couple of common variants used by community nodes.
    Stays purely name-based — no manifest opt-in required so existing
    presets surface the seed widget automatically.
    """
    lowered = internal_id.lower()
    return any(hint in lowered for hint in SeedParamWidget.SEED_NAME_HINTS)


def create_param_widget(
    entry: Mapping[str, Any],
    parent: QWidget | None = None,
    *,
    host_api: Any | None = None,
) -> ParamWidget:
    """Factory that returns the ``ParamWidget`` subclass for a manifest entry.

    ``host_api`` is forwarded to widgets that need access to host
    services (currently only :class:`StringParamWidget`'s LivePrompt
    composer for snippet repository lookups). Other widgets ignore it,
    so the parameter is keyword-only to keep historical positional
    call sites working.

    Raises
    ------
    ValueError
        If ``entry['type']`` is missing, not one of the five supported kinds,
        or if the entry is otherwise malformed (e.g. enum without choices).
        Callers are expected to have run ``validate_manifest`` first; this
        guard exists for direct / defensive usage.
    """
    ptype = entry.get("type")
    if not isinstance(ptype, str):
        raise ValueError(f"param entry missing 'type': {entry!r}")
    # Seed-shaped int gets the dedicated control regardless of widget
    # registry; matches the ComfyUI panel which always shows the mode
    # dropdown for seed-named INT inputs.
    if ptype == "int":
        internal_id = entry.get("internal_id")
        if isinstance(internal_id, str) and _looks_like_seed(internal_id):
            return SeedParamWidget(entry, parent)
    cls = _WIDGET_BY_TYPE.get(ptype)
    if cls is None:
        raise ValueError(
            f"unsupported param type {ptype!r} (expected one of {sorted(_WIDGET_BY_TYPE)})"
        )
    if cls is StringParamWidget:
        return StringParamWidget(entry, parent, host_api=host_api)
    return cls(entry, parent)


def _coerce_int(value: Any, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_float(value: Any, fallback: float) -> float:
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _decimals_from_step(step: float) -> int:
    """Count decimal digits implied by a step value.

    ``0.001`` → 3, ``0.01`` → 2, ``1`` → 0. Caps at 6 to match the
    manifest's serialisation precision (param_hash uses 6 decimals).
    """
    if step <= 0:
        return 0
    text = f"{step:.10f}".rstrip("0")
    if "." not in text:
        return 0
    after = text.split(".", 1)[1]
    return min(6, len(after))


__all__ = [
    "SEED_MODE_DECREMENT",
    "SEED_MODE_FIXED",
    "SEED_MODE_INCREMENT",
    "SEED_MODE_RANDOMIZE",
    "BoolParamWidget",
    "EnumParamWidget",
    "FloatParamWidget",
    "IntParamWidget",
    "ParamWidget",
    "SeedParamWidget",
    "StringParamWidget",
    "create_param_widget",
]
