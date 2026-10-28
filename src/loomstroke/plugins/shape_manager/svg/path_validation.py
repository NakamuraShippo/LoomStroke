"""Validate the existing SVG path subset before geometry is constructed."""

from __future__ import annotations

import re
from collections.abc import Sequence

_ARGUMENT_COUNTS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Q": 4, "A": 7, "Z": 0}
_SEPARATORS = " \t\r\n,"


def tokenize_path_data(data: str, pattern: re.Pattern[str]) -> list[str]:
    """Keep every supported token and reject any other non-separator character."""
    tokens: list[str] = []
    position = 0
    for match in pattern.finditer(data):
        if data[position : match.start()].strip(_SEPARATORS):
            raise ValueError("SVG path contains unsupported syntax")
        tokens.append(match.group())
        position = match.end()
    if data[position:].strip(_SEPARATORS):
        raise ValueError("SVG path contains unsupported syntax")
    return tokens


def validate_path_commands(tokens: Sequence[str]) -> None:
    """Require complete argument groups, including implicit command repetitions."""
    if not tokens:
        return
    if tokens[0] not in {"M", "m"}:
        raise ValueError("SVG path must start with a move command")
    position = 0
    while position < len(tokens):
        argument_count = _ARGUMENT_COUNTS.get(tokens[position].upper())
        if argument_count is None:
            raise ValueError("SVG path values require a command")
        position += 1
        if argument_count == 0:
            continue
        start = position
        while position < len(tokens) and not tokens[position].isalpha():
            position += 1
        count = position - start
        if count == 0 or count % argument_count:
            raise ValueError("SVG path command has incomplete arguments")
