"""Where a paragraph may break, as a per-index table (CT-03).

Rules, in order: no break before a line-start-prohibited character, no break
after a line-end-prohibited character, no break inside an inseparable pair
(……, ——), no break before a space (breaks happen after spaces), and in
``word`` wrap mode no break inside a Latin word.  CJK characters are
breakable between each other, as JIS X 4051 allows.
"""

from __future__ import annotations

import unicodedata

from .grapheme_clusters import grapheme_spans
from .kinsoku_policy import KinsokuPolicy

_WORD_JOINERS = frozenset("'’-")  # noqa: RUF001


class BreakOpportunityFinder:
    """Compute break opportunities for ``text[start:end]``."""

    def opportunities(
        self,
        text: str,
        start: int,
        end: int,
        *,
        policy: KinsokuPolicy,
        wrap_mode: str = "word",
    ) -> dict[int, bool]:
        """``{j: allowed}`` for every ``j`` in ``start < j <= end``.

        ``allowed[j]`` means a line may end right before ``text[j]``.  ``end``
        itself is always allowed (the paragraph ends there).
        """
        allowed: dict[int, bool] = {}
        boundaries = {
            cluster_end for _cluster_start, cluster_end in grapheme_spans(text, start, end)
        }
        word_mode = str(wrap_mode).strip().lower() != "char"
        for j in range(start + 1, end + 1):
            if j == end:
                allowed[j] = True
                continue
            left = text[j - 1]
            right = text[j]
            ok = True
            if (
                j not in boundaries
                or not policy.can_start_line(right)
                or not policy.can_end_line(left)
                or policy.is_inseparable_pair(left, right)
                or right == " "
                or (word_mode and self.is_word_char(left) and self.is_word_char(right))
            ):
                ok = False
            allowed[j] = ok
        return allowed

    @staticmethod
    def is_word_char(char: str) -> bool:
        """Latin-ish word characters that must not be split in ``word`` mode."""
        if char in _WORD_JOINERS:
            return True
        if not char.isalnum():
            return False
        # CJK ideographs / kana are "alnum" for str.isalnum but break freely.
        try:
            name = unicodedata.name(char)
        except ValueError:
            return False
        return not (
            name.startswith("CJK")
            or "HIRAGANA" in name
            or "KATAKANA" in name
            or "HANGUL" in name
            or "FULLWIDTH" in name
            or "IDEOGRAPH" in name
        )
