"""Increment 3 tests — SourceAdapter Interface and Open-Meteo Adapter.

TDD Test Suite (from plan.md lines 185-188):
1. Recorded response -> exact expected readings (golden-parse test).
2. Null hours skipped with counted reason.
3. Request batching under call budget.
4. 429 & 5xx retried with backoff on fake clock.
5. Malformed JSON -> ParseError.
6. Offline execution from fixtures; one @live test passes by hand.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from disaster_intel.adapters import (
    AdapterMetrics,
    MockHttpTransport,
    OpenMeteoAdapter,
    ParseError,
    SourceError,
)
from disaster_intel.clock import FakeClock

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── Golden parse and fixture tests ──────────────────────────────────


@pytest.mark.unit
class TestOpenMeteoGoldenParse:
    """Recorded response must parse into exact expected RawReading records."""

    def test_golden_parse_from_fixture(self) -> None:
        fixture_path = FIXTURES_DIR / "open_meteo_sample.json"
        raw_payload = json.loads(fixture_path.read_text(encoding="utf-8"))

        adapter = OpenMeteoAdapter()
        readings = adapter.parse_payload(raw_payload)

        # 4 timestamps * 6 hourly variables = 24 readings
        assert len(readings) == 24

        # Verify all readings have source="open-meteo" and grid_node geometry
        for r in readings:
            assert r.source == "open-meteo"
            assert r.geometry.kind == "grid_node"
            assert r.geometry.lat == 13.0
            assert r.geometry.lon == 74.8
            assert r.geometry.resolution_deg == 0.1
            assert r.timestamp.tzinfo == UTC

        # Check first precipitation reading
        precip_0 = next(
            r
            for r in readings
            if r.variable == "precipitation"
            and r.timestamp == datetime(2024, 7, 15, 0, 0, tzinfo=UTC)
        )
        assert precip_0.value == 0.0
        assert precip_0.unit == "mm"

        # Check third precipitation reading
        precip_2 = next(
            r
            for r in readings
            if r.variable == "precipitation"
            and r.timestamp == datetime(2024, 7, 15, 2, 0, tzinfo=UTC)
        )
        assert precip_2.value == 5.8

        # Check temperature reading
        temp_0 = next(
            r
            for r in readings
            if r.variable == "temperature"
            and r.timestamp == datetime(2024, 7, 15, 0, 0, tzinfo=UTC)
        )
        assert temp_0.value == 26.5
        assert temp_0.unit == "celsius"

        # Check surface soil moisture reading
        sm_surf_0 = next(
            r
            for r in readings
            if r.variable == "soil_moisture_surface"
            and r.timestamp == datetime(2024, 7, 15, 0, 0, tzinfo=UTC)
        )
        assert sm_surf_0.value == 0.38
        assert sm_surf_0.unit == "m3/m3"

        # Check rootzone soil moisture reading
        sm_root_0 = next(
            r
            for r in readings
            if r.variable == "soil_moisture_rootzone"
            and r.timestamp == datetime(2024, 7, 15, 0, 0, tzinfo=UTC)
        )
        assert sm_root_0.value == 0.35
        assert sm_root_0.unit == "m3/m3"

        # Check wind speed reading
        wind_0 = next(
            r
            for r in readings
            if r.variable == "wind_speed"
            and r.timestamp == datetime(2024, 7, 15, 0, 0, tzinfo=UTC)
        )
        assert wind_0.value == 12.4
        assert wind_0.unit == "km/h"

        # Check pressure reading
        pres_0 = next(
            r
            for r in readings
            if r.variable == "pressure" and r.timestamp == datetime(2024, 7, 15, 0, 0, tzinfo=UTC)
        )
        assert pres_0.value == 1008.2
        assert pres_0.unit == "hPa"


# ── Null hours skipping and metrics ─────────────────────────────────


@pytest.mark.unit
class TestOpenMeteoNullHandling:
    """Null hours must be skipped with counted reason in metrics."""

    def test_null_hours_skipped_with_counter(self) -> None:
        fixture_path = FIXTURES_DIR / "open_meteo_with_nulls.json"
        raw_payload = json.loads(fixture_path.read_text(encoding="utf-8"))

        adapter = OpenMeteoAdapter()
        readings = adapter.parse_payload(raw_payload)

        # 3 timestamps * 6 variables = 18 possible values. 6 are null -> 12 valid readings
        assert len(readings) == 12
        assert adapter.metrics.records_skipped.get("null_hour") == 6
        assert adapter.metrics.readings_produced == 12

        # Verify no readings contain None or NaN
        for r in readings:
            assert r.value is not None


# ── Malformed payload parsing ───────────────────────────────────────


@pytest.mark.unit
class TestOpenMeteoMalformedPayload:
    """Malformed JSON or missing schema fields must raise ParseError."""

    def test_missing_hourly_section_raises_parse_error(self) -> None:
        adapter = OpenMeteoAdapter()
        bad_payload = {"latitude": 13.0, "longitude": 74.8}

        with pytest.raises(ParseError, match=r"[Hh]ourly"):
            adapter.parse_payload(bad_payload)

        assert adapter.metrics.parse_errors == 1

    def test_missing_time_array_raises_parse_error(self) -> None:
        adapter = OpenMeteoAdapter()
        bad_payload = {
            "latitude": 13.0,
            "longitude": 74.8,
            "hourly": {"precipitation": [1.0, 2.0]},
        }

        with pytest.raises(ParseError, match=r"[Tt]ime"):
            adapter.parse_payload(bad_payload)

        assert adapter.metrics.parse_errors == 1

    def test_mismatched_array_lengths_raises_parse_error(self) -> None:
        adapter = OpenMeteoAdapter()
        bad_payload = {
            "latitude": 13.0,
            "longitude": 74.8,
            "hourly": {
                "time": ["2024-07-15T00:00", "2024-07-15T01:00"],
                "precipitation": [1.0],  # only 1 value for 2 times!
            },
        }

        with pytest.raises(ParseError, match=r"[Ll]ength"):
            adapter.parse_payload(bad_payload)

        assert adapter.metrics.parse_errors == 1


# ── Request batching under call budget ───────────────────────────────


@pytest.mark.unit
class TestOpenMeteoBatching:
    """Multiple coordinates must be batched under call budget."""

    def test_batching_under_call_budget(self) -> None:
        clock = FakeClock()
        # 125 coordinates
        coords = [(12.0 + i * 0.1, 74.0 + i * 0.1) for i in range(125)]
        batch_size = 50

        # Create dummy mock transport that responds 200 for each batch
        recorded_requests: list[dict[str, Any]] = []

        def mock_handler(request_dict: dict[str, Any]) -> dict[str, Any]:
            recorded_requests.append(request_dict)
            # Count how many coords requested in this batch
            lats = str(request_dict["params"]["latitude"]).split(",")
            # Return list of responses matching coords count
            return {
                "status_code": 200,
                "json": [
                    {
                        "latitude": float(lat),
                        "longitude": 74.0,
                        "hourly": {
                            "time": ["2024-07-15T00:00"],
                            "precipitation": [0.0],
                        },
                    }
                    for lat in lats
                ],
            }

        transport = MockHttpTransport(handler=mock_handler)
        adapter = OpenMeteoAdapter(clock=clock, transport=transport, batch_size=batch_size)

        readings = adapter.fetch_grid_nodes(
            coords=coords,
            start_date="2024-07-15",
            end_date="2024-07-15",
        )

        # 125 coords with batch size 50 -> 3 batches (50, 50, 25)
        assert len(recorded_requests) == 3
        assert adapter.metrics.requests_made == 3
        assert len(readings) == 125


# ── Retries with exponential backoff on 429 and 5xx ─────────────────


@pytest.mark.unit
class TestOpenMeteoRetries:
    """429 and 5xx errors must be retried with backoff on fake clock."""

    def test_429_retried_with_backoff_and_succeeds(self) -> None:
        clock = FakeClock()
        call_count = 0

        def mock_handler(request_dict: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {"status_code": 429, "text": "Too Many Requests"}
            return {
                "status_code": 200,
                "json": {
                    "latitude": 13.0,
                    "longitude": 74.8,
                    "hourly": {
                        "time": ["2024-07-15T00:00"],
                        "precipitation": [1.5],
                    },
                },
            }

        transport = MockHttpTransport(handler=mock_handler)
        adapter = OpenMeteoAdapter(
            clock=clock,
            transport=transport,
            max_retries=3,
            initial_backoff_s=1.0,
        )

        readings = adapter.fetch_grid_nodes(
            coords=[(13.0, 74.8)],
            start_date="2024-07-15",
            end_date="2024-07-15",
        )

        assert len(readings) == 1
        assert readings[0].value == 1.5
        assert call_count == 3
        assert adapter.metrics.retries == 2
        # Backoff on fake clock should be 1.0s then 2.0s
        assert clock.sleep_log == [1.0, 2.0]

    def test_503_retried_with_backoff_and_succeeds(self) -> None:
        clock = FakeClock()
        call_count = 0

        def mock_handler(request_dict: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"status_code": 503, "text": "Service Unavailable"}
            return {
                "status_code": 200,
                "json": {
                    "latitude": 13.0,
                    "longitude": 74.8,
                    "hourly": {
                        "time": ["2024-07-15T00:00"],
                        "precipitation": [0.0],
                    },
                },
            }

        transport = MockHttpTransport(handler=mock_handler)
        adapter = OpenMeteoAdapter(
            clock=clock,
            transport=transport,
            max_retries=3,
            initial_backoff_s=0.5,
        )

        readings = adapter.fetch_grid_nodes(
            coords=[(13.0, 74.8)],
            start_date="2024-07-15",
            end_date="2024-07-15",
        )

        assert len(readings) == 1
        assert call_count == 2
        assert adapter.metrics.retries == 1
        assert clock.sleep_log == [0.5]

    def test_retries_exhausted_raises_source_error(self) -> None:
        clock = FakeClock()

        def mock_handler(request_dict: dict[str, Any]) -> dict[str, Any]:
            return {"status_code": 500, "text": "Internal Server Error"}

        transport = MockHttpTransport(handler=mock_handler)
        adapter = OpenMeteoAdapter(
            clock=clock,
            transport=transport,
            max_retries=2,
            initial_backoff_s=1.0,
        )

        with pytest.raises(SourceError, match=r"500"):
            adapter.fetch_grid_nodes(
                coords=[(13.0, 74.8)],
                start_date="2024-07-15",
                end_date="2024-07-15",
            )

        assert adapter.metrics.source_errors == 1
        assert adapter.metrics.retries == 2


# ── Failure attribution ─────────────────────────────────────────────


@pytest.mark.unit
class TestFailureAttribution:
    """Errors must be attributed to either source error or parse error."""

    def test_metrics_attribute_failures_correctly(self) -> None:
        metrics = AdapterMetrics()
        metrics.record_source_error()
        metrics.record_source_error()
        metrics.record_parse_error()

        assert metrics.source_errors == 2
        assert metrics.parse_errors == 1
        assert metrics.has_errors is True


# ── Live test (never run in CI) ─────────────────────────────────────


@pytest.mark.live
def test_live_open_meteo_api_fetch() -> None:
    """Live test hitting Open-Meteo API for Mangalore (12.91°N, 74.85°E).

    Runs only when explicitly invoked: pytest -m live tests/test_adapters.py
    """
    adapter = OpenMeteoAdapter()
    readings = adapter.fetch_grid_nodes(
        coords=[(12.91, 74.85)],
        past_days=1,
        forecast_days=0,
    )
    assert len(readings) > 0
    assert all(r.source == "open-meteo" for r in readings)
    print(f"\nLive Open-Meteo test retrieved {len(readings)} readings successfully.")
