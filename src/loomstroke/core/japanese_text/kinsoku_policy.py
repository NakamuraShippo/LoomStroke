"""Kinsoku (禁則) policy: which characters may not start or end a line (CT-03).

``ja_standard`` follows the common JIS X 4051 subset used by manga lettering:
closing brackets, punctuation, small kana, prolonged sound marks and
iteration marks never start a line; opening brackets never end one; leader
and dash pairs are inseparable; touten / kuten (and their fullwidth
comma / period forms) may hang past the line end when
``hanging`` is on.  Policies are versioned built-ins identified by a stable
id — the id is what a document stores, never the character tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

_LINE_START_PROHIBITED = (
    "、。，．・：；？！"  # noqa: RUF001
    "ヽヾゝゞ々〻ー～"  # noqa: RUF001
    "ぁぃぅぇぉっゃゅょゎゕゖ"
    "ァィゥェォッャュョヮヵヶ"
    "）］｝〕〉》」』】〙〗〟’”｠»"  # noqa: RUF001
    ")]}〕>"  # noqa: RUF001
    ",.!?:;"
    "゛゜"
    "〜ｰｧｨｩｪｫｯｬｭｮﾞﾟ\u3099\u309a"
    "ㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ"
)
_LINE_END_PROHIBITED = "（［｛〔〈《「『【〘〖〝‘“｟«([{<"  # noqa: RUF001
_INSEPARABLE = "—―…‥⋯"
_HANGING = "、。，．"  # noqa: RUF001


@dataclass(frozen=True, slots=True)
class KinsokuPolicy:
    """Line-breaking prohibitions for one language convention."""

    policy_id: str
    line_start_prohibited: frozenset[str]
    line_end_prohibited: frozenset[str]
    inseparable: frozenset[str]
    hanging_chars: frozenset[str]
    hanging: bool = True

    STANDARD_ID: ClassVar[str] = "ja_standard"
    NONE_ID: ClassVar[str] = "none"

    @classmethod
    def standard(cls, *, hanging: bool = True) -> KinsokuPolicy:
        return cls(
            policy_id=cls.STANDARD_ID,
            line_start_prohibited=frozenset(_LINE_START_PROHIBITED),
            line_end_prohibited=frozenset(_LINE_END_PROHIBITED),
            inseparable=frozenset(_INSEPARABLE),
            hanging_chars=frozenset(_HANGING),
            hanging=bool(hanging),
        )

    @classmethod
    def none(cls) -> KinsokuPolicy:
        return cls(
            policy_id=cls.NONE_ID,
            line_start_prohibited=frozenset(),
            line_end_prohibited=frozenset(),
            inseparable=frozenset(),
            hanging_chars=frozenset(),
            hanging=False,
        )

    @classmethod
    def by_id(cls, policy_id: str | None, *, hanging: bool | None = None) -> KinsokuPolicy:
        """Built-in policy for a stored id; unknown ids fall back to ``ja_standard``."""
        normalized = str(policy_id or "").strip().lower()
        if normalized == cls.NONE_ID:
            return cls.none()
        return cls.standard(hanging=True if hanging is None else hanging)

    def can_start_line(self, char: str) -> bool:
        return char not in self.line_start_prohibited

    def can_end_line(self, char: str) -> bool:
        return char not in self.line_end_prohibited

    def is_inseparable_pair(self, left: str, right: str) -> bool:
        return left in self.inseparable and right in self.inseparable and left == right

    def can_hang(self, char: str) -> bool:
        return self.hanging and char in self.hanging_chars
