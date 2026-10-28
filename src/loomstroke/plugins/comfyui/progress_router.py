"""Bounded, connection-local delivery of progress for individual prompt IDs."""

from __future__ import annotations

import asyncio
import weakref
from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

_OWNED_LIMIT = 64
_ORPHAN_LIMIT = 64
_PROGRESS_LIMIT = 31


class _ProgressRoutingError(RuntimeError):
    """The requested stream cannot be delivered without guessing its owner."""


@dataclass
class _ProgressCapacity:
    # Shared only as a counter: old, draining generations retain their own slots.
    used: int = 0


@dataclass
class _Mailbox:
    progress: deque[tuple[int, dict[str, Any]]] = field(default_factory=deque)
    terminal: tuple[int, dict[str, Any] | _ProgressRoutingError] | None = None
    finished: bool = False
    subscriber: object | None = None
    unscoped_peer: str | None = None
    unscoped_mailbox: weakref.ReferenceType[_Mailbox] | None = None
    unscoped_ambiguous: bool = False
    window_epoch: int = 0
    ready: asyncio.Event = field(default_factory=asyncio.Event)


def _is_terminal(message: dict[str, Any]) -> bool:
    kind = message.get("type")
    if not isinstance(kind, str):
        return False
    if kind in {"execution_success", "execution_complete", "complete", "execution_error", "error"}:
        return True
    data = message.get("data")
    return kind == "executing" and isinstance(data, dict) and data.get("node") is None


