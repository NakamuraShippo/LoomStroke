"""Optional implementation hints on existing UI notification callables."""

from inspect import getattr_static
from types import FunctionType
from typing import Any

_HINT = "_loomstroke_log_only_ui"


def has_log_only_ui_hint(ui: Any) -> bool:
    """Do not invoke descriptors or unwrap markers copied to other callables."""
    for name in ("show_message", "show_notification"):
        function = getattr_static(ui, name, None)
        if (
            type(function) is not FunctionType
            or getattr_static(function, _HINT, None) is not function
        ):
            return False
    return True
