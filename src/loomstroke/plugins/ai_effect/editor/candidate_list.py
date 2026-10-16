"""Candidate list widget (Phase 7-b).

Shared widget that renders a list of authoring candidates — one of
:class:`SlotCandidate` / :class:`ParamCandidate` / :class:`OutputCandidate` —
as a single-column list where each row:

- has a checkbox controlling whether the candidate will be included when
  the preset is exported (Phase 7-d);
- shows ``suggested_display_name`` as the primary label;
- carries ``source_hint`` (e.g. ``"node_id:42.inputs.seed"``) as a tooltip
  so the author can correlate the candidate back to the raw workflow.

The widget is intentionally dumb: it never calls the Gateway. The host
(:class:`EffectEditorWindow`) populates it after :meth:`AIGateway.detect_candidates`
returns and listens to the signals below.

Signals
-------
- ``selection_changed(object)`` — emitted with the selected candidate (or
  ``None`` when the selection is cleared). Used by Phase 7-c inspector.
- ``include_toggled(object, bool)`` — emitted when the user flips the
  include checkbox. Used by Phase 7-d export to filter the author choices.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class CandidateListView(QWidget):
    """Single-column checkable list of authoring candidates."""

    selection_changed = Signal(object)
    include_toggled = Signal(object, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setUniformItemSizes(True)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.currentItemChanged.connect(self._on_current_item_changed)
        layout.addWidget(self._list)

        # ``_suppress_item_changed`` guards bulk updates from
        # ``set_candidates`` so we don't flood include_toggled for every
        # row during repopulation.
        self._suppress_item_changed = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_candidates(
        self,
        candidates: list[Any],
        *,
        default_included: bool = True,
    ) -> None:
        """Replace the list with ``candidates``.

        Each item starts checked when ``default_included`` is True (the
        common case — :meth:`AIGateway.detect_candidates` returns what the
        author is expected to include by default).

        Per-candidate override: when ``default_included`` is True, a
        candidate may opt out of the default-on UX by setting
        ``suggested_default_included = False`` on its dataclass. This is
        used by NanoBanana's catalogue, which exposes 14 input-image
        slots but only checks the first one by default. When
        ``default_included`` is False (import-preset path), the override
        is ignored — every row starts unchecked and the import flow
        explicitly ticks the ones the saved preset actually used.
        """
        self._suppress_item_changed = True
        try:
            self._list.clear()
            for cand in candidates:
                item = QListWidgetItem(_display_label(cand))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                if default_included:
                    per_candidate = bool(getattr(cand, "suggested_default_included", True))
                    check_state = (
                        Qt.CheckState.Checked if per_candidate else Qt.CheckState.Unchecked
                    )
                else:
                    check_state = Qt.CheckState.Unchecked
                item.setCheckState(check_state)
                item.setToolTip(_tooltip_for(cand))
                item.setData(Qt.ItemDataRole.UserRole, cand)
                self._list.addItem(item)
        finally:
            self._suppress_item_changed = False

    def included_candidates(self) -> list[Any]:
        """Return every candidate whose checkbox is currently ticked, in
        list order."""
        result: list[Any] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def set_included(self, candidate: Any, included: bool) -> bool:
        """Toggle the include checkbox for a specific candidate instance.

        Returns True if the candidate was found (and its state updated),
        False otherwise. Matches by object identity — callers that load
        a saved preset pass the freshly-detected candidate the imported
        choice maps to. ``include_toggled`` is suppressed during the
        update so the host can drive bulk updates without the paired
        signal firing once per row.
        """
        self._suppress_item_changed = True
        try:
            target_state = Qt.CheckState.Checked if included else Qt.CheckState.Unchecked
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item is None:
                    continue
                if item.data(Qt.ItemDataRole.UserRole) is candidate:
                    item.setCheckState(target_state)
                    return True
            return False
        finally:
            self._suppress_item_changed = False

    def all_candidates(self) -> list[Any]:
        """Return every candidate regardless of checkbox state, in list order."""
        result: list[Any] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None:
                continue
            result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def current_candidate(self) -> Any | None:
        """Return the candidate tied to the selected row, or ``None``."""
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def count(self) -> int:
        return self._list.count()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._suppress_item_changed:
            return
        candidate = item.data(Qt.ItemDataRole.UserRole)
        included = item.checkState() == Qt.CheckState.Checked
        self.include_toggled.emit(candidate, included)

    def _on_current_item_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            self.selection_changed.emit(None)
            return
        self.selection_changed.emit(current.data(Qt.ItemDataRole.UserRole))


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _display_label(candidate: Any) -> str:
    """Primary row text — prefers ``suggested_display_name`` then hint."""
    name = getattr(candidate, "suggested_display_name", "") or ""
    if name:
        return str(name)
    hint = getattr(candidate, "source_hint", "") or ""
    return str(hint) if hint else repr(candidate)


def _tooltip_for(candidate: Any) -> str:
    """Hover tooltip — exposes source hint + suggested metadata where
    available so the author can verify the heuristic's guess."""
    lines: list[str] = []
    hint = getattr(candidate, "source_hint", "")
    if hint:
        lines.append(f"source: {hint}")
    suggested_type = getattr(candidate, "suggested_type", None)
    if suggested_type is not None:
        lines.append(f"type: {suggested_type}")
    suggested_default = getattr(candidate, "suggested_default", None)
    if suggested_default is not None:
        lines.append(f"default: {suggested_default!r}")
    suggested_range = getattr(candidate, "suggested_range", None)
    if suggested_range:
        lines.append(f"range: {suggested_range}")
    suggested_choices = getattr(candidate, "suggested_choices", None)
    if suggested_choices:
        lines.append(f"choices: {suggested_choices}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GroupedParamCandidateView — node-grouped tree view for parameters
# ---------------------------------------------------------------------------


def _node_group_key(candidate: Any) -> str:
    """Extract the node-level grouping key from a parameter candidate.

    The ComfyUI gateway emits ``source_hint`` formatted as
    ``"Node#{node_id} {class_type}.{input_key}"``. We trim the trailing
    ``.{input_key}`` so every parameter on the same node lands under one
    header. When the hint has no dot (test stubs / future gateways), the
    whole hint is used; empty hints fall back to a stable sentinel so
    every parameter still has a parent row.
    """
    hint = getattr(candidate, "source_hint", "") or ""
    hint = str(hint)
    if "." in hint:
        return hint.rsplit(".", 1)[0]
    return hint or "<ungrouped>"


class GroupedParamCandidateView(QWidget):
    """Tree view of parameter candidates grouped by source node.

    Layout:

        ▾ Node#5 KSampler         ☑ (tri-state)
              steps               ☑
              cfg                 ☑
        ▸ Node#7 EmptyLatentImage ☑
              width               ☑
              height              ☑

    The node row's checkbox is auto-tristate (Qt updates it when child
    states diverge). Explicitly toggling the node row propagates to all
    children so the user can include/exclude every parameter of a node
    in one click — matching the user's "ノード単位で表示非表示と
    パラメーター個別の表示非表示" request.

    Public API mirrors :class:`CandidateListView` so callers (the
    Editor's :class:`CandidatesPane`, import flow, export flow) are
    drop-in compatible.
    """

    selection_changed = Signal(object)
    include_toggled = Signal(object, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tree = QTreeWidget(self)
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.setUniformRowHeights(True)
        self._tree.setExpandsOnDoubleClick(True)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.currentItemChanged.connect(self._on_current_item_changed)
        layout.addWidget(self._tree)

        # Insertion order (matches set_candidates input). Used by
        # included_candidates() / all_candidates() so the gateway's
        # original ordering — and therefore the export's parameter
        # order — is preserved across the tree restructure.
        self._candidates_in_order: list[Any] = []
        # Guards bulk operations (set_candidates, set_included, parent
        # → child propagation) from re-entering the include_toggled
        # path.
        self._suppress_item_changed = False

    # ------------------------------------------------------------------
    # Public API (mirrors CandidateListView)
    # ------------------------------------------------------------------

    def set_candidates(
        self,
        candidates: list[Any],
        *,
        default_included: bool = True,
    ) -> None:
        self._suppress_item_changed = True
        try:
            self._tree.clear()
            self._candidates_in_order = list(candidates)
            target_state = Qt.CheckState.Checked if default_included else Qt.CheckState.Unchecked
            # Group preserves first-encounter order so the resulting
            # tree mirrors how the workflow's nodes appear, rather than
            # alphabetising silently.
            groups: dict[str, QTreeWidgetItem] = {}
            for cand in candidates:
                key = _node_group_key(cand)
                parent_item = groups.get(key)
                if parent_item is None:
                    parent_item = QTreeWidgetItem([key])
                    parent_item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsAutoTristate
                    )
                    parent_item.setCheckState(0, target_state)
                    parent_item.setData(0, Qt.ItemDataRole.UserRole, None)
                    self._tree.addTopLevelItem(parent_item)
                    parent_item.setExpanded(True)
                    groups[key] = parent_item
                child = QTreeWidgetItem([_display_label(cand)])
                child.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                child.setCheckState(0, target_state)
                child.setToolTip(0, _tooltip_for(cand))
                child.setData(0, Qt.ItemDataRole.UserRole, cand)
                parent_item.addChild(child)
        finally:
            self._suppress_item_changed = False

    def included_candidates(self) -> list[Any]:
        """Return every candidate whose checkbox is currently ticked,
        in the original insertion order."""
        result: list[Any] = []
        for cand in self._candidates_in_order:
            item = self._find_leaf(cand)
            if item is None:
                continue
            if item.checkState(0) == Qt.CheckState.Checked:
                result.append(cand)
        return result

    def set_included(self, candidate: Any, included: bool) -> bool:
        """Toggle the include checkbox for a specific candidate.

        Returns True when the candidate was found. Suppresses
        ``include_toggled`` while updating so bulk import flows can
        emit a single refresh signal afterwards.
        """
        item = self._find_leaf(candidate)
        if item is None:
            return False
        target = Qt.CheckState.Checked if included else Qt.CheckState.Unchecked
        self._suppress_item_changed = True
        try:
            item.setCheckState(0, target)
        finally:
            self._suppress_item_changed = False
        return True

    def all_candidates(self) -> list[Any]:
        return list(self._candidates_in_order)

    def current_candidate(self) -> Any | None:
        item = self._tree.currentItem()
        if item is None:
            return None
        # Node header rows store ``None`` so selecting a header doesn't
        # leak into the inspector.
        return item.data(0, Qt.ItemDataRole.UserRole)

    def count(self) -> int:
        return len(self._candidates_in_order)

    def group_count(self) -> int:
        return self._tree.topLevelItemCount()

    def set_node_visibility(self, group_key: str, included: bool) -> bool:
        """Programmatically toggle every parameter under ``group_key``.

        Mirrors clicking the node-level checkbox. Returns True when the
        group exists. Useful for tests and for future plumbing of a
        "hide all parameters from this node" command.
        """
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            if top is None:
                continue
            if top.text(0) == group_key:
                top.setCheckState(0, Qt.CheckState.Checked if included else Qt.CheckState.Unchecked)
                return True
        return False

    # Test/debug helper to mirror ``CandidateListView._list`` access — the
    # editor tests pin selection by index. Kept narrow (read-only).
    @property
    def tree(self) -> QTreeWidget:
        return self._tree

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_leaf(self, candidate: Any) -> QTreeWidgetItem | None:
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            if top is None:
                continue
            for j in range(top.childCount()):
                leaf = top.child(j)
                if leaf is None:
                    continue
                if leaf.data(0, Qt.ItemDataRole.UserRole) is candidate:
                    return leaf
        return None

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._suppress_item_changed or column != 0:
            return
        # Top-level (node header) toggle propagates to children. Qt's
        # ItemIsAutoTristate handles child → parent already; we cover
        # the parent → child direction here.
        if item.parent() is None and (
            item.checkState(0) in (Qt.CheckState.Checked, Qt.CheckState.Unchecked)
        ):
            target = item.checkState(0)
            self._suppress_item_changed = True
            child_changes: list[tuple[Any, bool]] = []
            try:
                for i in range(item.childCount()):
                    child = item.child(i)
                    if child is None:
                        continue
                    if child.checkState(0) != target:
                        child.setCheckState(0, target)
                        cand = child.data(0, Qt.ItemDataRole.UserRole)
                        if cand is not None:
                            child_changes.append((cand, target == Qt.CheckState.Checked))
            finally:
                self._suppress_item_changed = False
            for cand, included in child_changes:
                self.include_toggled.emit(cand, included)
            return
        # Leaf toggle.
        candidate = item.data(0, Qt.ItemDataRole.UserRole)
        if candidate is None:
            return
        included = item.checkState(0) == Qt.CheckState.Checked
        self.include_toggled.emit(candidate, included)

    def _on_current_item_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            self.selection_changed.emit(None)
            return
        candidate = current.data(0, Qt.ItemDataRole.UserRole)
        # Header rows carry ``None`` — emit None too so the inspector
        # blanks out instead of trying to render a node header as a
        # candidate.
        self.selection_changed.emit(candidate)


__all__ = ["CandidateListView", "GroupedParamCandidateView"]
