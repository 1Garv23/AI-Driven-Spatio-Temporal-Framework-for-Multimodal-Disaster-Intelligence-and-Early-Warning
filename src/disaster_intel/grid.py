"""H3 Grid Core and Coastal Karnataka + Western Ghats cell set.

Increment 1: Discretizes the study region into H3 resolution-7 hexagons,
enforces stable sorting, caching, and spatial query primitives.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import h3

from disaster_intel.config import load_region_config

if TYPE_CHECKING:
    from collections.abc import Iterator


DEFAULT_CACHE_DIR = Path(".cache")


class CoastalGrid:
    """Manages the H3 grid discretization for the coastal Karnataka & Western Ghats study region.

    Attributes:
        resolution: H3 resolution level (default: 7, ~5.16 km^2 per hexagon).
        boundary_polygon: The outer polygon coordinates [lat, lon] defining the region.
        cells: Deterministically sorted tuple of H3 cell IDs within the region.
    """

    def __init__(
        self,
        resolution: int = 7,
        boundary_polygon: list[tuple[float, float]] | None = None,
        cache_path: Path | str | None = None,
        use_cache: bool = True,
        force_recompute: bool = False,
    ) -> None:
        self.resolution = resolution
        self.use_cache = use_cache

        if boundary_polygon is not None:
            self.boundary_polygon = boundary_polygon
        else:
            config = load_region_config()
            self.boundary_polygon = [
                (float(lat), float(lon)) for lat, lon in config["boundary_polygon"]
            ]

        self._cache_path: Path | None = (
            Path(cache_path)
            if cache_path is not None
            else (DEFAULT_CACHE_DIR / f"coastal_grid_res{self.resolution}.json")
            if use_cache
            else None
        )

        self._cells: tuple[str, ...] = self._load_or_compute_cells(force_recompute)
        self._cell_set: set[str] = set(self._cells)

    def _load_or_compute_cells(self, force_recompute: bool) -> tuple[str, ...]:
        if (
            self.use_cache
            and not force_recompute
            and self._cache_path is not None
            and self._cache_path.is_file()
        ):
            try:
                with open(self._cache_path, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return tuple(data)
            except (json.JSONDecodeError, OSError):
                # Fall back to recompute on corrupt cache
                pass

        # Compute via H3 polygon fill
        poly = h3.LatLngPoly(self.boundary_polygon)
        cells_set = h3.polygon_to_cells(poly, self.resolution)
        sorted_cells = tuple(sorted(cells_set))

        # Save to disk cache if caching enabled
        if self.use_cache and self._cache_path is not None:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(list(sorted_cells), f)

        return sorted_cells

    @property
    def cells(self) -> tuple[str, ...]:
        """Return the stable, sorted tuple of H3 cell IDs."""
        return self._cells

    def __len__(self) -> int:
        return len(self._cells)

    def __contains__(self, cell: str) -> bool:
        """Fast O(1) membership check."""
        return cell in self._cell_set

    def __iter__(self) -> Iterator[str]:
        return iter(self._cells)

    def latlng_to_cell(self, lat: float, lng: float) -> str:
        """Index a coordinate point to its H3 cell ID at grid resolution."""
        return str(h3.latlng_to_cell(lat, lng, self.resolution))

    def cell_to_latlng(self, cell: str) -> tuple[float, float]:
        """Return the (latitude, longitude) center coordinate of an H3 cell."""
        res: tuple[float, float] = h3.cell_to_latlng(cell)
        return res

    def neighbors(self, cell: str) -> tuple[str, ...]:
        """Return the immediate 1-ring neighbor cells (6 for regular hexagons)."""
        ring = h3.grid_ring(cell, 1)
        return tuple(sorted(ring))

    def cell_area(self, cell: str, unit: str = "km^2") -> float:
        """Compute the exact spherical area of an H3 cell."""
        area: float = h3.cell_area(cell, unit)
        return area

    def mean_cell_area(self, unit: str = "km^2") -> float:
        """Return the theoretical average hexagon area for the grid resolution."""
        avg: float = h3.average_hexagon_area(self.resolution, unit)
        return avg
