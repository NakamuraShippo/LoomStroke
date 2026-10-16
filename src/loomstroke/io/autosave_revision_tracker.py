"""Dirty-revision state machine for periodic autosave (AUTOSAVE-01B).

Pure Python, no Qt / timer / filesystem / logging dependencies: the
:class:`AutosaveRevisionTracker` only answers "is there anything new to
autosave, and which state would this run cover?".  ``AutosaveManager``
owns the QTimer / EventBus wiring and translates events into these
transitions:

- ``mark_modified()``   — every ``DOC_CHANGED`` (O(1), holds no payload)
- ``mark_manual_save()``— a successful normal (non-autosave) save
- ``reset_document()``  — document created / loaded / closed (new epoch)
- ``begin_autosave()``  — a periodic tick or ``force_save()`` wants to run
- ``complete_success()`` / ``complete_failure()`` — the run finished

Tokens are ``(document_epoch, revision)`` pairs.  The epoch guards
against a slow autosave of the PREVIOUS document completing after a
load/new/close and wrongly marking the NEW document clean; revisions are
monotonic so a stale completion can never move coverage backwards.
Edits that happen while an autosave is in flight keep the tracker dirty
(current > covered) so the next periodic tick captures them exactly once.
"""

from __future__ import annotations

AutosaveToken = tuple[int, int]
"""``(document_epoch, revision)`` captured when an autosave run begins."""


class AutosaveRevisionTracker:
    """Track document dirtiness and single-flight ownership for autosave."""

    def __init__(self) -> None:
        self._epoch = 0
        self._current_revision = 0
        self._covered_revision = 0
        self._in_flight_token: AutosaveToken | None = None

    # -- inspection --------------------------------------------------------
    @property
    def document_epoch(self) -> int:
        return self._epoch

    @property
    def current_revision(self) -> int:
        return self._current_revision

    @property
    def covered_revision(self) -> int:
        return self._covered_revision

    @property
    def is_dirty(self) -> bool:
        """True when edits exist that no recovery snapshot has covered."""

        return self._current_revision > self._covered_revision

    @property
    def is_in_flight(self) -> bool:
        return self._in_flight_token is not None

    # -- transitions -------------------------------------------------------
    def mark_modified(self) -> AutosaveToken:
        """Record one document modification; never touches timers or I/O."""

        self._current_revision += 1
        return (self._epoch, self._current_revision)

    def mark_manual_save(self) -> None:
        """A successful normal save covers everything up to now.

        Coverage is monotonic: a stale notification can never move it
        backwards below what an autosave already covered.
        """

        self._covered_revision = max(self._covered_revision, self._current_revision)

    def reset_document(self) -> None:
        """Enter a new document epoch (created / loaded / closed), clean.

        An in-flight autosave of the previous epoch keeps its token; its
        completion still releases the flight but cannot touch the new
        epoch's coverage (the token epoch no longer matches).
        """

        self._epoch += 1
        self._current_revision = 0
        self._covered_revision = 0

    def begin_autosave(self, *, force: bool = False) -> AutosaveToken | None:
        """Claim the single autosave flight; ``None`` means "do not run".

        Refuses while another run is in flight (even for ``force``), and
        refuses clean states unless ``force`` is set.  The returned token
        freezes the state this run will cover; edits arriving afterwards
        raise ``current_revision`` past it and keep the tracker dirty.
        """

        if self._in_flight_token is not None:
            return None
        if not force and not self.is_dirty:
            return None
        token = (self._epoch, self._current_revision)
        self._in_flight_token = token
        return token

    def complete_success(self, token: AutosaveToken) -> None:
        """The run for ``token`` wrote its snapshot successfully.

        A completion is applied only when ``token`` is the exact in-flight
        token — a stale or mismatched claim is ignored entirely, so it can
        neither free somebody else's run nor advance coverage.  Coverage
        additionally requires the current epoch: a run from before a
        document switch releases its flight without marking the new
        document clean.
        """

        if self._in_flight_token != token:
            return
        self._in_flight_token = None
        epoch, revision = token
        if epoch == self._epoch and revision > self._covered_revision:
            self._covered_revision = revision

    def complete_failure(self, token: AutosaveToken) -> None:
        """The run for ``token`` failed or was cancelled: stay dirty.

        Coverage is untouched so the next periodic tick retries; the
        flight is released only for the matching token so a stale
        completion cannot free somebody else's run.
        """

        if self._in_flight_token == token:
            self._in_flight_token = None


__all__ = ("AutosaveRevisionTracker", "AutosaveToken")
