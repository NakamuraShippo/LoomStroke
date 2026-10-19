"""Bounded worker-owned buffers reused after a completed native batch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _Buffer:
    handle: Any
    size: int
    usage: int
    busy: bool = False


class NativeCmykBufferPool:
    LIMIT = 128 * 1024 * 1024

    def __init__(self, bundle: Any) -> None:
        self.bundle = bundle
        self._buffers: list[_Buffer] = []
        self.allocated_bytes = 0
        self.created = 0

    def acquire(self, size: int, usage: int) -> Any:
        available = [
            item
            for item in self._buffers
            if not item.busy and item.usage == usage and item.size >= size
        ]
        if available:
            item = min(available, key=lambda candidate: candidate.size)
        else:
            for cached in list(self._buffers):
                if self.allocated_bytes + size <= self.LIMIT:
                    break
                if not cached.busy:
                    self.allocated_bytes -= cached.size
                    cached.handle.destroy()
                    self._buffers.remove(cached)
            if self.allocated_bytes + size > self.LIMIT:
                raise ValueError("CMYK batch exceeds the GPU working-memory budget")
            handle = self.bundle.device.create_buffer(size=size, usage=usage)
            item = _Buffer(handle, size, usage)
            self._buffers.append(item)
            self.allocated_bytes += size
            self.created += 1
        item.busy = True
        return item.handle

    def finish(self) -> None:
        for item in self._buffers:
            item.busy = False

    def release(self) -> None:
        for item in self._buffers:
            item.handle.destroy()
        self._buffers.clear()
        self.allocated_bytes = 0
