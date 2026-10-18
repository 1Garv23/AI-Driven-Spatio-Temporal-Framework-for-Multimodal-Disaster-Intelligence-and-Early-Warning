"""Increment 6 tests — End-to-End Ingestion Pipeline and Objective 1 Metrics.

TDD Test Suite (from plan.md lines 210-217):
1. Fake adapter -> sink gives expected observations.
2. Bad record counted (not fatal).
3. Duplicates dropped by idempotency key.
4. Latency p50/p95/p99 correct on known inputs.
5. Completion rate attributes failures to source or parse.
6. CLI `ingest --replay <fixture>` fills sink and prints metrics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from disaster_intel.adapters.base import AdapterMetrics, SourceAdapter
from disaster_intel.bus.memory import InMemoryBus
from disaster_intel.clock import FakeClock
from disaster_intel.discretizers import SpatialDiscretizer
from disaster_intel.grid import CoastalGrid
from disaster_intel.models import (
    PointGeometry,
    RawReading,
)
from disaster_intel.pipeline import (
    IngestionMetrics,
    IngestionPipeline,
    InMemorySink,
    calculate_percentiles,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeSourceAdapter(SourceAdapter):
    """Deterministic in-memory adapter for pipeline testing."""

    source_name: str = "fake-source"

    def __init__(self, readings: list[RawReading] | None = None) -> None:
        self.metrics = AdapterMetrics()
        self._readings = readings or []

    def parse_payload(self, payload: Any) -> list[RawReading]:
        return self._readings

    def fetch_readings(self) -> list[RawReading]:
        self.metrics.requests_made += 1
        self.metrics.readings_produced += len(self._readings)
        return self._readings


@pytest.fixture(scope="module")
def coastal_grid() -> CoastalGrid:
    return CoastalGrid(use_cache=True)


# ── Ingestion Pipeline Unit Tests ────────────────────────────────────


@pytest.mark.unit
class TestIngestionPipeline:
    """Verifies end-to-end data flow: Adapter -> Discretizer -> Bus -> Sink."""

    def test_pipeline_delivers_observations_to_sink(self, coastal_grid: CoastalGrid) -> None:
        clock = FakeClock()
        bus = InMemoryBus()
        sink = InMemorySink()
        discretizer = SpatialDiscretizer(grid=coastal_grid)
        ts = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)

        reading = RawReading(
            source="usgs",
            variable="seismic_magnitude",
            value=4.2,
            timestamp=ts,
            geometry=PointGeometry(lat=13.0, lon=75.0),
            unit="magnitude",
        )
        adapter = FakeSourceAdapter([reading])

        pipeline = IngestionPipeline(
            adapter=adapter,
            discretizer=discretizer,
            bus=bus,
            sink=sink,
            clock=clock,
        )

        metrics = pipeline.run_once()

        assert metrics.raw_readings_received == 1
        assert metrics.observations_produced == 1
        assert metrics.observations_stored == 1
        assert sink.count() == 1

        stored = sink.get_by_cell(coastal_grid.latlng_to_cell(13.0, 75.0))
        assert len(stored) == 1
        assert stored[0].value == 4.2
        assert stored[0].variable == "seismic_magnitude"

    def test_bad_record_counted_and_not_fatal(self, coastal_grid: CoastalGrid) -> None:
        """A corrupt record must be recorded in metrics without crashing the pipeline."""
        clock = FakeClock()
        bus = InMemoryBus()
        sink = InMemorySink()
        discretizer = SpatialDiscretizer(grid=coastal_grid)
        ts = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)

        valid_reading = RawReading(
            source="usgs",
            variable="seismic_magnitude",
            value=3.5,
            timestamp=ts,
            geometry=PointGeometry(lat=13.0, lon=75.0),
            unit="magnitude",
        )
        adapter = FakeSourceAdapter([valid_reading])

        pipeline = IngestionPipeline(
            adapter=adapter,
            discretizer=discretizer,
            bus=bus,
            sink=sink,
            clock=clock,
        )

        # Inject a bad raw item that causes discretization/processing failure
        bad_item = {"invalid": "payload missing geometry"}

        pipeline.process_raw_items([valid_reading, bad_item])

        assert pipeline.metrics.raw_readings_received == 2
        assert pipeline.metrics.parse_errors == 1
        assert pipeline.metrics.observations_stored == 1
        assert sink.count() == 1

    def test_duplicates_dropped_by_idempotency_key(self, coastal_grid: CoastalGrid) -> None:
        """Identical observations processed twice must be deduplicated by idempotency key."""
        clock = FakeClock()
        bus = InMemoryBus()
        sink = InMemorySink()
        discretizer = SpatialDiscretizer(grid=coastal_grid)
        ts = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)

        reading = RawReading(
            source="open-meteo",
            variable="precipitation",
            value=10.0,
            timestamp=ts,
            geometry=PointGeometry(lat=13.0, lon=75.0),
            unit="mm",
        )
        adapter = FakeSourceAdapter([reading])

        pipeline = IngestionPipeline(
            adapter=adapter,
            discretizer=discretizer,
            bus=bus,
            sink=sink,
            clock=clock,
        )

        # Run pipeline twice with same input
        pipeline.run_once()
        pipeline.run_once()

        assert pipeline.metrics.raw_readings_received == 2
        assert pipeline.metrics.observations_produced == 2
        # Exactly 1 observation kept in sink, 1 duplicate dropped
        assert sink.count() == 1
        assert pipeline.metrics.duplicates_dropped == 1


# ── Objective 1 Metrics & Latency Tests ──────────────────────────────


@pytest.mark.unit
class TestObjective1Metrics:
    """Verifies calculation of latency percentiles and failure attribution."""

    def test_latency_percentiles_calculation(self) -> None:
        # Array of 100 latencies from 1.0 to 100.0 ms
        latencies = [float(i) for i in range(1, 101)]

        p50, p95, p99 = calculate_percentiles(latencies, [50, 95, 99])

        assert 49.5 <= p50 <= 51.5
        assert 94.0 <= p95 <= 96.0
        assert 98.0 <= p99 <= 100.0

    def test_latency_tracked_during_pipeline_run(self, coastal_grid: CoastalGrid) -> None:
        clock = FakeClock(datetime(2024, 7, 15, 12, 0, 0, tzinfo=UTC))
        bus = InMemoryBus()
        sink = InMemorySink()
        discretizer = SpatialDiscretizer(grid=coastal_grid)

        # Event observed at 11:59:59 (1 second = 1000 ms before processing)
        event_time = datetime(2024, 7, 15, 11, 59, 59, tzinfo=UTC)
        reading = RawReading(
            source="usgs",
            variable="seismic_magnitude",
            value=3.0,
            timestamp=event_time,
            geometry=PointGeometry(lat=13.0, lon=75.0),
            unit="magnitude",
        )
        adapter = FakeSourceAdapter([reading])

        pipeline = IngestionPipeline(
            adapter=adapter,
            discretizer=discretizer,
            bus=bus,
            sink=sink,
            clock=clock,
        )

        metrics = pipeline.run_once()

        assert len(metrics.latencies_ms) == 1
        assert metrics.latencies_ms[0] == 1000.0
        assert metrics.p50_latency_ms == 1000.0

    def test_completion_rate_and_failure_attribution(self) -> None:
        metrics = IngestionMetrics()
        metrics.raw_readings_received = 100
        metrics.observations_stored = 85
        metrics.source_errors = 10
        metrics.parse_errors = 5

        # 85 successful out of 100 = 85.0%
        assert metrics.completion_rate == 85.0
        assert metrics.has_failures is True
        assert metrics.source_errors == 10
        assert metrics.parse_errors == 5


# ── Replay Fixture CLI Test ──────────────────────────────────────────


@pytest.mark.unit
class TestReplayFixtureCLI:
    """Verifies that replay fixture mode runs cleanly from the CLI entry point."""

    def test_replay_fixture_execution(self, coastal_grid: CoastalGrid) -> None:
        from disaster_intel.pipeline import run_replay_fixture

        fixture_file = FIXTURES_DIR / "open_meteo_sample.json"
        assert fixture_file.is_file()

        metrics = run_replay_fixture(
            fixture_path=fixture_file,
            grid=coastal_grid,
        )

        # 4 timestamps * 6 variables = 24 raw readings
        assert metrics.raw_readings_received == 24
        # Grid node fans out to ~20 cells -> ~480 observations
        assert metrics.observations_produced > 24
        assert metrics.observations_stored == metrics.observations_produced
        assert metrics.completion_rate == 100.0
        assert metrics.source_errors == 0
        assert metrics.parse_errors == 0
