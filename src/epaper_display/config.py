from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    interval_seconds: int = 1800
    refresh_poll_seconds: int = 30
    display_orientation: str = "portrait"
    portrait_rotation_degrees: int = 90

    @property
    def api_specs_database_path(self) -> Path:
        return self.data_dir / "api-specs.sqlite3"

    @property
    def image_metadata_database_path(self) -> Path:
        return self.data_dir / "catalog.sqlite3"

    @property
    def gallery_dir(self) -> Path:
        return self.data_dir / "gallery"

    @classmethod
    def load(cls, config_path: Path | None = None) -> AppConfig:
        default_config = Path("/etc/epaper-display/config.json")
        selected_config = config_path or Path(
            os.environ.get("EPAPER_CONFIG", default_config)
        )
        raw: dict[str, Any] = {}
        if selected_config.exists():
            raw = json.loads(selected_config.read_text(encoding="utf-8"))
        elif config_path is not None or os.environ.get("EPAPER_CONFIG"):
            raise FileNotFoundError(f"Configuration file not found: {selected_config}")
        if not isinstance(raw, dict):
            raise ValueError("Configuration file must contain a JSON object.")
        if "feeds" in raw:
            raise ValueError(
                "Feed specifications are now stored in the API source registry; "
                "configure them with 'epaper-display sources configure'."
            )

        configured_data_dir = os.environ.get("EPAPER_DATA_DIR")
        if configured_data_dir is None and "data_dir" in raw:
            configured_data_dir = raw["data_dir"]
        if configured_data_dir is None:
            configured_data_dir = str(
                Path.home() / ".local" / "share" / "epaper-display"
            )
        if not isinstance(configured_data_dir, str) or not configured_data_dir:
            raise ValueError("data_dir must be a non-empty string path.")
        data_dir = Path(configured_data_dir).expanduser()
        interval = _positive_integer(raw.get("interval_seconds", 1800), "interval_seconds")
        poll = _positive_integer(raw.get("refresh_poll_seconds", 30), "refresh_poll_seconds")
        orientation = raw.get("display_orientation", "portrait")
        if orientation not in ("portrait", "landscape"):
            raise ValueError("display_orientation must be 'portrait' or 'landscape'.")
        rotation = raw.get("portrait_rotation_degrees", 90)
        if rotation not in (90, 270):
            raise ValueError("portrait_rotation_degrees must be 90 or 270.")
        return cls(data_dir, interval, poll, orientation, rotation)
