"""AI-Driven Spatio-Temporal Framework for Multimodal Disaster Intelligence."""

__version__ = "0.1.0"

from disaster_intel.adapters import (
    AdapterError,
    AdapterMetrics,
    OpenMeteoAdapter,
    ParseError,
    SourceAdapter,
    SourceError,
)
from disaster_intel.clock import Clock, FakeClock, SystemClock
from disaster_intel.grid import CoastalGrid
from disaster_intel.models import (
    AggMethod,
    CAPSeverity,
    Geometry,
    GridNodeGeometry,
    Observation,
    PointGeometry,
    PolygonGeometry,
    RawReading,
    Variable,
    VariableCatalog,
)

__all__ = [
    "AdapterError",
    "AdapterMetrics",
    "AggMethod",
    "CAPSeverity",
    "Clock",
    "CoastalGrid",
    "FakeClock",
    "Geometry",
    "GridNodeGeometry",
    "Observation",
    "OpenMeteoAdapter",
    "ParseError",
    "PointGeometry",
    "PolygonGeometry",
    "RawReading",
    "SourceAdapter",
    "SourceError",
    "SystemClock",
    "Variable",
    "VariableCatalog",
    "__version__",
]
