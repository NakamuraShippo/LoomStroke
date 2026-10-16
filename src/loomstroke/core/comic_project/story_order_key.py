"""Lexicographic order keys for chapters / pages / dialogues (CT-17 §7.1).

Keys are base-36 fraction strings that sort as plain text; ``between``
always finds a key strictly between two neighbours (fractional indexing),
so inserting or moving never renumbers the rest of the list.  Keys never
end in ``0``, so equal values have one spelling.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["StoryOrderKey"]

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_BASE = len(_DIGITS)


def _digit(key: str | None, index: int) -> int:
    if key is None:
        return _BASE  # "infinity" at every position
    return _DIGITS.index(key[index]) if index < len(key) else 0


class StoryOrderKey:
    @staticmethod
    def validate(key: str) -> None:
        if not key or any(ch not in _DIGITS for ch in key) or key.endswith("0"):
            raise ValueError(f"invalid order key: {key!r}")

    @classmethod
    def first(cls) -> str:
        return cls.between(None, None)

    @classmethod
    def after(cls, key: str) -> str:
        return cls.between(key, None)

    @classmethod
    def before(cls, key: str) -> str:
        return cls.between(None, key)

    @classmethod
    def between(cls, low: str | None, high: str | None) -> str:
        """A key with ``low < key < high`` (``None`` = open end)."""
        if low is not None:
            cls.validate(low)
        if high is not None:
            cls.validate(high)
        if low is not None and high is not None and low >= high:
            raise ValueError("low must sort before high")
        a = low or ""
        b: str | None = high
        prefix = ""
        index = 0
        while True:
            da = _digit(a, index)
            db = _digit(b, index)
            if db - da > 1:
                return prefix + _DIGITS[(da + db) // 2]
            # db == da (descend with both) or db == da + 1 (descend with an
            # open upper bound from here on).
            prefix += _DIGITS[da]
            if db == da + 1:
                b = None
            index += 1

    @staticmethod
    def sort(keys: Iterable[str]) -> list[str]:
        return sorted(keys)
