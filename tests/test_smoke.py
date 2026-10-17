"""Increment 0 smoke tests.

Validates the repository foundation:
1. Package imports cleanly with a valid version string.
2. Unit tests strictly block network access via pytest-socket.
"""

from __future__ import annotations

import socket

import pytest
import pytest_socket

import disaster_intel


@pytest.mark.unit
def test_package_imports_and_has_version() -> None:
    """Verify disaster_intel is installed and exports a valid semantic version."""
    assert disaster_intel.__version__ == "0.1.0"


@pytest.mark.unit
def test_unit_tests_block_network_sockets() -> None:
    """Verify that network calls are blocked in unit tests (plan.md TDD Rule 3).

    Attempting to open a socket connection inside a @pytest.mark.unit test
    must raise SocketBlockedError or SocketConnectBlockedError.
    """
    with pytest.raises(pytest_socket.SocketBlockedError):
        # Even creating a socket should be blocked or connecting blocked
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", 80))
