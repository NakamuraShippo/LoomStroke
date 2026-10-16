"""Which clipboard offer to paste (CT-19 spec 19 §5 / §14).

Priority: the LoomStroke selection envelope (editable: mask / bounds /
DPI), then the standard clipboard image.  An invalid custom payload
falls back to the standard image *with a warning code* — it never aborts
the paste silently and never lets a bad envelope masquerade as a
successful editable paste.  Decodes are cached per clipboard sequence.
"""

from __future__ import annotations

import logging

from .clipboard_mime import ClipboardMime
from .clipboard_provider import ClipboardProvider
from .decoded_clipboard_payload import DecodedClipboardPayload
from .selection_clipboard_codec import SelectionClipboardCodec

logger = logging.getLogger(__name__)

__all__ = ["ClipboardPastePlanner"]


class ClipboardPastePlanner:
    UNSUPPORTED: str = "clipboard_unsupported"

    def __init__(
        self, provider: ClipboardProvider, codec: SelectionClipboardCodec | None = None
    ) -> None:
        self._provider = provider
        self._codec = codec or SelectionClipboardCodec()
        self._cache: tuple[int, DecodedClipboardPayload | None, str] | None = None

    def inspect(self) -> dict[str, bool]:
        formats = set(self._provider.formats())
        return {
            "selection": ClipboardMime.SELECTION in formats,
            "vector": ClipboardMime.VECTOR in formats,
            "image": self._provider.image_png() is not None,
        }

    def decode_preferred(self) -> tuple[DecodedClipboardPayload | None, str]:
        """``(payload, reason)``; reason is ``ok``, a fallback warning, or the refusal code."""
        sequence = self._provider.sequence()
        if self._cache is not None and sequence and self._cache[0] == sequence:
            return self._cache[1], self._cache[2]
        payload, reason = self._decode()
        if sequence:
            self._cache = (sequence, payload, reason)
        return payload, reason

    def _decode(self) -> tuple[DecodedClipboardPayload | None, str]:
        formats = set(self._provider.formats())
        fallback_reason = "ok"
        if ClipboardMime.SELECTION in formats:
            envelope = self._provider.data(ClipboardMime.SELECTION)
            parts = {
                mime: data
                for mime in (ClipboardMime.SELECTION_IMAGE, ClipboardMime.SELECTION_MASK)
                if (data := self._provider.data(mime)) is not None
            }
            try:
                if envelope is None:
                    raise ValueError("clipboard_parts_missing")
                return self._codec.decode(envelope, parts), "ok"
            except ValueError as error:
                logger.debug("custom clipboard payload rejected: %s", error)
                fallback_reason = "clipboard_custom_invalid_fallback_image"
        png = self._provider.image_png()
        if png is None:
            return None, self.UNSUPPORTED if fallback_reason == "ok" else "clipboard_custom_invalid"
        try:
            payload = self._codec.decode_image(png)
        except ValueError as error:
            return None, str(error)
        except Exception:
            logger.debug("standard clipboard image rejected", exc_info=True)
            return None, "clipboard_image_decode_failed"
        if fallback_reason != "ok":
            payload = DecodedClipboardPayload(
                kind=payload.kind,
                image=payload.image,
                mask=payload.mask,
                bounds=payload.bounds,
                source_dpi=payload.source_dpi,
                origin=payload.origin,
                warnings=(fallback_reason,),
            )
        return payload, fallback_reason
