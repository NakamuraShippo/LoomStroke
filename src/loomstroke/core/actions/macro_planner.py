"""Dry run: validate, bind parameters, resolve targets, collect effects (CT-20 §6.2)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .action_catalog import ActionCatalog
from .action_context import ActionContext
from .action_descriptor import ActionDescriptor
from .macro_definition import MacroDefinition
from .macro_plan import MacroPlan
from .macro_step import MacroStep
from .macro_validator import MacroValidator
from .planned_step import PlannedStep
from .resolved_target import ResolvedTarget
from .target_resolver import TargetResolver

__all__ = ["MacroPlanner"]


class MacroPlanner:
    def __init__(self, catalog: ActionCatalog, resolver: TargetResolver | None = None) -> None:
        self._catalog = catalog
        self._resolver = resolver or TargetResolver()
        self._validator = MacroValidator(catalog)

    def plan(
        self,
        definition: MacroDefinition,
        context: ActionContext,
        bindings: Mapping[str, Any] | None = None,
    ) -> MacroPlan:
        validation = self._validator.validate(definition)
        # Step-scoped findings surface as blocked steps below; only macro-level
        # problems (no steps, duplicate ids) make the whole plan invalid.
        errors = tuple(
            issue.code for issue in validation.issues if issue.is_error and not issue.step_id
        )
        steps = tuple(
            self._plan_step(definition, step, context, dict(bindings or {}))
            for step in definition.steps
        )
        return MacroPlan(definition.macro_id, definition.name, context.page_id, steps, errors)

    def _plan_step(
        self,
        definition: MacroDefinition,
        step: MacroStep,
        context: ActionContext,
        bindings: dict[str, Any],
    ) -> PlannedStep:
        if not step.enabled:
            return PlannedStep(step, "disabled")
        unsupported = self._catalog.supports(step.action_id, step.action_version)
        if unsupported is not None:
            return PlannedStep(step, "blocked", reason_code=unsupported)
        descriptor = self._catalog.get(step.action_id)
        assert descriptor is not None
        params = self._params(definition, step, descriptor, bindings)
        if isinstance(params, PlannedStep):
            return params
        target = self._resolver.resolve(step.target, context)
        if step.target.target_kind != descriptor.target_kind:
            target = ResolvedTarget.blocked(descriptor.target_kind, "target_kind_mismatch")
        if not target.ok:
            return PlannedStep(
                step,
                "blocked",
                target,
                params,
                descriptor.effects,
                target.blocked_code,
                warnings=target.candidates,
            )
        return self._preview(step, descriptor, context, target, params)

    def _params(
        self,
        definition: MacroDefinition,
        step: MacroStep,
        descriptor: ActionDescriptor,
        bindings: dict[str, Any],
    ) -> dict[str, Any] | PlannedStep:
        values, missing = self.bind(definition, step, bindings)
        if missing:
            return PlannedStep(step, "blocked", reason_code="parameter_unbound", warnings=missing)
        params, errors = descriptor.resolve_parameters(values)
        if errors:
            return PlannedStep(
                step,
                "blocked",
                reason_code=errors[0][1],
                warnings=tuple(f"{name}:{code}" for name, code in errors),
            )
        return params

    def _preview(
        self,
        step: MacroStep,
        descriptor: ActionDescriptor,
        context: ActionContext,
        target: ResolvedTarget,
        params: dict[str, Any],
    ) -> PlannedStep:
        planner = self._catalog.planner(step.action_id)
        if planner is None:
            return PlannedStep(step, "ready", target, params, descriptor.effects)
        preview = planner(context, target, params)
        status = "blocked" if preview.is_blocked else "ready"
        return PlannedStep(
            step,
            status,
            target,
            params,
            descriptor.effects,
            preview.blocked_code,
            preview.warnings,
            preview.outputs,
        )

    @staticmethod
    def bind(
        definition: MacroDefinition, step: MacroStep, bindings: Mapping[str, Any]
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Literal bindings plus ``$param`` references resolved from *bindings* / defaults."""
        values: dict[str, Any] = {}
        missing: list[str] = []
        for name, raw in step.bindings.items():
            ref = MacroStep.parameter_ref(raw)
            if ref is None:
                values[name] = raw
                continue
            if ref in bindings:
                values[name] = bindings[ref]
                continue
            parameter = definition.parameter(ref)
            if parameter is not None and parameter.default is not None:
                values[name] = parameter.default
            else:
                missing.append(ref)
        return values, tuple(missing)