class _ProgressRouter:
    """Keep active ends pinned and retain only a finite early-message window."""

    def __init__(self, capacity: _ProgressCapacity):
        self._capacity = capacity
        self._owned: OrderedDict[object, _Mailbox] = OrderedDict()
        self._orphans: OrderedDict[str | None, _Mailbox] = OrderedDict()
        self._epoch = 0
        self._sequence = 0
        self._sealed = False

    @property
    def _retained_message_count(self) -> int:
        return sum(
            len(box.progress) + int(box.terminal is not None)
            for box in (*self._owned.values(), *self._orphans.values())
        )

    def _append(self, box: _Mailbox, message: dict[str, Any] | _ProgressRoutingError) -> None:
        if box.finished:
            return
        self._sequence += 1
        if isinstance(message, _ProgressRoutingError) or _is_terminal(message):
            box.terminal = (self._sequence, message)
            box.finished = True
        elif message.get("type") == "progress":
            if len(box.progress) == _PROGRESS_LIMIT:
                box.progress.popleft()
            box.progress.append((self._sequence, message))
        box.ready.set()

    def _orphan(self, prompt: str | None, box: _Mailbox | None = None) -> _Mailbox:
        if prompt in self._orphans:
            self._orphans.move_to_end(prompt)
            return self._orphans[prompt]
        if len(self._orphans) == _ORPHAN_LIMIT:
            self._orphans.popitem(last=False)
            self._epoch += 1
        result = box if box is not None else _Mailbox()
        result.window_epoch = self._epoch
        self._orphans[prompt] = result
        return result

    def _admit(self) -> None:
        if self._sealed:
            raise _ProgressRoutingError("Progress connection is closed")
        if self._capacity.used == _OWNED_LIMIT:
            for prompt, box in self._owned.items():
                if isinstance(prompt, str) and box.finished and box.subscriber is None:
                    del self._owned[prompt]
                    self._capacity.used -= 1
                    self._orphan(prompt, box)
                    break
        if self._capacity.used == _OWNED_LIMIT:
            raise _ProgressRoutingError("Progress subscription capacity is exhausted")
        self._capacity.used += 1

    def reserve(self) -> tuple[object, int]:
        self._admit()
        ticket = object()
        self._owned[ticket] = _Mailbox()
        return ticket, self._epoch

    def abandon(self, ticket: tuple[object, int]) -> None:
        if self._owned.pop(ticket[0], None) is not None:
            self._capacity.used -= 1

    def claim(self, ticket: tuple[object, int], prompt: str) -> None:
        if self._sealed:
            raise _ProgressRoutingError("Progress connection is closed")
        if prompt in self._owned:
            raise _ProgressRoutingError("Prompt already has a progress owner")
        box = self._owned.get(ticket[0])
        if box is None:
            raise _ProgressRoutingError("Progress reservation is closed")
        early = self._orphans.pop(prompt, None)
        if box.unscoped_ambiguous or (
            box.unscoped_peer is not None and box.unscoped_peer != prompt
        ):
            box.progress.clear()
            box.finished = False
            self._append(box, _ProgressRoutingError("Unscoped progress is ambiguous"))
            peer = self._orphans.get(box.unscoped_peer) or self._owned.get(box.unscoped_peer)
            if (
                peer is not None
                and box.unscoped_mailbox is not None
                and box.unscoped_mailbox() is peer
            ):
                self._append(peer, _ProgressRoutingError("Unscoped progress is ambiguous"))
        elif ticket[1] != self._epoch and (early is None or early.window_epoch > ticket[1]):
            box.progress.clear()
            box.finished = False
            self._append(box, _ProgressRoutingError("The early progress window was lost"))
        elif early is not None:
            records = [*box.progress, *early.progress]
            records.extend(end for end in (box.terminal, early.terminal) if end is not None)
            box.progress.clear()
            box.terminal = None
            box.finished = False
            for sequence, record in sorted(records, key=lambda entry: entry[0]):
                if box.finished:
                    break
                if isinstance(record, _ProgressRoutingError) or _is_terminal(record):
                    box.terminal = (sequence, record)
                    box.finished = True
                else:
                    box.progress.append((sequence, record))
                    if len(box.progress) > _PROGRESS_LIMIT:
                        box.progress.popleft()
        del self._owned[ticket[0]]
        self._owned[prompt] = box

    def _note_early_peer(self, prompt: str, peer: _Mailbox) -> None:
        pending = [
            box
            for key, box in self._owned.items()
            if not isinstance(key, str) and (box.progress or box.terminal is not None)
        ]
        for box in pending:
            if len(pending) != 1 or (
                box.unscoped_peer is not None
                and (
                    box.unscoped_peer != prompt
                    or box.unscoped_mailbox is None
                    or box.unscoped_mailbox() is not peer
                )
            ):
                box.unscoped_ambiguous = True
                if box.unscoped_peer != prompt:
                    self._append(peer, _ProgressRoutingError("Unscoped progress is ambiguous"))
                previous = box.unscoped_mailbox() if box.unscoped_mailbox is not None else None
                current = self._owned.get(box.unscoped_peer) or self._orphans.get(box.unscoped_peer)
                if previous is not None and previous is current:
                    self._append(previous, _ProgressRoutingError("Unscoped progress is ambiguous"))
            else:
                box.unscoped_peer = prompt
                box.unscoped_mailbox = weakref.ref(peer)

    def dispatch(self, message: dict[str, Any], prompt: str | None) -> None:
        if self._sealed or not isinstance(message, dict):
            return
        if message.get("type") != "progress" and not _is_terminal(message):
            return
        if prompt is not None:
            box = self._owned.get(prompt)
            if box is None:
                box = self._orphan(prompt)
            if not box.finished:
                self._note_early_peer(prompt, box)
            self._append(box, message)
            return
        owned = [(key, box) for key, box in self._owned.items() if not box.finished]
        orphans = [(key, box) for key, box in self._orphans.items() if not box.finished]
        # A pending HTTP receipt can identify its single early peer only on claim.
        if len(owned) == len(orphans) == 1:
            ticket, box = owned[0]
            peer, peer_box = orphans[0]
            if (
                not isinstance(ticket, str)
                and isinstance(peer, str)
                and (box.unscoped_peer is None or box.unscoped_peer == peer)
            ):
                box.unscoped_peer = peer
                box.unscoped_mailbox = weakref.ref(peer_box)
                self._append(box, message)
                return
        candidates = [box for _, box in (*owned, *orphans)]
        if len(candidates) > 1:
            for box in candidates:
                self._append(box, _ProgressRoutingError("Unscoped progress is ambiguous"))
        else:
            self._append(candidates[0] if candidates else self._orphan(None), message)

    def _subscribe(self, prompt: str) -> tuple[_Mailbox, object]:
        box = self._owned.get(prompt)
        if box is not None and box.subscriber is not None:
            raise _ProgressRoutingError("Prompt already has an active progress subscriber")
        if box is None:
            self._admit()
            box = self._orphans.pop(prompt, None)
            if box is None and not self._owned:
                box = self._orphans.pop(None, None)
            box = box if box is not None else _Mailbox()
            self._owned[prompt] = box
        subscriber = object()
        box.subscriber = subscriber
        return box, subscriber

    def _release(self, prompt: str, box: _Mailbox, subscriber: object) -> None:
        if self._owned.get(prompt) is box and box.subscriber is subscriber:
            del self._owned[prompt]
            self._capacity.used -= 1

    async def messages(self, prompt: str) -> AsyncIterator[dict[str, Any]]:
        box, subscriber = self._subscribe(prompt)
        try:
            while True:
                if box.progress:
                    yield box.progress.popleft()[1]
                elif box.terminal is not None:
                    _, terminal = box.terminal
                    box.terminal = None
                    self._release(prompt, box, subscriber)
                    if isinstance(terminal, _ProgressRoutingError):
                        raise terminal
                    yield terminal
                    return
                elif self._sealed:
                    return
                else:
                    box.ready.clear()
                    await box.ready.wait()
        finally:
            self._release(prompt, box, subscriber)

    def seal(self) -> None:
        self._sealed = True
        self._orphans.clear()
        for prompt, box in list(self._owned.items()):
            if box.subscriber is None:
                del self._owned[prompt]
                self._capacity.used -= 1
            else:
                box.ready.set()
