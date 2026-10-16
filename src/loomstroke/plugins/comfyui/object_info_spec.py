"""Interpretation of ComfyUI ``object_info`` input specs.

ComfyUI describes a node input as ``[head, options]``. Combo (enum) inputs have
been expressed two different ways across versions, and both are served by the
same running instance depending on how each node declares its inputs:

- **Legacy** — the head *is* the choice list::

      "sampler_name": [["euler", "heun", ...], {"tooltip": "..."}]

- **Current** — the head is the literal string ``"COMBO"`` and the choices live
  under ``options["options"]``::

      "preset": ["COMBO", {"multiselect": false, "options": ["custom", ...]}]

Code that only recognised the legacy shape silently lost the choice list for
current-shape inputs: they degraded to free-form strings, skipped choice
validation, and rendered as text fields instead of dropdowns. This module is
the single place that understands both shapes.
"""

from __future__ import annotations

from typing import Any

COMBO_HEAD = "COMBO"


def split_input_spec(spec: Any) -> tuple[Any, dict[str, Any]]:
    """Return ``(head, options)`` for an ``object_info`` input spec."""

    if not isinstance(spec, (list, tuple)) or not spec:
        return None, {}
    head = spec[0]
    options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    return head, options


def is_combo_head(head: Any) -> bool:
    """True when ``head`` denotes a combo input in either shape."""

    return isinstance(head, list) or (isinstance(head, str) and head.upper() == COMBO_HEAD)


def combo_choices(head: Any, options: Any = None) -> list[Any] | None:
    """Return the choice list for a combo input.

    ``None`` means the choices are *indeterminate* here — either the input is
    not a combo at all, or it is a remote combo whose values ComfyUI resolves at
    runtime. An empty list means the node genuinely declares no choices. Callers
    must never read ``None`` as "no valid values" and reject everything.
    """

    option_map = options if isinstance(options, dict) else {}
    if isinstance(head, list):
        return [item for item in head if item is not None]
    if not (isinstance(head, str) and head.upper() == COMBO_HEAD):
        return None
    raw = option_map.get("options")
    if not isinstance(raw, list):
        return None
    return [item for item in raw if item is not None]


def combo_choices_from_spec(spec: Any) -> list[Any] | None:
    """``combo_choices`` applied directly to an ``[head, options]`` spec."""

    head, options = split_input_spec(spec)
    return combo_choices(head, options)


def is_multiselect(options: Any) -> bool:
    """True when a combo accepts a list of values rather than a single one."""

    return bool(options.get("multiselect")) if isinstance(options, dict) else False


__all__ = [
    "COMBO_HEAD",
    "combo_choices",
    "combo_choices_from_spec",
    "is_combo_head",
    "is_multiselect",
    "split_input_spec",
]
