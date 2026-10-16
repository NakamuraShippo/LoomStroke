"""UI-side facade over the CT-20 action catalog: dry run, play, batch, record, library.

Page switching for batch targets happens here (core stays page-agnostic);
the original active page is restored afterwards.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from loomstroke.core.actions import (
    ActionCatalog,
    ActionContext,
    BatchPolicy,
    BatchRunner,
    BatchRunResult,
    MacroDefinition,
    MacroExecutor,
    MacroPlan,
    MacroPlanner,
    MacroRecorder,
    MacroRunResult,
    MacroStep,
    MacroStore,
    TargetSelector,
)
from loomstroke.core.actions.adapters import register_builtin_actions
from loomstroke.platform.user_dirs import default_user_root

from .actions import register_ui_actions

__all__ = ["ActionService", "BatchScope"]

BatchScope = Literal["current", "all", "range"]
CancelCheck = Callable[[], bool]


class ActionService:
    def __init__(
        self,
        host: Any,
        *,
        user_root: Path | None = None,
        catalog: ActionCatalog | None = None,
        document_dir_provider: Callable[[], Path | None] | None = None,
        app_version: str = "",
    ) -> None:
        self._host = host
        self._catalog = catalog or self.default_catalog()
        self._store = MacroStore(user_root or default_user_root())
        self._recorder = MacroRecorder(self._catalog)
        self._planner = MacroPlanner(self._catalog)
        self._executor = MacroExecutor(self._catalog)
        self._runner = BatchRunner(self._planner, self._executor)
        self._document_dir = document_dir_provider or (lambda: None)
        self._app_version = app_version

    @staticmethod
    def default_catalog() -> ActionCatalog:
        catalog = ActionCatalog()
        register_builtin_actions(catalog)
        register_ui_actions(catalog)
        return catalog

    # -- accessors ---------------------------------------------------------------------

    @property
    def catalog(self) -> ActionCatalog:
        return self._catalog

    @property
    def store(self) -> MacroStore:
        return self._store

    @property
    def recorder(self) -> MacroRecorder:
        return self._recorder

    @property
    def app_version(self) -> str:
        return self._app_version

    # -- pages -------------------------------------------------------------------------

    def active_page_id(self) -> str:
        pages = getattr(self._host, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        try:
            value = getter() if callable(getter) else ""
        except Exception:
            value = ""
        return str(value or "")

    def page_ids(self) -> tuple[str, ...]:
        pages = getattr(self._host, "pages", None)
        lister = getattr(pages, "list_pages", None)
        if not callable(lister):
            active = self.active_page_id()
            return (active,) if active else ()
        try:
            return tuple(str(summary.id) for summary in lister())
        except Exception:
            return ()

    def targets_for_scope(self, scope: BatchScope, spec: str = "") -> tuple[str, ...]:
        if scope == "current":
            active = self.active_page_id()
            return (active,) if active else ()
        ids = self.page_ids()
        if scope == "all":
            return ids
        return tuple(ids[index] for index in self.parse_range(spec, len(ids)))

    @staticmethod
    def parse_range(spec: str, count: int) -> tuple[int, ...]:
        """``"1-3,5"`` (1-based, inclusive) -> sorted unique 0-based indexes within *count*."""
        indexes: set[int] = set()
        for part in spec.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                start_text, _, end_text = part.partition("-")
                if not start_text.isdigit() or not end_text.isdigit():
                    continue
                start, end = int(start_text), int(end_text)
            elif part.isdigit():
                start = end = int(part)
            else:
                continue
            for number in range(start, end + 1):
                if 1 <= number <= count:
                    indexes.add(number - 1)
        return tuple(sorted(indexes))

    def context_for(self, page_id: str | None = None) -> ActionContext:
        page = page_id or self.active_page_id()
        return ActionContext(self._host, page, self._document_dir())

    # -- run ---------------------------------------------------------------------------

    def dry_run(
        self,
        definition: MacroDefinition,
        page_id: str | None = None,
        bindings: Mapping[str, Any] | None = None,
    ) -> MacroPlan:
        return self._planner.plan(definition, self.context_for(page_id), bindings)

    def run_on_page(
        self,
        definition: MacroDefinition,
        page_id: str | None = None,
        bindings: Mapping[str, Any] | None = None,
        *,
        cancel: CancelCheck | None = None,
    ) -> MacroRunResult:
        target = page_id or self.active_page_id()
        original = self.active_page_id()
        self._switch_page(target)
        try:
            context = self.context_for(target)
            plan = self._planner.plan(definition, context, bindings)
            return self._executor.execute(
                plan, context, cancel=cancel, undo_grouping=definition.undo_grouping
            )
        finally:
            self._switch_page(original)

    def run_batch(
        self,
        definition: MacroDefinition,
        targets: Sequence[str],
        *,
        policy: BatchPolicy = "stop",
        bindings: Mapping[str, Any] | None = None,
        cancel: CancelCheck | None = None,
    ) -> BatchRunResult:
        original = self.active_page_id()
        try:
            return self._runner.run(
                definition,
                tuple(targets),
                self.context_for,
                bindings=bindings,
                policy=policy,
                cancel=cancel,
                before_target=self._switch_page,
            )
        finally:
            self._switch_page(original)

    def run_action(
        self, action_id: str, params: Mapping[str, Any], selector: TargetSelector
    ) -> MacroRunResult:
        """Run one catalog action on the active page (and record it while recording)."""
        descriptor = self._catalog.get(action_id)
        if descriptor is None:
            return MacroRunResult(
                "_adhoc", self.active_page_id(), stopped_reason="action_unavailable"
            )
        step = MacroStep("s1", action_id, descriptor.version, dict(params), selector)
        definition = MacroDefinition("_adhoc", descriptor.title, (step,))
        context = self.context_for()
        plan = self._planner.plan(definition, context)
        result = self._executor.execute(
            plan, context, transaction_label=descriptor.title, undo_grouping="page"
        )
        if result.ok and result.executed_any:
            self._recorder.record(action_id, params, selector)
        return result

    # -- internals ---------------------------------------------------------------------

    def _switch_page(self, page_id: str) -> None:
        if not page_id or page_id == self.active_page_id():
            return
        pages = getattr(self._host, "pages", None)
        setter = getattr(pages, "set_active_page", None)
        if callable(setter):
            setter(page_id)
