"""Document revision/epoch tracking for save correctness (SAVE-ASYNC-REV-01).

``DocumentService.save_async()`` snapshots the document on the main thread
and encodes on a worker while the Qt event loop keeps running — edits and
even document switches can happen before the save completes.  This pure
Python tracker lets the persistence layer freeze a ``(epoch, revision)``
token at snapshot time and, at completion, decide whether the archive it
just wrote still covers the live document:

- ``covers_latest`` — same document, no edits since the snapshot: the
  save may confirm the saved-document baseline (history reset, page
  mark-all-clean, metadata baseline).
- same document but stale — the archive is a valid save of an OLDER
  state: report success, but the document stays dirty and history is
  preserved.
- different epoch — the document was replaced while encoding: the old
  completion must not touch the new document at all.

Ownership: ``DocumentServiceImpl`` owns one tracker and feeds it from
EventBus (``DOC_CHANGED`` advances the revision; ``DOC_CREATED`` /
``DOC_LOADED`` / ``DOC_CLOSED`` start a new epoch).  This class stays
free of Qt / filesystem / UI / logging dependencies.

This is deliberately SEPARATE from ``AutosaveRevisionTracker``
(AUTOSAVE-01B): that one owns autosave scheduling / coverage /
single-flight, this one owns core save correctness.  They are not merged
into a shared base for now.
"""

from __future__ import annotations

from dataclasses import dataclass

DocumentRevisionToken = tuple[int, int]
"""``(document_epoch, revision)`` frozen when a save snapshot is captured."""


@dataclass(frozen=True, slots=True)
class DocumentRevisionOutcome:
    """Classification of a save token against the current document state."""

    same_document: bool
    covers_latest: bool
    captured_epoch: int
    captured_revision: int


class DocumentRevisionTracker:
    """Monotonic document epoch/revision state for save-outcome decisions."""

    def __init__(self) -> None:
        self._epoch = 0
        self._revision = 0

    # -- inspection --------------------------------------------------------
    @property
    def document_epoch(self) -> int:
        return self._epoch

    @property
    def current_revision(self) -> int:
        return self._revision

    # -- transitions -------------------------------------------------------
    def mark_modified(self) -> None:
        """Record one document modification (O(1); holds no payload)."""

        self._revision += 1

    def reset_document(self) -> None:
        """Enter a new document epoch (created / loaded / closed)."""

        self._epoch += 1
        self._revision = 0

    # -- tokens ------------------------------------------------------------
    def capture_token(self) -> DocumentRevisionToken:
        """Freeze the state a snapshot taken right now would cover."""

        return (self._epoch, self._revision)

    def is_same_document(self, token: DocumentRevisionToken) -> bool:
        return token[0] == self._epoch

    def is_current(self, token: DocumentRevisionToken) -> bool:
        return self.is_same_document(token) and token[1] == self._revision

    def classify(self, token: DocumentRevisionToken) -> DocumentRevisionOutcome:
        """Read-only classification; never mutates epoch or revision."""

        same_document = self.is_same_document(token)
        return DocumentRevisionOutcome(
            same_document=same_document,
            covers_latest=same_document and token[1] == self._revision,
            captured_epoch=token[0],
            captured_revision=token[1],
        )


__all__ = (
    "DocumentRevisionOutcome",
    "DocumentRevisionToken",
    "DocumentRevisionTracker",
)
