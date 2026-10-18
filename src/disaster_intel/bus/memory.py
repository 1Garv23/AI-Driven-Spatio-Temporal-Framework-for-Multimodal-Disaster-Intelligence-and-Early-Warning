"""In-memory reference implementation of the Bus interface."""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from disaster_intel.bus.base import (
    BufferFullError,
    Bus,
    Message,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class InMemoryBus(Bus):
    """Thread-safe in-memory message bus implementing the full Bus contract.

    Supports:
    - Ordered topic partitions
    - Independent consumer-group offsets
    - Historical replay via seek
    - Bounded queue backpressure via BufferFullError
    """

    def __init__(self, max_queue_size: int | None = None) -> None:
        self.max_queue_size = max_queue_size
        self._topics: dict[str, list[Message]] = defaultdict(list)
        # (topic, group_id) -> next offset to poll from
        self._group_positions: dict[tuple[str, str], int] = {}
        # (topic, group_id) -> last committed offset
        self._group_committed: dict[tuple[str, str], int] = {}
        self._lock = threading.RLock()

    def publish(
        self,
        topic: str,
        key: str,
        payload: Any,
        headers: dict[str, str] | None = None,
    ) -> int:
        with self._lock:
            topic_messages = self._topics[topic]
            if self.max_queue_size is not None and len(topic_messages) >= self.max_queue_size:
                raise BufferFullError(
                    f"Topic '{topic}' buffer is full: capacity is {self.max_queue_size}"
                )

            offset = len(topic_messages)
            msg = Message(
                topic=topic,
                key=key,
                payload=payload,
                offset=offset,
                timestamp=datetime.now(UTC),
                headers=headers or {},
            )
            topic_messages.append(msg)
            return offset

    def publish_batch(
        self,
        topic: str,
        messages: Sequence[tuple[str, Any]],
        headers: dict[str, str] | None = None,
    ) -> list[int]:
        with self._lock:
            topic_messages = self._topics[topic]
            if (
                self.max_queue_size is not None
                and len(topic_messages) + len(messages) > self.max_queue_size
            ):
                raise BufferFullError(
                    f"Topic '{topic}' cannot accept batch of {len(messages)}: "
                    f"capacity is {self.max_queue_size}, currently {len(topic_messages)}"
                )

            offsets: list[int] = []
            now = datetime.now(UTC)
            base_headers = headers or {}

            for key, payload in messages:
                offset = len(topic_messages)
                msg = Message(
                    topic=topic,
                    key=key,
                    payload=payload,
                    offset=offset,
                    timestamp=now,
                    headers=base_headers,
                )
                topic_messages.append(msg)
                offsets.append(offset)

            return offsets

    def poll(
        self,
        topic: str,
        group_id: str,
        max_messages: int = 100,
        auto_commit: bool = True,
    ) -> list[Message]:
        with self._lock:
            if topic not in self._topics:
                return []

            topic_messages = self._topics[topic]
            group_key = (topic, group_id)

            # Start reading from current position (or from 0 if group is new)
            pos = self._group_positions.get(group_key, 0)
            if pos >= len(topic_messages):
                return []

            batch = topic_messages[pos : pos + max_messages]
            new_pos = pos + len(batch)
            self._group_positions[group_key] = new_pos

            if auto_commit and batch:
                self._group_committed[group_key] = batch[-1].offset

            return list(batch)

    def commit(
        self,
        topic: str,
        group_id: str,
        offset: int | None = None,
    ) -> None:
        with self._lock:
            group_key = (topic, group_id)
            if offset is not None:
                self._group_committed[group_key] = offset
                # If explicitly committing offset K, next message to poll should be K + 1
                self._group_positions[group_key] = offset + 1
            else:
                current_pos = self._group_positions.get(group_key, 0)
                if current_pos > 0:
                    self._group_committed[group_key] = current_pos - 1

    def seek(
        self,
        topic: str,
        group_id: str,
        offset: int,
    ) -> None:
        with self._lock:
            group_key = (topic, group_id)
            if offset < 0:
                raise ValueError(f"Offset cannot be negative: {offset}")
            self._group_positions[group_key] = offset

    def get_offset(
        self,
        topic: str,
        group_id: str,
    ) -> int:
        with self._lock:
            return self._group_committed.get((topic, group_id), -1)

    def close(self) -> None:
        """Clear all in-memory buffers."""
        with self._lock:
            self._topics.clear()
            self._group_positions.clear()
            self._group_committed.clear()
