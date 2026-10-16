"""Actions / Macro / Batch (CT-20): declarative catalog, validation, dry run, execution.

Pure Python, one class per module.  Built-in command-backed adapters live in
``loomstroke.core.actions.adapters``; UI adapters and the panel in ``loomstroke.ui``
and ``loomstroke.plugins.actions_panel``.
"""

from __future__ import annotations

from .action_catalog import ActionCatalog, ActionExecutor, ActionPlanner
from .action_context import ActionContext
from .action_descriptor import ActionDescriptor, TargetKind
from .action_effect import ActionEffect, EffectKind
from .action_outcome import ActionOutcome
from .action_parameter import ActionParameter, ParameterKind
from .action_preview import ActionPreview
from .batch_run_result import BatchPolicy, BatchRunResult
from .batch_runner import BatchRunner
from .batch_target_record import BatchTargetRecord, BatchTargetStatus
from .macro_definition import MacroDefinition, UndoGrouping
from .macro_executor import MacroExecutor
from .macro_format_error import MacroFormatError
from .macro_issue import MacroIssue
from .macro_parameter import MacroParameter
from .macro_plan import MacroPlan
from .macro_planner import MacroPlanner
from .macro_recorder import MacroRecorder
from .macro_run_result import MacroRunResult
from .macro_step import MacroStep, OnError
from .macro_store import MacroStore
from .macro_validation_result import MacroValidationResult
from .macro_validator import MacroValidator
from .planned_step import PlannedStep, StepStatus
from .resolved_target import ResolvedTarget
from .step_outcome import StepOutcome, StepRunStatus
from .target_resolver import TargetResolver
from .target_selector import SelectorKind, TargetSelector

__all__ = [
    "ActionCatalog",
    "ActionContext",
    "ActionDescriptor",
    "ActionEffect",
    "ActionExecutor",
    "ActionOutcome",
    "ActionParameter",
    "ActionPlanner",
    "ActionPreview",
    "BatchPolicy",
    "BatchRunResult",
    "BatchRunner",
    "BatchTargetRecord",
    "BatchTargetStatus",
    "EffectKind",
    "MacroDefinition",
    "MacroExecutor",
    "MacroFormatError",
    "MacroIssue",
    "MacroParameter",
    "MacroPlan",
    "MacroPlanner",
    "MacroRecorder",
    "MacroRunResult",
    "MacroStep",
    "MacroStore",
    "MacroValidationResult",
    "MacroValidator",
    "OnError",
    "ParameterKind",
    "PlannedStep",
    "ResolvedTarget",
    "SelectorKind",
    "StepOutcome",
    "StepRunStatus",
    "StepStatus",
    "TargetKind",
    "TargetResolver",
    "TargetSelector",
    "UndoGrouping",
]
