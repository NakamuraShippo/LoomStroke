from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

__all__ = [
    "ConnectionSignalProvider",
    "Signal0",
    "Signal1",
    "Signal2",
    "Signal3",
]

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")


class Signal0(Protocol):
    """Protocol representing a Qt signal without payload."""

    def connect(self, slot: Callable[[], Any]) -> Any: ...

    def disconnect(self, slot: Callable[[], Any]) -> Any: ...

    def emit(self) -> None: ...


class Signal1(Protocol[T]):
    """Protocol representing a Qt signal with a single positional argument."""

    def connect(self, slot: Callable[[T], Any]) -> Any: ...

    def disconnect(self, slot: Callable[[T], Any]) -> Any: ...

    def emit(self, value: T) -> None: ...


class Signal2(Protocol[T, U]):
    """Protocol representing a Qt signal with two positional arguments."""

    def connect(self, slot: Callable[[T, U], Any]) -> Any: ...

    def disconnect(self, slot: Callable[[T, U], Any]) -> Any: ...

    def emit(self, first: T, second: U) -> None: ...


class Signal3(Protocol[T, U, V]):
    """Protocol representing a Qt signal with three positional arguments."""

    def connect(self, slot: Callable[[T, U, V], Any]) -> Any: ...

    def disconnect(self, slot: Callable[[T, U, V], Any]) -> Any: ...

    def emit(self, first: T, second: U, third: V) -> None: ...


@runtime_checkable
class ConnectionSignalProvider(Protocol):
    """Protocol for objects exposing connection lifecycle signals."""

    connection_established: Signal1[str]
    connection_closed: Signal1[str]
    connection_failed: Signal1[str]
