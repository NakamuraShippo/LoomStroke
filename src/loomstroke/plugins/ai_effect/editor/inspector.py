"""Per-candidate choice inspector (Phase 7-c).

Right-hand pane of the Effect Editor. Swaps between three form layouts
depending on the currently selected candidate kind and lets the author
edit the ``*Choice`` fields that will be fed into
:meth:`AIGateway.apply_author_choices` at export time.

Defaults come from the candidate's ``suggested_*`` metadata so the form
is pre-populated with the heuristic's best guess. Each edit emits
``choice_changed(candidate, choice)`` so the host can maintain the full
``AuthorChoices`` set for export (Phase 7-d).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.plugins.ai_effect.editor.export import (
    coerce_param_default,
    default_output_choice,
    default_param_choice,
    default_slot_choice,
)
from loomstroke.plugins.ai_effect.gateway_protocol import (
    OutputCandidate,
    OutputChoice,
    ParamCandidate,
    ParamChoice,
    SlotCandidate,
    SlotChoice,
)
from loomstroke.ui.translations import translate_with_fallback


def _tr(text: str) -> str:
    return translate_with_fallback("ChoiceInspector", text)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_PARAM_TYPES: tuple[str, ...] = ("int", "float", "bool", "string", "enum")
_VALID_OUTPUT_MODES: tuple[str, ...] = ("self", "new_layer_above")

# Bounds consistent with ParamWidget's (param_widgets.py) fallbacks — keep
# range spinners usable when the candidate does not specify limits.
_INT_WIDE = 10**9
_FLOAT_WIDE = 1e12

# ---------------------------------------------------------------------------
# Inspector
# ---------------------------------------------------------------------------


class ChoiceInspector(QWidget):
    """Form inspector for a single authoring candidate.

    The inspector is stateful: :meth:`set_candidate` resets the form from
    the candidate's ``suggested_*`` fields, and every subsequent edit
    emits :attr:`choice_changed` with an up-to-date typed Choice object.

    Clear the inspector (``set_candidate(None)``) to show a placeholder
    message — used when the user deselects or a new workflow is loaded.
    """

    choice_changed = Signal(object, object)  # (candidate, Choice)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._candidate: Any = None
        self._suppress_emit = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        self._heading = QLabel(_tr("Inspector"))
        self._heading.setStyleSheet("font-weight: bold;")
        outer.addWidget(self._heading)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, stretch=1)

        # Pages. Index 0 = empty placeholder, 1 = slot, 2 = param, 3 = output.
        self._empty_page = self._build_empty_page()
        self._stack.addWidget(self._empty_page)

        self._slot_page, self._slot_widgets = self._build_slot_page()
        self._stack.addWidget(self._slot_page)

        self._param_page, self._param_widgets = self._build_param_page()
        self._stack.addWidget(self._param_page)

        self._output_page, self._output_widgets = self._build_output_page()
        self._stack.addWidget(self._output_page)

        self._stack.setCurrentWidget(self._empty_page)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_candidate(self, candidate: Any, choice: Any | None = None) -> None:
        """Load the given candidate into the matching form page.

        ``None`` clears the inspector back to the empty placeholder.
        ``choice`` may be supplied when re-editing an imported preset so
        the form reflects the saved author values instead of candidate
        defaults.
        """
        self._candidate = candidate
        if candidate is None:
            self._stack.setCurrentWidget(self._empty_page)
            return
        self._suppress_emit = True
        try:
            if isinstance(candidate, SlotCandidate):
                if isinstance(choice, SlotChoice):
                    self._populate_slot_choice(choice)
                else:
                    self._populate_slot(candidate)
                self._stack.setCurrentWidget(self._slot_page)
            elif isinstance(candidate, ParamCandidate):
                if isinstance(choice, ParamChoice):
                    self._populate_param_choice(choice)
                else:
                    self._populate_param(candidate)
                self._stack.setCurrentWidget(self._param_page)
            elif isinstance(candidate, OutputCandidate):
                if isinstance(choice, OutputChoice):
                    self._populate_output_choice(choice)
                else:
                    self._populate_output(candidate)
                self._stack.setCurrentWidget(self._output_page)
            else:
                # Unknown candidate kind — fall back to empty page.
                self._stack.setCurrentWidget(self._empty_page)
        finally:
            self._suppress_emit = False

    def current_candidate(self) -> Any:
        return self._candidate

    def current_choice(self) -> Any:
        """Build and return the Choice object corresponding to the
        candidate currently on display, or ``None`` when the inspector
        is empty."""
        if isinstance(self._candidate, SlotCandidate):
            return self._build_slot_choice()
        if isinstance(self._candidate, ParamCandidate):
            return self._build_param_choice()
        if isinstance(self._candidate, OutputCandidate):
            return self._build_output_choice()
        return None

    # ------------------------------------------------------------------
    # Page construction
    # ------------------------------------------------------------------

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        label = QLabel(_tr("Select a candidate on the left."))
        label.setStyleSheet("color: palette(mid);")
        v.addWidget(label)
        v.addStretch(1)
        return page

    def _build_slot_page(self) -> tuple[QWidget, dict[str, QWidget]]:
        page = QWidget()
        form = QFormLayout(page)
        internal = QLineEdit()
        name = QLineEdit()
        required = QCheckBox(_tr("Required"))
        order = QSpinBox()
        order.setRange(0, 10_000)
        form.addRow(_tr("Internal id"), internal)
        form.addRow(_tr("Display name"), name)
        form.addRow(required)
        form.addRow(_tr("Order"), order)

        for w in (internal, name, order):
            w_signal = getattr(w, "textChanged", None) or getattr(w, "valueChanged", None)
            if w_signal is not None:
                w_signal.connect(self._emit_choice_changed)
        required.toggled.connect(self._emit_choice_changed)
        return page, {"internal": internal, "name": name, "required": required, "order": order}

    def _build_param_page(self) -> tuple[QWidget, dict[str, QWidget]]:  # noqa: PLR0915
        page = QWidget()
        form = QFormLayout(page)
        internal = QLineEdit()
        name = QLineEdit()
        type_combo = QComboBox()
        for t in _VALID_PARAM_TYPES:
            type_combo.addItem(t, t)
        default = QLineEdit()
        group = QLineEdit()
        order = QSpinBox()
        order.setRange(0, 10_000)
        required_text = QCheckBox(_tr("Required text"))
        # Range fields (only meaningful for int / float).
        range_min = QDoubleSpinBox()
        range_min.setRange(-_FLOAT_WIDE, _FLOAT_WIDE)
        range_min.setDecimals(6)
        range_max = QDoubleSpinBox()
        range_max.setRange(-_FLOAT_WIDE, _FLOAT_WIDE)
        range_max.setDecimals(6)
        range_step = QDoubleSpinBox()
        range_step.setRange(0.0, _FLOAT_WIDE)
        range_step.setDecimals(6)
        # Enum choices: one-per-line text area as a QLineEdit for simplicity.
        # Multi-line editor is a later polish — current test presets have small
        # enums (typically <= 8 entries) that fit comfortably on one line.
        choices_line = QLineEdit()
        choices_line.setPlaceholderText(_tr("comma-separated (enum only)"))

        form.addRow(_tr("Internal id"), internal)
        form.addRow(_tr("Display name"), name)
        form.addRow(_tr("Type"), type_combo)
        form.addRow(_tr("Default"), default)
        form.addRow(_tr("Group"), group)
        form.addRow(_tr("Order"), order)
        form.addRow(required_text)
        form.addRow(_tr("Range min"), range_min)
        form.addRow(_tr("Range max"), range_max)
        form.addRow(_tr("Range step"), range_step)
        form.addRow(_tr("Choices"), choices_line)

        widgets: dict[str, QWidget] = {
            "internal": internal,
            "name": name,
            "type": type_combo,
            "default": default,
            "group": group,
            "order": order,
            "required_text": required_text,
            "range_min": range_min,
            "range_max": range_max,
            "range_step": range_step,
            "choices": choices_line,
        }
        # Signals.
        for key in ("internal", "name", "default", "group", "choices"):
            widgets[key].textChanged.connect(self._emit_choice_changed)  # type: ignore[attr-defined]
        widgets["order"].valueChanged.connect(self._emit_choice_changed)  # type: ignore[attr-defined]
        widgets["required_text"].toggled.connect(self._emit_choice_changed)  # type: ignore[attr-defined]
        widgets["type"].currentIndexChanged.connect(self._emit_choice_changed)  # type: ignore[attr-defined]
        for key in ("range_min", "range_max", "range_step"):
            widgets[key].valueChanged.connect(self._emit_choice_changed)  # type: ignore[attr-defined]
        return page, widgets

    def _build_output_page(self) -> tuple[QWidget, dict[str, QWidget]]:
        page = QWidget()
        form = QFormLayout(page)
        internal = QLineEdit()
        name = QLineEdit()
        mode_combo = QComboBox()
        for m in _VALID_OUTPUT_MODES:
            mode_combo.addItem(m, m)
        overwrite = QCheckBox(_tr("Overwrite on regenerate"))
        order = QSpinBox()
        order.setRange(0, 10_000)

        form.addRow(_tr("Internal id"), internal)
        form.addRow(_tr("Display name"), name)
        form.addRow(_tr("Mode"), mode_combo)
        form.addRow(overwrite)
        form.addRow(_tr("Order"), order)

        widgets: dict[str, QWidget] = {
            "internal": internal,
            "name": name,
            "mode": mode_combo,
            "overwrite": overwrite,
            "order": order,
        }
        internal.textChanged.connect(self._emit_choice_changed)
        name.textChanged.connect(self._emit_choice_changed)
        mode_combo.currentIndexChanged.connect(self._emit_choice_changed)
        overwrite.toggled.connect(self._emit_choice_changed)
        order.valueChanged.connect(self._emit_choice_changed)
        return page, widgets

    # ------------------------------------------------------------------
    # Populate (candidate → widgets)
    # ------------------------------------------------------------------

    def _populate_slot(self, candidate: SlotCandidate) -> None:
        self._populate_slot_choice(default_slot_choice(candidate))

    def _populate_slot_choice(self, choice: SlotChoice) -> None:
        w = self._slot_widgets
        w["internal"].setText(choice.internal_id)  # type: ignore[attr-defined]
        w["name"].setText(choice.display_name)  # type: ignore[attr-defined]
        w["required"].setChecked(choice.required)  # type: ignore[attr-defined]
        w["order"].setValue(int(choice.order))  # type: ignore[attr-defined]

    def _populate_param(self, candidate: ParamCandidate) -> None:
        d = default_param_choice(candidate)
        self._populate_param_choice(d)

    def _populate_param_choice(self, choice: ParamChoice) -> None:
        w = self._param_widgets
        w["internal"].setText(choice.internal_id)  # type: ignore[attr-defined]
        w["name"].setText(choice.display_name)  # type: ignore[attr-defined]
        ptype = choice.type if choice.type in _VALID_PARAM_TYPES else "string"
        combo: QComboBox = w["type"]  # type: ignore[assignment]
        index = combo.findData(ptype)
        combo.setCurrentIndex(index if index >= 0 else combo.findData("string"))
        w["default"].setText("" if choice.default is None else str(choice.default))  # type: ignore[attr-defined]
        w["group"].setText(choice.group or "")  # type: ignore[attr-defined]
        w["order"].setValue(int(choice.order))  # type: ignore[attr-defined]
        w["required_text"].setChecked(choice.required_text)  # type: ignore[attr-defined]
        rng = choice.range or {}
        w["range_min"].setValue(float(rng.get("min", 0) or 0))  # type: ignore[attr-defined]
        w["range_max"].setValue(float(rng.get("max", 0) or 0))  # type: ignore[attr-defined]
        w["range_step"].setValue(float(rng.get("step", 0) or 0))  # type: ignore[attr-defined]
        text = ", ".join(str(c) for c in choice.choices) if choice.choices else ""
        w["choices"].setText(text)  # type: ignore[attr-defined]

    def _populate_output(self, candidate: OutputCandidate) -> None:
        self._populate_output_choice(default_output_choice(candidate))

    def _populate_output_choice(self, choice: OutputChoice) -> None:
        w = self._output_widgets
        w["internal"].setText(choice.internal_id)  # type: ignore[attr-defined]
        w["name"].setText(choice.display_name)  # type: ignore[attr-defined]
        mode_combo: QComboBox = w["mode"]  # type: ignore[assignment]
        index = mode_combo.findData(choice.mode)
        mode_combo.setCurrentIndex(index if index >= 0 else mode_combo.findData("new_layer_above"))
        w["overwrite"].setChecked(choice.overwrite_on_regenerate)  # type: ignore[attr-defined]
        w["order"].setValue(int(choice.order))  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Build (widgets → Choice dataclass)
    # ------------------------------------------------------------------

    def _build_slot_choice(self) -> SlotChoice:
        w = self._slot_widgets
        candidate: SlotCandidate = self._candidate  # type: ignore[assignment]
        return SlotChoice(
            internal_id=w["internal"].text().strip(),  # type: ignore[attr-defined]
            display_name=w["name"].text(),  # type: ignore[attr-defined]
            required=bool(w["required"].isChecked()),  # type: ignore[attr-defined]
            order=int(w["order"].value()),  # type: ignore[attr-defined]
            gateway_ref=dict(candidate.gateway_ref),
        )

    def _build_param_choice(self) -> ParamChoice:
        w = self._param_widgets
        candidate: ParamCandidate = self._candidate  # type: ignore[assignment]
        ptype = str(w["type"].currentData() or "string")  # type: ignore[attr-defined]
        default_raw = w["default"].text()  # type: ignore[attr-defined]
        default: Any = coerce_param_default(default_raw, ptype)
        range_min = float(w["range_min"].value())  # type: ignore[attr-defined]
        range_max = float(w["range_max"].value())  # type: ignore[attr-defined]
        range_step = float(w["range_step"].value())  # type: ignore[attr-defined]
        if ptype in ("int", "float") and (range_min != 0 or range_max != 0):
            range_dict: dict[str, Any] | None = {"min": range_min, "max": range_max}
            if range_step > 0:
                range_dict["step"] = range_step
        else:
            range_dict = None
        choices_raw = w["choices"].text()  # type: ignore[attr-defined]
        choices: list[Any] | None = None
        if ptype == "enum":
            choices = [c.strip() for c in choices_raw.split(",") if c.strip()]
            if not choices:
                choices = None
        group_text = w["group"].text().strip()  # type: ignore[attr-defined]
        return ParamChoice(
            internal_id=w["internal"].text().strip(),  # type: ignore[attr-defined]
            display_name=w["name"].text(),  # type: ignore[attr-defined]
            default=default,
            group=group_text or None,
            type=ptype,
            range=range_dict,
            choices=choices,
            required_text=bool(w["required_text"].isChecked()),  # type: ignore[attr-defined]
            order=int(w["order"].value()),  # type: ignore[attr-defined]
            gateway_ref=dict(candidate.gateway_ref),
        )

    def _build_output_choice(self) -> OutputChoice:
        w = self._output_widgets
        candidate: OutputCandidate = self._candidate  # type: ignore[assignment]
        return OutputChoice(
            internal_id=w["internal"].text().strip(),  # type: ignore[attr-defined]
            display_name=w["name"].text(),  # type: ignore[attr-defined]
            mode=str(w["mode"].currentData() or "self"),  # type: ignore[attr-defined]
            overwrite_on_regenerate=bool(w["overwrite"].isChecked()),  # type: ignore[attr-defined]
            order=int(w["order"].value()),  # type: ignore[attr-defined]
            gateway_ref=dict(candidate.gateway_ref),
        )

    # ------------------------------------------------------------------
    # Signal plumbing
    # ------------------------------------------------------------------

    def _emit_choice_changed(self, *_args: Any) -> None:
        if self._suppress_emit or self._candidate is None:
            return
        choice = self.current_choice()
        if choice is not None:
            self.choice_changed.emit(self._candidate, choice)


__all__ = ["ChoiceInspector"]
