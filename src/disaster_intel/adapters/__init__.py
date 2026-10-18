"""Telemetry source adapters package."""

from disaster_intel.adapters.base import (
    AdapterError,
    AdapterMetrics,
    HttpResponse,
    HttpTransport,
    HttpxTransport,
    MockHttpTransport,
    ParseError,
    SourceAdapter,
    SourceError,
)
from disaster_intel.adapters.open_meteo import OpenMeteoAdapter

__all__ = [
    "AdapterError",
    "AdapterMetrics",
    "HttpResponse",
    "HttpTransport",
    "HttpxTransport",
    "MockHttpTransport",
    "OpenMeteoAdapter",
    "ParseError",
    "SourceAdapter",
    "SourceError",
]
