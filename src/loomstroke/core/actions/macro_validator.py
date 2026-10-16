"""Schema / catalog / binding / selector validation of a macro (CT-20 §8.2 ``macros.validate``)."""

from __future__ import annotations

from .action_catalog import ActionCatalog
from .macro_definition import MacroDefinition
from .macro_issue import MacroIssue
from .macro_step import MacroStep
from .macro_validation_result import MacroValidationResult

__all__ = ["MacroValidator"]


class MacroValidator:
    def __init__(self, catalog: ActionCatalog) -> None:
        self._catalog = catalog

    def validate(self, definition: MacroDefinition) -> MacroValidationResult:
        issues: list[MacroIssue] = []
        if not definition.steps:
            issues.append(MacroIssue("no_steps"))
        seen: set[str] = set()
        for step in definition.steps:
            if step.step_id in seen:
                issues.append(MacroIssue("duplicate_step_id", step.step_id))
            seen.add(step.step_id)
            issues.extend(self._validate_step(definition, step))
        return MacroValidationResult(tuple(issues))

    def _validate_step(self, definition: MacroDefinition, step: MacroStep) -> list[MacroIssue]:
        unsupported = self._catalog.supports(step.action_id, step.action_version)
        if unsupported is not None:
            return [MacroIssue(unsupported, step.step_id, step.action_id)]
        descriptor = self._catalog.get(step.action_id)
        assert descriptor is not None
        issues: list[MacroIssue] = []
        if step.target.target_kind != descriptor.target_kind:
            issues.append(MacroIssue("target_kind_mismatch", step.step_id, step.target.describe()))
        literals: dict[str, object] = {}
        for name, value in step.bindings.items():
            if descriptor.parameter(name) is None:
                issues.append(MacroIssue("binding_unknown_name", step.step_id, name))
                continue
            ref = MacroStep.parameter_ref(value)
            if ref is None:
                literals[name] = value
            elif definition.parameter(ref) is None:
                issues.append(MacroIssue("binding_unknown_parameter", step.step_id, ref))
        for parameter in descriptor.parameters:
            if parameter.name in step.bindings:
                if parameter.name in literals:
                    _, error = parameter.coerce(literals[parameter.name])
                    if error is not None:
                        issues.append(MacroIssue(error, step.step_id, parameter.name))
            elif parameter.required and parameter.default is None:
                issues.append(MacroIssue("parameter_missing", step.step_id, parameter.name))
        if not descriptor.recordable:
            issues.append(
                MacroIssue("action_not_recordable", step.step_id, step.action_id, "warning")
            )
        return issues
