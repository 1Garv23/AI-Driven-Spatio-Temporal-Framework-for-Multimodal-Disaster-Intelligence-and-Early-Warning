"""Open-Meteo weather telemetry adapter.

Handles fetching and parsing hourly weather grid data:
- Precipitation
- Surface and rootzone soil moisture
- Temperature
- Wind speed
- Surface pressure

Features:
- Multi-location batching under request call budget
- Retries with exponential backoff on 429 / 5xx via injected Clock
- Null hour skipping with granular attribution in metrics
- Offline parsing from recorded fixtures
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from disaster_intel.adapters.base import (
    AdapterMetrics,
    HttpTransport,
    HttpxTransport,
    ParseError,
    SourceAdapter,
    SourceError,
)
from disaster_intel.clock import Clock, SystemClock
from disaster_intel.models import GridNodeGeometry, RawReading

# Mapping from Open-Meteo hourly parameter name to (catalog_variable_name, unit)
OPEN_METEO_VARIABLE_MAP: dict[str, tuple[str, str]] = {
    "precipitation": ("precipitation", "mm"),
    "soil_moisture_0_to_7cm": ("soil_moisture_surface", "m3/m3"),
    "soil_moisture_7_to_28cm": ("soil_moisture_rootzone", "m3/m3"),
    "temperature_2m": ("temperature", "celsius"),
    "wind_speed_10m": ("wind_speed", "km/h"),
    "surface_pressure": ("pressure", "hPa"),
}


class OpenMeteoAdapter(SourceAdapter):
    """Adapter for Open-Meteo hourly forecast and historical APIs."""

    source_name: str = "open-meteo"

    def __init__(
        self,
        clock: Clock | None = None,
        transport: HttpTransport | None = None,
        base_url: str = "https://api.open-meteo.com/v1/forecast",
        batch_size: int = 50,
        max_retries: int = 3,
        initial_backoff_s: float = 1.0,
        backoff_multiplier: float = 2.0,
    ) -> None:
        self.clock = clock or SystemClock()
        self.transport = transport or HttpxTransport()
        self.base_url = base_url
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.initial_backoff_s = initial_backoff_s
        self.backoff_multiplier = backoff_multiplier
        self.metrics = AdapterMetrics()

    def parse_payload(self, payload: Any) -> list[RawReading]:
        """Parse raw Open-Meteo JSON payload into a list of RawReading records.

        Supports single location dict or multi-location list.
        """
        try:
            if isinstance(payload, list):
                readings: list[RawReading] = []
                for item in payload:
                    readings.extend(self._parse_single_location(item))
                return readings
            elif isinstance(payload, dict):
                return self._parse_single_location(payload)
            else:
                raise ParseError(f"Expected dict or list payload, got {type(payload).__name__}")
        except ParseError:
            self.metrics.record_parse_error()
            raise
        except Exception as exc:
            self.metrics.record_parse_error()
            raise ParseError(f"Unexpected parsing error: {exc}") from exc

    def _parse_single_location(self, data: dict[str, Any]) -> list[RawReading]:
        if "hourly" not in data or not isinstance(data["hourly"], dict):
            raise ParseError("Missing 'hourly' section in Open-Meteo payload")

        hourly = data["hourly"]
        if "time" not in hourly or not isinstance(hourly["time"], list):
            raise ParseError("Missing 'time' array in Open-Meteo hourly section")

        time_array = hourly["time"]
        lat = float(data.get("latitude", 0.0))
        lon = float(data.get("longitude", 0.0))
        geom = GridNodeGeometry(lat=lat, lon=lon, resolution_deg=0.1)

        # Validate array lengths for any present variables
        for om_var in OPEN_METEO_VARIABLE_MAP:
            if om_var in hourly:
                var_array = hourly[om_var]
                if len(var_array) != len(time_array):
                    raise ParseError(
                        f"Array length mismatch for variable {om_var}: "
                        f"{len(var_array)} != {len(time_array)}"
                    )

        readings: list[RawReading] = []
        for i, time_str in enumerate(time_array):
            # Parse ISO timestamp and normalize to UTC
            dt = datetime.fromisoformat(time_str)
            dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

            for om_var, (catalog_var, unit) in OPEN_METEO_VARIABLE_MAP.items():
                if om_var not in hourly:
                    continue

                raw_val = hourly[om_var][i]
                if raw_val is None:
                    self.metrics.record_skip("null_hour")
                    continue

                reading = RawReading(
                    source=self.source_name,
                    variable=catalog_var,
                    value=float(raw_val),
                    timestamp=dt,
                    geometry=geom,
                    unit=unit,
                )
                self.metrics.readings_produced += 1
                readings.append(reading)

        return readings

    def fetch_grid_nodes(
        self,
        coords: list[tuple[float, float]],
        start_date: str | None = None,
        end_date: str | None = None,
        past_days: int | None = None,
        forecast_days: int | None = None,
    ) -> list[RawReading]:
        """Fetch weather data for multiple grid node coordinates, batched under budget."""
        all_readings: list[RawReading] = []

        # Split coordinates into batches
        for i in range(0, len(coords), self.batch_size):
            batch = coords[i : i + self.batch_size]
            lats_str = ",".join(str(lat) for lat, _ in batch)
            lons_str = ",".join(str(lon) for _, lon in batch)

            params: dict[str, Any] = {
                "latitude": lats_str,
                "longitude": lons_str,
                "hourly": ",".join(OPEN_METEO_VARIABLE_MAP.keys()),
                "timezone": "UTC",
            }
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            if past_days is not None:
                params["past_days"] = past_days
            if forecast_days is not None:
                params["forecast_days"] = forecast_days

            payload = self._fetch_with_retries(params)
            self.metrics.requests_made += 1
            batch_readings = self.parse_payload(payload)
            all_readings.extend(batch_readings)

        return all_readings

    def _fetch_with_retries(self, params: dict[str, Any]) -> Any:
        """Execute HTTP request with exponential backoff on 429 and 5xx."""
        attempt = 0
        while True:
            try:
                response = self.transport.get(self.base_url, params=params)
            except Exception as exc:
                if attempt < self.max_retries:
                    self.metrics.retries += 1
                    sleep_s = self.initial_backoff_s * (self.backoff_multiplier**attempt)
                    attempt += 1
                    self.clock.sleep(sleep_s)
                    continue
                else:
                    self.metrics.record_source_error()
                    raise SourceError(f"Network transport error: {exc}") from exc

            if response.status_code == 200:
                try:
                    return response.json()
                except Exception as exc:
                    self.metrics.record_parse_error()
                    raise ParseError(f"Malformed JSON in HTTP response: {exc}") from exc

            # Retryable errors: 429 (rate limit) and 5xx (server error)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < self.max_retries:
                    self.metrics.retries += 1
                    sleep_s = self.initial_backoff_s * (self.backoff_multiplier**attempt)
                    attempt += 1
                    self.clock.sleep(sleep_s)
                    continue
                else:
                    self.metrics.record_source_error()
                    raise SourceError(
                        f"Open-Meteo HTTP {response.status_code} "
                        f"after {self.max_retries} retries: {response.text}"
                    )
            else:
                self.metrics.record_source_error()
                raise SourceError(f"Open-Meteo HTTP error {response.status_code}: {response.text}")
