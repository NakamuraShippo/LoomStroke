"""Composite panes for the Effect Editor (Phase 7-b onward).

Currently contains :class:`CandidatesPane`: the left pane of the editor,
which stacks three :class:`CandidateListView` instances (Input slots,
Parameters, Outputs). Later sub-phases will add ``InspectorPane`` (7-c)
and ``MetadataPane`` (7-d) here.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from loomstroke.plugins.ai_effect.editor.candidate_list import (
    CandidateListView,
    GroupedParamCandidateView,
)
from loomstroke.plugins.ai_effect.gateway_protocol import CandidateSet
from loomstroke.ui.translations import translate_with_fallback


def _tr(text: str) -> str:
    return translate_with_fallback("CandidatesPane", text)


class CandidatesPane(QWidget):
    """Left pane of the Effect Editor.

    Exposes the author-choice candidates returned by
    :meth:`AIGateway.detect_candidates` as three linked sections. Slots
    and outputs use the flat :class:`CandidateListView`; parameters use
    the node-grouped :class:`GroupedParamCandidateView` so the author
    can fold/unfold and toggle visibility per node.

    Forwards ``selection_changed`` and ``include_toggled`` from all
    three sections so the host can subscribe in one place.
    """

    selection_changed = Signal(object)
    include_toggled = Signal(object, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.slots = self._add_list_section(layout, "Input slots")
        self.parameters = self._add_grouped_param_section(layout, "Parameters")
        self.outputs = self._add_list_section(layout, "Outputs")

        for view in (self.slots, self.parameters, self.outputs):
            view.selection_changed.connect(self._on_child_selection)
            view.include_toggled.connect(self._on_child_include_toggled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate(
        self,
        candidate_set: CandidateSet,
        *,
        default_included: bool = True,
    ) -> None:
        """Replace the contents of all three lists from a ``CandidateSet``.

        ``default_included`` controls the initial checkbox state for every
        row. The default (True) matches fresh-workflow flow where the
        heuristic's picks are what the author usually wants. Import flow
        passes False and then ticks only the candidates that the saved
        preset actually uses (Phase 7-f).
        """
        self.slots.set_candidates(
            list(candidate_set.input_slots),
            default_included=default_included,
        )
        self.parameters.set_candidates(
            list(candidate_set.parameters),
            default_included=default_included,
        )
        self.outputs.set_candidates(
            list(candidate_set.outputs),
            default_included=default_included,
        )

    def clear(self) -> None:
        self.slots.set_candidates([])
        self.parameters.set_candidates([])
        self.outputs.set_candidates([])

    def included(self) -> CandidateSet:
        """Return a new CandidateSet containing only candidates whose
        include checkbox is currently ticked."""
        return CandidateSet(
            input_slots=list(self.slots.included_candidates()),
            parameters=list(self.parameters.included_candidates()),
            outputs=list(self.outputs.included_candidates()),
        )

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _add_list_section(self, layout: QVBoxLayout, title: str) -> CandidateListView:
        heading = QLabel(_tr(title))
        heading.setStyleSheet("font-weight: bold;")
        layout.addWidget(heading)
        view = CandidateListView()
        layout.addWidget(view, stretch=1)
        return view

    def _add_grouped_param_section(
        self, layout: QVBoxLayout, title: str
    ) -> GroupedParamCandidateView:
        heading = QLabel(_tr(title))
        heading.setStyleSheet("font-weight: bold;")
        layout.addWidget(heading)
        view = GroupedParamCandidateView()
        layout.addWidget(view, stretch=1)
        return view

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_child_selection(self, candidate: Any) -> None:
        # Single-selection across 3 sections: when one section emits a
        # real selection, clear the others so the inspector sees
        # exactly one active candidate.
        if candidate is not None:
            sender = self.sender()
            for view in (self.slots, self.parameters, self.outputs):
                if view is sender:
                    continue
                self._clear_view_selection(view)
        self.selection_changed.emit(candidate)

    @staticmethod
    def _clear_view_selection(view: Any) -> None:
        # CandidateListView (QListWidget) and GroupedParamCandidateView
        # (QTreeWidget) expose different inner widgets. Probe both so
        # the pane stays the only place that knows about both shapes.
        inner = getattr(view, "_list", None) or getattr(view, "_tree", None)
        if inner is None:
            return
        inner.blockSignals(True)
        try:
            inner.clearSelection()
            inner.setCurrentItem(None)
        finally:
            inner.blockSignals(False)

    def _on_child_include_toggled(self, candidate: Any, included: bool) -> None:
        self.include_toggled.emit(candidate, included)


__all__ = ["CandidatesPane"]
