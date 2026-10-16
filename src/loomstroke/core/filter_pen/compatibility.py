"""Keep renamed module exports synchronized for legacy extension hooks."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any


class _AliasModule(ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        aliases = self.__dict__.get("_legacy_export_aliases", {})
        for legacy, canonical in aliases.items():
            if name in (legacy, canonical):
                super().__setattr__(legacy, value)
                super().__setattr__(canonical, value)
                return
        super().__setattr__(name, value)


def bind_legacy_names(module_name: str, aliases: dict[str, str]) -> None:
    """Export both spellings from one module and preserve hook replacement."""
    module = sys.modules[module_name]
    for legacy, canonical in aliases.items():
        module.__dict__[legacy] = module.__dict__[canonical]
    module.__dict__["_legacy_export_aliases"] = aliases
    module.__class__ = _AliasModule
