"""Base abstractions and metrics for telemetry source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from disaster_intel.models import RawReading


class AdapterError(Exception):
    """Base error raised by telemetry adapters."""


class SourceError(AdapterError):
    """Error originating from upstream source (HTTP 4xx/5xx, timeouts, connection drop)."""


class ParseError(AdapterError):
    """Error parsing upstream payload into valid RawReading domain models."""


@dataclass
class AdapterMetrics:
    """Metrics tracking adapter execution and failure attribution."""

    requests_made: int = 0
    readings_produced: int = 0
    records_skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    retries: int = 0
    source_errors: int = 0
    parse_errors: int = 0

    @property
    def has_errors(self) -> bool:
        return self.source_errors > 0 or self.parse_errors > 0

    def record_source_error(self) -> None:
        self.source_errors += 1

    def record_parse_error(self) -> None:
        self.parse_errors += 1

    def record_skip(self, reason: str, count: int = 1) -> None:
        self.records_skipped[reason] = self.records_skipped.get(reason, 0) + count


@dataclass
class HttpResponse:
    """Minimal HTTP response abstraction."""

    status_code: int
    text: str = ""
    json_data: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        if self.json_data is not None:
            return self.json_data
        import json

        return json.loads(self.text)


@runtime_checkable
class HttpTransport(Protocol):
    """Transport protocol for performing HTTP requests."""

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse: ...


class HttpxTransport:
    """Production transport using httpx."""

    def __init__(self) -> None:
        import httpx

        self._client = httpx.Client()

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        response = self._client.get(url, params=params, headers=headers, timeout=timeout)
        try:
            json_payload = response.json()
        except Exception:
            json_payload = None

        return HttpResponse(
            status_code=response.status_code,
            text=response.text,
            json_data=json_payload,
            headers=dict(response.headers),
        )


class MockHttpTransport:
    """In-memory test transport responding according to a custom handler."""

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], dict[str, Any] | HttpResponse],
    ) -> None:
        self._handler = handler

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        request_dict = {
            "url": url,
            "params": params or {},
            "headers": headers or {},
            "timeout": timeout,
        }
        res = self._handler(request_dict)
        if isinstance(res, HttpResponse):
            return res
        return HttpResponse(
            status_code=res.get("status_code", 200),
            text=res.get("text", ""),
            json_data=res.get("json"),
            headers=res.get("headers", {}),
        )


class SourceAdapter(ABC):
    """Abstract interface for all data source adapters."""

    source_name: str
    metrics: AdapterMetrics

    @abstractmethod
    def parse_payload(self, payload: Any) -> list[RawReading]:
        """Parse raw response payload into domain RawReading objects."""
        ...
