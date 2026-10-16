"""System clipboard interop (CT-19, spec 19): codec, validation, paste planning.

Pure Python (Pillow / numpy) — Qt lives only in the UI provider / bridge.
One class per module:

- :mod:`clipboard_mime` — MIME names (``ClipboardMime``)
- :mod:`clipboard_provider` — ``ClipboardProvider`` Protocol (bytes in / out)
- :mod:`clipboard_envelope` — custom selection envelope JSON
- :mod:`clipboard_payload_validator` — limits, signatures, hashes
- :mod:`decoded_clipboard_payload` — what a paste receives
- :mod:`selection_clipboard_codec` — selection <-> envelope + PNG parts
- :mod:`clipboard_paste_planner` — MIME priority + fallback decisions
"""

from .clipboard_envelope import ClipboardEnvelope
from .clipboard_mime import ClipboardMime
from .clipboard_paste_planner import ClipboardPastePlanner
from .clipboard_payload_validator import ClipboardPayloadValidator
from .clipboard_provider import ClipboardProvider
from .decoded_clipboard_payload import DecodedClipboardPayload
from .selection_clipboard_codec import SelectionClipboardCodec

__all__ = [
    "ClipboardEnvelope",
    "ClipboardMime",
    "ClipboardPastePlanner",
    "ClipboardPayloadValidator",
    "ClipboardProvider",
    "DecodedClipboardPayload",
    "SelectionClipboardCodec",
]
