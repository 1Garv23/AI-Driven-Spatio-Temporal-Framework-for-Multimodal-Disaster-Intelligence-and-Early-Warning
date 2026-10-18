"""Base abstractions and data structures for message streaming bus."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


class BusError(Exception):
    """Base error for message bus operations."""


class BufferFullError(BusError):
    """Raised when publishing to a bounded topic that has reached capacity."""


class TopicNotFoundError(BusError):
    """Raised when querying an unknown topic."""


@dataclass(frozen=True, slots=True)
class Message:
    """A single record on the message bus."""

    topic: str
    key: str
    payload: Any
    offset: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    headers: dict[str, str] = field(default_factory=dict)


class Bus(ABC):
    """Abstract interface defining the message bus contract."""

    @abstractmethod
    def publish(
        self,
        topic: str,
        key: str,
        payload: Any,
        headers: dict[str, str] | None = None,
    ) -> int:
        """Publish a message to a topic. Returns the assigned offset."""
        ...

    @abstractmethod
    def publish_batch(
        self,
        topic: str,
        messages: Sequence[tuple[str, Any]],
        headers: dict[str, str] | None = None,
    ) -> list[int]:
        """Publish a batch of (key, payload) tuples. Returns assigned offsets."""
        ...

    @abstractmethod
    def poll(
        self,
        topic: str,
        group_id: str,
        max_messages: int = 100,
        auto_commit: bool = True,
    ) -> list[Message]:
        """Poll up to max_messages for a consumer group."""
        ...

    @abstractmethod
    def commit(
        self,
        topic: str,
        group_id: str,
        offset: int | None = None,
    ) -> None:
        """Commit consumer group offset."""
        ...

    @abstractmethod
    def seek(
        self,
        topic: str,
        group_id: str,
        offset: int,
    ) -> None:
        """Reset consumer group read position to a specific offset."""
        ...

    @abstractmethod
    def get_offset(
        self,
        topic: str,
        group_id: str,
    ) -> int:
        """Return the current committed offset for the consumer group (-1 if none)."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Clean up bus connections or resources."""
        ...
