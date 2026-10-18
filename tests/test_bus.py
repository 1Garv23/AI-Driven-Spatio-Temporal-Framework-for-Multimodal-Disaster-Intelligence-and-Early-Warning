"""Increment 5 tests — Bus Interface and Contract Tests.

TDD Test Suite (from plan.md lines 202-209):
1. Order preserved per key (key = H3 cell).
2. Independent consumer-group offsets.
3. Replay from offset (seek).
4. Duplicates after simulated crash (at-least-once delivery).
5. Bounded queue pushes back when full (backpressure).
6. One topic per source (topic isolation).
7. Contract suite is reusable against ANY Bus implementation (InMemoryBus, RedpandaBus).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pytest

from disaster_intel.bus import (
    BufferFullError,
    Bus,
    InMemoryBus,
)


class BaseBusContractTests(ABC):
    """Reusable contract test suite that ANY Bus implementation must satisfy.

    Both InMemoryBus and RedpandaBus must inherit and pass this test suite.
    """

    @abstractmethod
    def create_bus(self, max_queue_size: int | None = None) -> Bus:
        """Factory method to create a clean Bus instance for testing."""
        ...

    def test_one_topic_per_source_isolation(self) -> None:
        """Messages published to distinct topics must be strictly isolated."""
        bus = self.create_bus()
        t1 = "telemetry.open-meteo"
        t2 = "telemetry.usgs"

        bus.publish(topic=t1, key="cell_1", payload={"source": "open-meteo", "val": 10.0})
        bus.publish(topic=t2, key="cell_2", payload={"source": "usgs", "val": 4.5})

        msgs_t1 = bus.poll(topic=t1, group_id="sub_1", max_messages=10)
        msgs_t2 = bus.poll(topic=t2, group_id="sub_2", max_messages=10)

        assert len(msgs_t1) == 1
        assert msgs_t1[0].topic == t1
        assert msgs_t1[0].payload["source"] == "open-meteo"

        assert len(msgs_t2) == 1
        assert msgs_t2[0].topic == t2
        assert msgs_t2[0].payload["source"] == "usgs"

    def test_order_preserved_per_key(self) -> None:
        """Messages with the same key (H3 cell ID) must arrive in exact published order."""
        bus = self.create_bus()
        topic = "telemetry.weather"
        cell_key = "8760b2494ffffff"

        # Publish 20 sequential observations for the same H3 cell
        for i in range(20):
            bus.publish(topic=topic, key=cell_key, payload={"seq": i, "temp": 20.0 + i})

        consumed = bus.poll(topic=topic, group_id="verifier", max_messages=50)
        assert len(consumed) == 20

        # Check order per key
        for expected_seq, msg in enumerate(consumed):
            assert msg.key == cell_key
            assert msg.payload["seq"] == expected_seq
            assert msg.offset == expected_seq

    def test_consumer_group_offsets_are_independent(self) -> None:
        """Multiple consumer groups maintain independent read positions on the same topic."""
        bus = self.create_bus()
        topic = "telemetry.stream"

        for i in range(10):
            bus.publish(topic=topic, key=f"cell_{i}", payload={"i": i})

        # Consumer Group A reads 4 messages and commits
        group_a_msgs = bus.poll(topic=topic, group_id="group_a", max_messages=4)
        assert len(group_a_msgs) == 4
        assert [m.offset for m in group_a_msgs] == [0, 1, 2, 3]

        # Consumer Group B reads from start independently
        group_b_msgs = bus.poll(topic=topic, group_id="group_b", max_messages=10)
        assert len(group_b_msgs) == 10
        assert [m.offset for m in group_b_msgs] == list(range(10))

        # Consumer Group A resumes from where it left off
        group_a_resumed = bus.poll(topic=topic, group_id="group_a", max_messages=10)
        assert len(group_a_resumed) == 6
        assert [m.offset for m in group_a_resumed] == [4, 5, 6, 7, 8, 9]

    def test_replay_from_offset_via_seek(self) -> None:
        """A consumer group can seek to any historical offset and replay messages."""
        bus = self.create_bus()
        topic = "telemetry.replay"

        for i in range(5):
            bus.publish(topic=topic, key="key", payload={"val": i})

        # Read all 5
        initial = bus.poll(topic=topic, group_id="replayer", max_messages=10)
        assert len(initial) == 5

        # Seek back to offset 2
        bus.seek(topic=topic, group_id="replayer", offset=2)

        # Poll again: must return offsets 2, 3, 4
        replayed = bus.poll(topic=topic, group_id="replayer", max_messages=10)
        assert len(replayed) == 3
        assert [m.offset for m in replayed] == [2, 3, 4]
        assert [m.payload["val"] for m in replayed] == [2, 3, 4]

    def test_duplicates_after_simulated_crash(self) -> None:
        """Simulates worker crash after partial processing: uncommitted messages re-delivered."""
        bus = self.create_bus()
        topic = "telemetry.orders"

        for i in range(6):
            bus.publish(topic=topic, key="cell", payload={"step": i})

        # Worker polls 4 messages with auto_commit=False
        worker_msgs = bus.poll(topic=topic, group_id="worker", max_messages=4, auto_commit=False)
        assert len(worker_msgs) == 4

        # Worker successfully processes and commits only first 2 messages (offset 0 and 1)
        bus.commit(topic=topic, group_id="worker", offset=1)

        # Simulated crash: worker process restarts, polls using same group_id
        recovered_msgs = bus.poll(topic=topic, group_id="worker", max_messages=10)
        # Should re-deliver starting from offset 2 (messages 2, 3, 4, 5)
        assert len(recovered_msgs) == 4
        assert [m.offset for m in recovered_msgs] == [2, 3, 4, 5]
        assert [m.payload["step"] for m in recovered_msgs] == [2, 3, 4, 5]

    def test_bounded_queue_pushes_back_when_full(self) -> None:
        """Publishing beyond max_queue_size must raise BufferFullError."""
        bus = self.create_bus(max_queue_size=3)
        topic = "telemetry.bounded"

        # First 3 publishes succeed
        bus.publish(topic=topic, key="k1", payload=1)
        bus.publish(topic=topic, key="k2", payload=2)
        bus.publish(topic=topic, key="k3", payload=3)

        # 4th publish must be rejected with backpressure error
        with pytest.raises(BufferFullError, match=r"[Bb]uffer|[Ff]ull|capacity"):
            bus.publish(topic=topic, key="k4", payload=4)

    def test_batch_publish(self) -> None:
        """Batch publish assigns sequential offsets and preserves order."""
        bus = self.create_bus()
        topic = "telemetry.batch"

        items = [(f"cell_{i}", {"data": i}) for i in range(10)]
        offsets = bus.publish_batch(topic=topic, messages=items)

        assert offsets == list(range(10))

        consumed = bus.poll(topic=topic, group_id="batch_reader", max_messages=20)
        assert len(consumed) == 10
        assert [m.offset for m in consumed] == list(range(10))

    def test_empty_poll_returns_empty_list(self) -> None:
        """Polling an empty topic or fully consumed topic returns [] safely."""
        bus = self.create_bus()
        msgs = bus.poll(topic="nonexistent.topic", group_id="g1", max_messages=10)
        assert msgs == []


# ── InMemoryBus Implementation of Contract Suite ────────────────────


@pytest.mark.unit
class TestInMemoryBus(BaseBusContractTests):
    """Verify that InMemoryBus fully satisfies the Bus contract suite."""

    def create_bus(self, max_queue_size: int | None = None) -> Bus:
        return InMemoryBus(max_queue_size=max_queue_size)
