"""Increment 0 infrastructure integration tests.

Verifies that the containerized backing services (ClickHouse, Redis, Redpanda)
defined in docker-compose.yml are running and answer health check pings.
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request

import pytest

CLICKHOUSE_PING_URL = "http://localhost:8123/ping"
REDPANDA_HEALTH_URL = "http://localhost:9644/v1/status/ready"
REDIS_HOST = "localhost"
REDIS_PORT = 6379


def _is_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


@pytest.mark.integration
def test_clickhouse_health_ping() -> None:
    """ClickHouse HTTP interface should respond to /ping with 200 and 'Ok.'."""
    if not _is_port_open("localhost", 8123):
        pytest.fail("ClickHouse port 8123 is not reachable. Run 'docker compose up -d' first.")

    req = urllib.request.Request(CLICKHOUSE_PING_URL)
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        body = resp.read().decode("utf-8").strip()
        assert body == "Ok."


@pytest.mark.integration
def test_redis_health_ping() -> None:
    """Redis should answer a raw RESP PING command with +PONG."""
    if not _is_port_open(REDIS_HOST, REDIS_PORT):
        pytest.fail(f"Redis port {REDIS_PORT} is not reachable. Run 'docker compose up -d' first.")

    with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=3.0) as s:
        s.sendall(b"PING\r\n")
        response = s.recv(1024).decode("utf-8")
        assert response.strip() == "+PONG"


@pytest.mark.integration
def test_redpanda_health_ping() -> None:
    """Redpanda Admin API should report cluster readiness on port 9644."""
    if not _is_port_open("localhost", 9644):
        pytest.fail("Redpanda port 9644 is not reachable. Run 'docker compose up -d' first.")

    req = urllib.request.Request(REDPANDA_HEALTH_URL)
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
