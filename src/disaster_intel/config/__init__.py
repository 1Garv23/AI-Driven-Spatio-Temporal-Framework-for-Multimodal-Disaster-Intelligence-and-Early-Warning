"""Configuration package for disaster-intel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent
STUDY_REGION_FILE = CONFIG_DIR / "study_region.json"


def load_region_config(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load study region configuration from JSON file."""
    path = Path(config_path) if config_path is not None else STUDY_REGION_FILE
    with open(path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data
