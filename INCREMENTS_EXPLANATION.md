# AI-Driven Spatio-Temporal Framework for Multimodal Disaster Intelligence
## Increments & Architecture Explanation Guide

This document provides a comprehensive, function-by-function and module-by-module explanation of everything built in the project so far. It explains **what** each component does, **why** it was designed that way, and **how** it connects to the broader disaster intelligence pipeline.

---

## Table of Contents
1. [Core Design Philosophy & TDD Discipline](#1-core-design-philosophy--tdd-discipline)
2. [Increment 0: Project Scaffold & Infrastructure Config](#2-increment-0-project-scaffold--infrastructure-config)
3. [Increment 1: H3 Spatial Grid (`CoastalGrid`)](#3-increment-1-h3-spatial-grid-coastalgrid)
4. [Increment 2: Multimodal Telemetry Data Model (`models.py`)](#4-increment-2-multimodal-telemetry-data-model-modelspy)
5. [Increment 3: Telemetry Adapters & Deterministic Clock (`clock.py`, `adapters/`)](#5-increment-3-telemetry-adapters--deterministic-clock-clockpy-adapters)
6. [Increment 4: Spatial Discretizers & Fan-Out Table (`discretizers.py`)](#6-increment-4-spatial-discretizers--fan-out-table-discretizerspy)
7. [Current System Status & Test Suite Summary](#7-current-system-status--test-suite-summary)
8. [Upcoming Increments Roadmap (Increments 5 to 14)](#8-upcoming-increments-roadmap-increments-5-to-14)

---

## 1. Core Design Philosophy & TDD Discipline

Every single feature in this project adheres to strict **Test-Driven Development (TDD)** and clean hexagonal architecture:

1. **Tests Are Written First (RED)**: We write unit tests specifying edge cases and constraints before creating the implementation. Code is only written to make tests pass (GREEN), then refactored.
2. **Network Isolation**: Unit tests never touch the network (`pytest-socket` blocks all socket creation during `@pytest.mark.unit`). External API calls run only through recorded JSON fixtures or injected mock transports.
3. **Deterministic Clock**: Unit tests never sleep or rely on system wall clocks; time is provided by an injected `Clock` protocol (`FakeClock`), allowing instantaneous testing of retry backoffs.
4. **Idempotency & Deduplication**: Every telemetry reading generates a deterministic hash-based key, ensuring retries and duplicate transmissions never corrupt historical aggregates.

---

## 2. Increment 0: Project Scaffold & Infrastructure Config

### Purpose
Establish a production-grade Python package foundation with strict quality gates, dependency pinning, and container orchestration for hot/cold storage and message streaming.

### Key Components
* **[`pyproject.toml`](file:///c:/Users/bajaj/OneDrive/Desktop/Documents/comp%20science/major%20project/AI-Driven-Spatio-Temporal-Framework-for-Multimodal-Disaster-Intelligence-and-Early-Warning/pyproject.toml)**: Configures `hatchling` build system, Python >= 3.11, strict ruff linter (`select = ["E", "F", "W", "I", "UP", "B", "SIM", "RUF"]`), strict mypy type checking, and pytest marker definitions (`unit`, `contract`, `integration`, `perf`, `live`, `slow`).
* **[`docker-compose.yml`](file:///c:/Users/bajaj/OneDrive/Desktop/Documents/comp%20science/major%20project/AI-Driven-Spatio-Temporal-Framework-for-Multimodal-Disaster-Intelligence-and-Early-Warning/docker-compose.yml)**: Defines three core services:
  * **ClickHouse** (`clickhouse/clickhouse-server:24.3`): Columnar analytical database with `ReplacingMergeTree` for permanent, compressed observation storage.
  * **Redis** (`redis:7-alpine`): In-memory store holding a 7-day rolling window per cell for sub-millisecond AI model inferences.
  * **Redpanda** (`vectorized/redpanda:v23.3`): High-throughput, Kafka-compatible message bus for event ingestion.
* **[`study_region.json`](file:///c:/Users/bajaj/OneDrive/Desktop/Documents/comp%20science/major%20project/AI-Driven-Spatio-Temporal-Framework-for-Multimodal-Disaster-Intelligence-and-Early-Warning/src/disaster_intel/config/study_region.json)**: Exact latitude/longitude polygon boundary enclosing Coastal Karnataka (Uttara Kannada, Udupi, Dakshina Kannada) and Western Ghats catchments (Shivamogga, Kodagu).

---

## 3. Increment 1: H3 Spatial Grid (`CoastalGrid`)

### Purpose
Solves **Gap G1 (Spatial Resolution Mismatch)**. Satellite and weather models use rectangular lat/lon grids with distortion towards the poles, while ground stations are scattered points. Uber's **H3 hexagonal hierarchical spatial index** provides equal-area, distortion-minimized cells where every cell has exactly 6 equidistant neighbors.

### Key Class: `CoastalGrid` ([`grid.py`](file:///c:/Users/bajaj/OneDrive/Desktop/Documents/comp%20science/major%20project/AI-Driven-Spatio-Temporal-Framework-for-Multimodal-Disaster-Intelligence-and-Early-Warning/src/disaster_intel/grid.py))
* **Resolution 7 Standard**: Each cell has an area of **~5.16 km²** (diameter ~2.6 km). This resolution matches the physical scale of localized flash floods and landslide hillslopes while remaining computationally tractable.
* **`self.cells`**: Precomputes and sorts a deterministic tuple of **5,930 H3 cells** completely covering the study region.
* **`latlng_to_cell(lat, lng)`**: Maps any GPS point to its enclosing H3 cell in $O(1)$ time.
* **`cell_to_latlng(cell)`**: Returns the geographic centroid `(lat, lon)` of an H3 hexagon.
* **`neighbors(cell)`**: Returns the 6 adjacent cells (1-ring neighbor set) used by spatial convolution and graph neural networks.
* **Disk Caching**: Cell computation is serialized to `.cache/coastal_grid_res7.json` on disk, eliminating re-computation overhead on future startups.

---

## 4. Increment 2: Multimodal Telemetry Data Model (`models.py`)

### Purpose
Provides strong typing, unit normalization, coordinate validation, and deduplication keys before any data enters storage or pipelines.

### Key Classes ([`models.py`](file:///c:/Users/bajaj/OneDrive/Desktop/Documents/comp%20science/major%20project/AI-Driven-Spatio-Temporal-Framework-for-Multimodal-Disaster-Intelligence-and-Early-Warning/src/disaster_intel/models.py))

#### 1. Geometry Types
* **`PointGeometry(lat, lon)`**: Represents a localized observation (e.g. USGS earthquake epicenter, rain gauge). Rejects out-of-range coordinates (`[-90, 90]`, `[-180, 180]`).
* **`GridNodeGeometry(lat, lon, resolution_deg)`**: Represents regular raster weather nodes (e.g. Open-Meteo 0.1° grid).
* **`PolygonGeometry(vertices)`**: Represents regional warning areas (e.g. NDMA SACHET alert boundaries). Enforces $\ge 3$ vertices.

#### 2. Telemetry Records
* **`RawReading`**:
  * An unprocessed record straight from an external source adapter (before spatial discretization).
  * Enforces **strict UTC normalization** (rejects naive datetimes; converts non-UTC timezones to UTC).
  * Rejects `NaN`, `+Inf`, and `-Inf` values at instantiation.
* **`Observation`**:
  * The canonical, per-cell, post-discretization record.
  * Carries an **H3 cell ID**, canonical physical unit, UTC timestamp, and source attribution.
  * **Deterministic Idempotency Key**: Generated via SHA-256 hash of `"{cell}|{variable}|{source}|{timestamp_iso}"`. If the same observation is sent twice (e.g. network retry after crash), the cold store recognizes the identical key and deduplicates it.

#### 3. `VariableCatalog` & Unit Conversion
* **`VariableCatalog`**: Registry defining every supported telemetry stream with its canonical storage unit and aggregation method:
  * `precipitation`: `mm`, Aggregation = `SUM`
  * `soil_moisture_surface` (0-7 cm): `m3/m3`, Aggregation = `MEAN`
  * `soil_moisture_rootzone` (7-28 cm): `m3/m3`, Aggregation = `MEAN`
  * `temperature`: `celsius`, Aggregation = `MEAN`
  * `wind_speed`: `km/h`, Aggregation = `MAX`
  * `pressure`: `hPa`, Aggregation = `MEAN`
  * `discharge`: `m3/s`, Aggregation = `MAX`
  * `seismic_magnitude`: `magnitude`, Aggregation = `MAX`
  * `cap_severity`: `index`, Aggregation = `MAX`
* **`convert_unit(value, from_unit, variable, interval_hours)`**:
  * Automatically converts alternative incoming units into canonical units:
    * Kelvin $\rightarrow$ Celsius ($K - 273.15$)
    * Fahrenheit $\rightarrow$ Celsius ($(F - 32) \times \frac{5}{9}$)
    * Precipitation rate $\text{mm/hr} \rightarrow \text{mm}$ (scaled by interval)
    * Soil moisture percentage $\% \rightarrow \text{m}^3/\text{m}^3$ (divided by 100)

---

## 5. Increment 3: Telemetry Adapters & Deterministic Clock (`clock.py`, `adapters/`)

### Purpose
Isolate all external API interactions behind a clean interface, implement rate-limiting protection, request batching, failure attribution, and ensure unit tests run without real time delays or network requests.

### Key Components

#### 1. Injected Clock Abstraction ([`clock.py`](file:///c:/Users/bajaj/OneDrive/Desktop/Documents/comp%20science/major%20project/AI-Driven-Spatio-Temporal-Framework-for-Multimodal-Disaster-Intelligence-and-Early-Warning/src/disaster_intel/clock.py))
* **`Clock` (Protocol)**: Defines `now() -> datetime` and `sleep(seconds: float) -> None`.
* **`SystemClock`**: Production clock utilizing real wall time (`datetime.now(UTC)` and `time.sleep`).
* **`FakeClock`**: Deterministic test clock. Calling `fake_clock.sleep(10.0)` does not pause execution for 10 seconds; instead, it advances internal simulated time instantly and records the sleep duration in `sleep_log`. This allows testing 10-second exponential backoffs in microseconds!

#### 2. Base Adapter & Metrics ([`adapters/base.py`](file:///c:/Users/bajaj/OneDrive/Desktop/Documents/comp%20science/major%20project/AI-Driven-Spatio-Temporal-Framework-for-Multimodal-Disaster-Intelligence-and-Early-Warning/src/disaster_intel/adapters/base.py))
* **`AdapterMetrics`**: Tracks performance and attributes errors:
  * `requests_made`, `readings_produced`, `retries`.
  * `records_skipped`: Granular map of reasons (e.g. `{"null_hour": 6}`).
  * **Failure Attribution**: Distinguishes upstream network/source outages (`SourceError`, e.g. 503 or 429) from data corruption (`ParseError`, e.g. malformed JSON).
* **`HttpTransport` & `MockHttpTransport`**: Transport protocol allowing tests to inject mock HTTP responses without network sockets.

#### 3. Open-Meteo Weather Adapter ([`adapters/open_meteo.py`](file:///c:/Users/bajaj/OneDrive/Desktop/Documents/comp%20science/major%20project/AI-Driven-Spatio-Temporal-Framework-for-Multimodal-Disaster-Intelligence-and-Early-Warning/src/disaster_intel/adapters/open_meteo.py))
* **Multi-Location Request Batching**: Free weather APIs have rate limits and call budgets. The adapter batches multiple coordinates into single requests (e.g. 50 locations per call).
* **Exponential Backoff**: If an HTTP `429` (Too Many Requests) or `5xx` (Server Error) occurs, the adapter retries with exponential backoff ($t_{\text{sleep}} = t_{\text{initial}} \times 2^{\text{attempt}}$) via the injected clock.
* **Null-Hour Skipping**: If a specific timestamp has missing data in the weather model (`null`), it is skipped safely and incremented in `metrics.records_skipped["null_hour"]` rather than crashing the pipeline.
* **Live Test Verified**: Tested and verified live against Open-Meteo's API for Mangalore (retrieving 144 hourly readings).

---

## 6. Increment 4: Spatial Discretizers & Fan-Out Table (`discretizers.py`)

### Purpose
Solve the mapping between incoming continuous spatial data and discrete H3 resolution-7 cells. Specifically optimizes raster weather ingestion so we **do not** call the weather API 5,930 times for 5,930 cells.

### Key Classes ([`discretizers.py`](file:///c:/Users/bajaj/OneDrive/Desktop/Documents/comp%20science/major%20project/AI-Driven-Spatio-Temporal-Framework-for-Multimodal-Disaster-Intelligence-and-Early-Warning/src/disaster_intel/discretizers.py))

#### 1. `FanOutTable` (95% Call Reduction)
* **The Problem**: Open-Meteo data is produced on a 0.1° grid (~11 km spacing). Our study region contains 5,930 H3 res-7 cells (~2.6 km diameter). Querying Open-Meteo once for every individual cell would waste thousands of redundant API requests.
* **The Solution**: 
  * Every H3 cell's centroid is deterministically rounded to its nearest 0.1° grid node.
  * The **5,930 cells** collapse into exactly **294 unique grid nodes** (fan-out factor of ~20.2 cells per node).
  * We fetch each grid node **once**, and the fan-out table replicates the value out to all ~20 overlapping cells.
  * The table is precomputed and saved to `.cache/fan_out_res7_0.1deg.json`.

#### 2. `PointDiscretizer`
* Maps single point readings (e.g. earthquake magnitudes, river discharge gauges) to the enclosing cell via `grid.latlng_to_cell(lat, lon)` and creates an `Observation`.

#### 3. `PolygonDiscretizer` & `point_in_polygon`
* Implements the ray-casting algorithm to test polygon containment.
* Uses `h3.polygon_to_cells` to identify all cells covered by regional warning polygons (NDMA SACHET CAP flood/cyclone alerts), creating an `Observation` for every enclosed cell.

#### 4. `SpatialDiscretizer`
* Unified router: inspects any incoming `RawReading.geometry.kind`:
  * `"point"` $\rightarrow$ dispatches to `PointDiscretizer`
  * `"grid_node"` $\rightarrow$ dispatches to `GridNodeDiscretizer` (fanning out via `FanOutTable`)
  * `"polygon"` $\rightarrow$ dispatches to `PolygonDiscretizer`

---

## 7. Current System Status & Test Suite Summary

All quality gates pass with zero warnings or errors:

* **Unit Test Suite**: **64 passed** in **1.68 seconds** (`pytest -m unit`).
* **Linter**: `ruff check src tests` (0 errors, clean).
* **Formatter**: `ruff format --check src tests` (100% compliant).
* **Type Checker**: `mypy src tests` (Strict typing verified across 16 source files).
* **Live Integration**: `@pytest.mark.live` verified against Open-Meteo live API.

---

## 8. Upcoming Increments Roadmap (Increments 5 to 14)

### Increment 5: Bus Interface & InMemoryBus
* Build the `Bus` protocol and in-memory streaming queue with consumer-group offset tracking, replay, backpressure, and one topic per source.
* Write a reusable **contract test suite** that tests `InMemoryBus` now and tests `RedpandaBus` later with the exact same tests.

### Increment 6: Ingestion Pipeline CLI & Objective 1 Metrics
* Connect Adapters $\rightarrow$ Spatial Discretizers $\rightarrow$ Bus $\rightarrow$ In-memory Sink.
* Provide the `ingest` CLI command.
* Compute Objective 1 metrics: p50/p95/p99 ingestion latency, completion rate, source vs parse failure attribution.

### Increment 7: ClickHouse Cold Store
* Observation tables with `ReplacingMergeTree` engines, Delta + ZSTD compression, deduplication on Part merges, and cold-correctness checks.

### Increment 8: Hourly Buckets & Rolling Windows
* Incremental materialized views maintaining hourly aggregates (`SUM` for rain, `AVG` for soil moisture, `MAX` for wind/discharge).
* Rolling windows: 1h, 6h, 24h, 72h, 7d.

### Increment 9: Redis Hot Tier
* 7-day circular buffer ring in Redis for sub-millisecond cell feature vector retrieval.

### Increments 10–14: Multimodal Fusion, Foundation Model & Warning Engine
* Cross-attention spatio-temporal fusion, NASA Prithvi geospatial backbone, and early warning lead-time benchmarking.
