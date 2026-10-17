"""Root test configuration.

Enforces the Phase 1 TDD discipline:
  - Unit tests cannot access the network (pytest-socket auto-enabled).
  - All markers are registered and strict.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _block_network_in_unit_tests(request: pytest.FixtureRequest) -> Iterator[None]:
    """Automatically disable sockets for every test marked ``@pytest.mark.unit``.

    This enforces TDD Rule 3 from plan.md:
    "Unit tests never touch the network or the clock."
    """
    if request.node.get_closest_marker("unit") is not None:
        import pytest_socket

        pytest_socket.disable_socket()
        yield
        pytest_socket.enable_socket()
    else:
        yield
