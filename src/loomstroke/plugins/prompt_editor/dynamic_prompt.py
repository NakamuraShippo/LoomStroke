"""Generation-time choices for LivePrompt, independent of Qt and gateways."""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import lcm, prod
from typing import Any

from loomstroke.plugins.common.constants import CK_LIST, CK_PROMPT, CK_STRING, CK_TEXT

SELECTION_MODES = ("fixed", "random", "sequential", "combinatorial")
_CHOICE = re.compile(r"\{([^{}]*\|[^{}]*)\}")


@dataclass(frozen=True)
class _Slot:
    mode: str
    values: tuple[str, ...]


class DynamicPromptSequence:
    """Visit one choice per submission; previews never consume this sequence.

    Sequential slots advance together. Combinatorial slots use mixed-radix
    indexing (rightmost fastest), without allocating a Cartesian product.
    Editing the composition or its candidates restarts the sequence.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()
        self._signature: object = None
        self._position = 0

    def reset(self) -> None:
        self._signature = None
        self._position = 0

    @classmethod
    def cycle_length(cls, components, catalog, render, resolve_items) -> int | None:
        """A full deterministic cycle, without advancing choices or the RNG.

        Sequential slots share one counter; Cartesian slots share an odometer.
        Random slots have no guaranteed coverage and do not define a cycle.
        """
        slots = [slot for c in components for slot in cls._slots(c, catalog, render, resolve_items)]
        sequential = [len(s.values) for s in slots if s.mode == "sequential"]
        combinations = [len(s.values) for s in slots if s.mode == "combinatorial"]
        if not sequential and not combinations:
            return None
        return lcm(*sequential, prod(combinations))

    def generate(
        self,
        components: Sequence[Any],
        catalog: Any,
        render: Callable[[Any], str],
        resolve_items: Callable[[Any], list[str]],
        separator: str,
    ) -> str:
        parts = [self._slots(c, catalog, render, resolve_items) for c in components]
        signature = (separator, tuple(tuple(part) for part in parts))
        if signature != self._signature:
            self.reset()
            self._signature = signature

        indices: dict[int, int] = {}
        remainder = self._position
        for part in reversed(parts):
            for slot in reversed(part):
                if slot.mode == "combinatorial":
                    indices[id(slot)] = remainder % len(slot.values)
                    remainder //= len(slot.values)

        rendered = []
        for part in parts:
            text = ""
            for slot in part:
                if slot.mode == "random":
                    value = self._rng.choice(slot.values)
                elif slot.mode == "sequential":
                    value = slot.values[self._position % len(slot.values)]
                else:
                    value = slot.values[indices.get(id(slot), 0)]
                text += value
            if text.strip():
                rendered.append(text.strip())
        result = separator.join(rendered)
        # Even an empty alternative consumes this attempt, so a rejected empty
        # prompt cannot trap sequential/combinatorial selection at that entry.
        self._position += 1
        return result

    @staticmethod
    def _slots(component, catalog, render, resolve_items) -> list[_Slot]:
        mode = component.selection_mode
        if mode not in SELECTION_MODES or mode == "fixed":
            return [_Slot("fixed", (render(component),))]
        if component.kind == CK_TEXT:
            text = component.text_value.strip()
            slots = []
            end = 0
            for match in _CHOICE.finditer(text):
                slots.append(_Slot("fixed", (text[end : match.start()],)))
                slots.append(_Slot(mode, tuple(v.strip() for v in match[1].split("|"))))
                end = match.end()
            slots.append(_Slot("fixed", (text[end:],)))
            return slots
        if catalog is None:
            return [_Slot("fixed", (render(component),))]
        if component.kind == CK_LIST:
            values = resolve_items(catalog.get(component.snippet_id))
            start = component.list_index or 0
        elif component.kind in {CK_STRING, CK_PROMPT}:
            snippets = catalog.iter(component.kind, tag=component.tag_filter)
            snippets = sorted(snippets, key=lambda s: ((s.title or s.id).casefold(), s.id))
            values = [(s.body or s.title or "").strip() for s in snippets]
            start = next(
                (i for i, snippet in enumerate(snippets) if snippet.id == component.snippet_id), 0
            )
        else:
            values, start = [], 0
        if not values:
            return [_Slot("fixed", (render(component),))]
        start = start % len(values)
        return [_Slot(mode, tuple(values[start:] + values[:start]))]
