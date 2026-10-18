"""Increment 4 tests — Spatial Discretizers (Point, Grid-Node, and Polygon).

TDD Test Suite (from plan.md lines 189-201):
1. Point maps to same cell as H3 library.
2. Every region cell maps to exactly one 0.1° weather grid node.
3. Constant raster stays constant across cells.
4. Gradient raster gives monotone cell values.
5. CAP polygon over known district yields cells whose centres are all inside it.
6. Cell-to-source-node fan-out table precomputed and cached.
7. Unified SpatialDiscretizer dispatches correctly for all geometry types.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import h3
import pytest

from disaster_intel.discretizers import (
    FanOutTable,
    GridNodeDiscretizer,
    PointDiscretizer,
    PolygonDiscretizer,
    SpatialDiscretizer,
    point_in_polygon,
)
from disaster_intel.grid import CoastalGrid
from disaster_intel.models import (
    GridNodeGeometry,
    Observation,
    PointGeometry,
    PolygonGeometry,
    RawReading,
)


@pytest.fixture(scope="module")
def coastal_grid() -> CoastalGrid:
    return CoastalGrid(use_cache=True)


# ── Point Discretizer Tests ─────────────────────────────────────────


@pytest.mark.unit
class TestPointDiscretizer:
    """Point coordinates must map to the exact H3 cell determined by H3 core."""

    def test_point_maps_to_same_cell_as_h3_library(self, coastal_grid: CoastalGrid) -> None:
        discretizer = PointDiscretizer(grid=coastal_grid)

        # Test various coordinates across Karnataka coast
        test_coords = [
            (12.9141, 74.8560),  # Mangalore center
            (13.3409, 74.7421),  # Udupi center
            (14.8135, 74.1298),  # Karwar center
            (12.4244, 75.7382),  # Madikeri / Kodagu
        ]

        for lat, lon in test_coords:
            expected_cell = h3.latlng_to_cell(lat, lon, coastal_grid.resolution)
            cell = discretizer.discretize_point(lat, lon)
            assert cell == expected_cell

    def test_point_raw_reading_produces_observation(self, coastal_grid: CoastalGrid) -> None:
        discretizer = PointDiscretizer(grid=coastal_grid)
        ts = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)

        reading = RawReading(
            source="usgs",
            variable="seismic_magnitude",
            value=4.5,
            timestamp=ts,
            geometry=PointGeometry(lat=13.0, lon=75.0),
            unit="magnitude",
        )

        obs_list = discretizer.discretize(reading)
        assert len(obs_list) == 1
        obs = obs_list[0]
        assert isinstance(obs, Observation)
        assert obs.cell == coastal_grid.latlng_to_cell(13.0, 75.0)
        assert obs.variable == "seismic_magnitude"
        assert obs.value == 4.5
        assert obs.unit == "magnitude"
        assert obs.timestamp == ts


# ── Grid-Node Discretizer & Fan-Out Table Tests ──────────────────────


@pytest.mark.unit
class TestFanOutTableAndGridNodeDiscretizer:
    """Fan-out table precomputation, 1-to-1 node mapping, and raster properties."""

    def test_every_region_cell_maps_to_exactly_one_grid_node(
        self, coastal_grid: CoastalGrid
    ) -> None:
        fan_out = FanOutTable.from_grid(grid=coastal_grid, resolution_deg=0.1)

        assert len(coastal_grid.cells) > 0
        for cell in coastal_grid.cells:
            # Each cell must have an entry
            assert cell in fan_out.cell_to_node
            node = fan_out.cell_to_node[cell]
            assert isinstance(node, tuple)
            assert len(node) == 2
            node_lat, node_lon = node

            # Node should be within 0.1 deg of cell center
            cell_lat, cell_lon = coastal_grid.cell_to_latlng(cell)
            assert abs(cell_lat - node_lat) <= 0.1
            assert abs(cell_lon - node_lon) <= 0.1

    def test_fan_out_table_partitions_all_cells(self, coastal_grid: CoastalGrid) -> None:
        fan_out = FanOutTable.from_grid(grid=coastal_grid, resolution_deg=0.1)

        # Sum of cells across all nodes must equal total cells
        all_cells_in_nodes = [c for cells in fan_out.node_to_cells.values() for c in cells]
        assert len(all_cells_in_nodes) == len(coastal_grid.cells)
        assert set(all_cells_in_nodes) == set(coastal_grid.cells)

        # Average fan-out factor should be around 15-25 cells per 0.1 deg node
        avg_cells_per_node = len(coastal_grid.cells) / len(fan_out.unique_nodes)
        assert 10.0 <= avg_cells_per_node <= 30.0

    def test_constant_raster_stays_constant(self, coastal_grid: CoastalGrid) -> None:
        fan_out = FanOutTable.from_grid(grid=coastal_grid, resolution_deg=0.1)
        discretizer = GridNodeDiscretizer(grid=coastal_grid, fan_out=fan_out)
        ts = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)

        # Feed constant value 25.0 mm for all unique nodes
        constant_val = 25.0
        all_obs: list[Observation] = []

        for lat, lon in fan_out.unique_nodes:
            reading = RawReading(
                source="open-meteo",
                variable="precipitation",
                value=constant_val,
                timestamp=ts,
                geometry=GridNodeGeometry(lat=lat, lon=lon, resolution_deg=0.1),
                unit="mm",
            )
            all_obs.extend(discretizer.discretize(reading))

        assert len(all_obs) == len(coastal_grid.cells)
        for obs in all_obs:
            assert obs.value == constant_val

    def test_gradient_raster_is_monotone_along_latitude(self, coastal_grid: CoastalGrid) -> None:
        fan_out = FanOutTable.from_grid(grid=coastal_grid, resolution_deg=0.1)
        discretizer = GridNodeDiscretizer(grid=coastal_grid, fan_out=fan_out)
        ts = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)

        # Linear North-South gradient: V = lat * 10.0
        cell_values: dict[str, float] = {}

        for lat, lon in fan_out.unique_nodes:
            reading = RawReading(
                source="open-meteo",
                variable="temperature",
                value=lat * 10.0,
                timestamp=ts,
                geometry=GridNodeGeometry(lat=lat, lon=lon, resolution_deg=0.1),
                unit="celsius",
            )
            for obs in discretizer.discretize(reading):
                cell_values[obs.cell] = obs.value

        # Select a transect of cells approximately along 74.8 longitude
        transect = [
            (c, coastal_grid.cell_to_latlng(c))
            for c in coastal_grid.cells
            if abs(coastal_grid.cell_to_latlng(c)[1] - 74.8) < 0.05
        ]
        # Sort by latitude south to north
        transect.sort(key=lambda item: item[1][0])
        assert len(transect) >= 10

        # Monotonically non-decreasing
        prev_val = -1e9
        for cell_id, (lat, _lon) in transect:
            val = cell_values[cell_id]
            assert val >= prev_val, f"Monotonicity violated at lat {lat}: {val} < {prev_val}"
            prev_val = val

    def test_fan_out_table_cached_to_disk(self, coastal_grid: CoastalGrid, tmp_path: Path) -> None:
        cache_file = tmp_path / "fan_out_cache.json"

        # 1. Compute and cache
        fan_out_1 = FanOutTable.from_grid(
            grid=coastal_grid, resolution_deg=0.1, cache_path=cache_file
        )
        assert cache_file.is_file()

        fan_out_2 = FanOutTable.from_cache(cache_path=cache_file)
        assert fan_out_2.cell_to_node == fan_out_1.cell_to_node
        assert fan_out_2.unique_nodes == fan_out_1.unique_nodes

    def test_grid_node_discretizer_handles_model_grid_coordinate_offsets(
        self, coastal_grid: CoastalGrid
    ) -> None:
        """Weather model grid outputs may have small coordinate shifts (e.g. 12.0562, 75.6065)."""
        discretizer = GridNodeDiscretizer(grid=coastal_grid)
        ts = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)

        # 12.056239, 75.606476 snaps to 12.1, 75.6
        reading = RawReading(
            source="open-meteo",
            variable="precipitation",
            value=15.0,
            timestamp=ts,
            geometry=GridNodeGeometry(lat=12.056239, lon=75.606476, resolution_deg=0.1),
            unit="mm",
        )
        obs = discretizer.discretize(reading)
        assert len(obs) > 0
        expected_cells = discretizer.fan_out.node_to_cells.get((12.1, 75.6), ())
        assert len(obs) == len(expected_cells)


# ── Polygon Discretizer Tests ───────────────────────────────────────


@pytest.mark.unit
class TestPolygonDiscretizer:
    """CAP warning polygons must yield cells whose centres are inside the polygon."""

    def test_cap_polygon_cells_centres_inside_polygon(self, coastal_grid: CoastalGrid) -> None:
        discretizer = PolygonDiscretizer(grid=coastal_grid)
        ts = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)

        # A rectangular polygon around Udupi district: lat [13.2, 13.6], lon [74.7, 75.0]
        polygon_vertices = [
            (13.2, 74.7),
            (13.6, 74.7),
            (13.6, 75.0),
            (13.2, 75.0),
        ]

        reading = RawReading(
            source="sachet",
            variable="cap_severity",
            value=4.0,  # "Severe"
            timestamp=ts,
            geometry=PolygonGeometry(vertices=polygon_vertices),
            unit="index",
        )

        obs_list = discretizer.discretize(reading)
        assert len(obs_list) > 0

        # Verify every cell center is inside (or within boundary tolerance of) the polygon
        for obs in obs_list:
            cell_lat, cell_lon = coastal_grid.cell_to_latlng(obs.cell)
            assert point_in_polygon(cell_lat, cell_lon, polygon_vertices), (
                f"Cell {obs.cell} center ({cell_lat}, {cell_lon}) is not inside polygon"
            )
            assert obs.variable == "cap_severity"
            assert obs.value == 4.0


# ── Unified Spatial Discretizer Tests ───────────────────────────────


@pytest.mark.unit
class TestUnifiedSpatialDiscretizer:
    """SpatialDiscretizer automatically dispatches to Point, GridNode, or Polygon."""

    def test_unified_discretizer_dispatches_correctly(self, coastal_grid: CoastalGrid) -> None:
        unified = SpatialDiscretizer(grid=coastal_grid)
        ts = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)

        # 1. Point
        p_read = RawReading(
            source="usgs",
            variable="seismic_magnitude",
            value=3.2,
            timestamp=ts,
            geometry=PointGeometry(lat=13.0, lon=75.0),
            unit="magnitude",
        )
        p_obs = unified.discretize(p_read)
        assert len(p_obs) == 1

        # 2. Grid Node
        node = unified.fan_out.unique_nodes[0]
        g_read = RawReading(
            source="open-meteo",
            variable="precipitation",
            value=12.0,
            timestamp=ts,
            geometry=GridNodeGeometry(lat=node[0], lon=node[1], resolution_deg=0.1),
            unit="mm",
        )
        g_obs = unified.discretize(g_read)
        assert len(g_obs) == len(unified.fan_out.node_to_cells[node])
        assert len(g_obs) > 1

        # 3. Polygon
        poly_vertices = [
            (13.2, 74.7),
            (13.5, 74.7),
            (13.5, 74.9),
            (13.2, 74.9),
        ]
        poly_read = RawReading(
            source="sachet",
            variable="cap_severity",
            value=3.0,
            timestamp=ts,
            geometry=PolygonGeometry(vertices=poly_vertices),
            unit="index",
        )
        poly_obs = unified.discretize(poly_read)
        assert len(poly_obs) > 10
