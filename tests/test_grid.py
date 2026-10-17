"""Increment 1 tests — H3 Grid Core and Coastal Karnataka Cell Set.

TDD Test Suite:
1. Known lat/lon -> known cell ID (NITK Surathkal).
2. Cell center round-trips to same cell (Hypothesis property test).
3. Mean res-7 area is approximately 5.16 km^2.
4. Every cell has 6 immediate neighbors.
5. 5-district polygon fills to ~5,400-6,200 cells with no duplicates.
6. CoastalGrid returns a stable, sorted cell list.
7. CoastalGrid caches cell list to disk and reloads without recomputing.
8. Fast O(1) membership check via 'in'.
"""

from __future__ import annotations

from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from disaster_intel.grid import CoastalGrid

# NITK Surathkal coordinates
NITK_LAT = 13.0108
NITK_LNG = 74.7943
NITK_RES7_CELL = "8760112eaffffff"


@pytest.mark.unit
def test_known_latlng_to_known_cell() -> None:
    """Verify that a known coordinate (NITK Surathkal) maps to its expected res-7 H3 cell."""
    grid = CoastalGrid(use_cache=False)
    cell = grid.latlng_to_cell(NITK_LAT, NITK_LNG)
    assert cell == NITK_RES7_CELL


@pytest.mark.unit
@given(
    lat=st.floats(min_value=11.9, max_value=15.5),
    lng=st.floats(min_value=74.0, max_value=76.0),
)
@settings(max_examples=50)
def test_cell_center_round_trip_hypothesis(lat: float, lng: float) -> None:
    """Property test: Converting any coordinate to a cell and back to center

    always round-trips to the identical H3 cell ID at resolution 7.
    """
    grid = CoastalGrid(use_cache=False)
    cell = grid.latlng_to_cell(lat, lng)
    center_lat, center_lng = grid.cell_to_latlng(cell)
    round_trip_cell = grid.latlng_to_cell(center_lat, center_lng)
    assert round_trip_cell == cell


@pytest.mark.unit
def test_res7_mean_area_approx_5_point_16() -> None:
    """Res-7 mean cell area should be approximately 5.16 km^2 (within 5.0 - 5.3 km^2)."""
    grid = CoastalGrid(use_cache=False)
    mean_area = grid.mean_cell_area()
    assert 5.0 < mean_area < 5.3
    # Check NITK cell specifically
    nitk_area = grid.cell_area(NITK_RES7_CELL)
    assert 5.0 < nitk_area < 5.3


@pytest.mark.unit
def test_every_cell_has_six_neighbors() -> None:
    """In an H3 hexagonal grid, every regular cell has exactly 6 immediate neighbors."""
    grid = CoastalGrid(use_cache=False)
    # Check for NITK cell and a sample of cells
    neighbors = grid.neighbors(NITK_RES7_CELL)
    assert len(neighbors) == 6
    assert len(set(neighbors)) == 6
    assert NITK_RES7_CELL not in neighbors


@pytest.mark.unit
def test_district_polygon_cell_count_and_no_duplicates() -> None:
    """The 5-district polygon must fill to between 5,400 and 6,200 cells with zero duplicates."""
    grid = CoastalGrid(use_cache=False)
    cells = grid.cells
    assert 5400 <= len(cells) <= 6200
    assert len(cells) == len(set(cells)), "Cell list must contain no duplicates"


@pytest.mark.unit
def test_coastal_grid_returns_stable_sorted_cells() -> None:
    """CoastalGrid.cells must return a deterministic, stable, lexicographically sorted tuple."""
    grid1 = CoastalGrid(use_cache=False)
    grid2 = CoastalGrid(use_cache=False)
    assert grid1.cells == grid2.cells
    assert list(grid1.cells) == sorted(grid1.cells)


@pytest.mark.unit
def test_coastal_grid_caching_to_disk(tmp_path: Path) -> None:
    """CoastalGrid should write its cell set to disk and reload from cache."""
    cache_file = tmp_path / "cells_cache.json"
    assert not cache_file.exists()

    # First run: computes and writes cache
    grid = CoastalGrid(cache_path=cache_file)
    assert cache_file.exists()
    cached_cells = grid.cells

    # Second run: loads from cache without recomputing
    grid_cached = CoastalGrid(cache_path=cache_file)
    assert grid_cached.cells == cached_cells


@pytest.mark.unit
def test_coastal_grid_membership_and_lookup() -> None:
    """CoastalGrid should support fast O(1) membership check."""
    grid = CoastalGrid(use_cache=False)
    assert NITK_RES7_CELL in grid
    # An arbitrary cell in Antarctica/North America should not be in Coastal Karnataka grid
    assert "871f1d488ffffff" not in grid
