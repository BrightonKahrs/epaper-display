from __future__ import annotations

import json
import logging
import time
from urllib.error import URLError

from epaper_display.api_client import refresh_source
from epaper_display.catalog import Catalog
from epaper_display.source_registry import ApiSourceRegistry
from epaper_display.telemetry import Telemetry


logger = logging.getLogger(__name__)


def refresh_due_sources(
    source_registry: ApiSourceRegistry,
    catalog: Catalog,
    now: float | None = None,
    force: bool = False,
    telemetry: Telemetry | None = None,
) -> None:
    timestamp = now if now is not None else time.time()
    for source in source_registry.list_sources(enabled_only=True):
        if not force and not source_registry.is_due(source.id, timestamp):
            continue
        started = time.monotonic()
        try:
            count = refresh_source(source, catalog, source_registry, timestamp)
            if telemetry:
                telemetry.source_refresh(source.source_type, True, time.monotonic() - started, count)
            logger.info("Refreshed source %s: imported %d slide(s)", source.id, count)
        except (OSError, ValueError, URLError, json.JSONDecodeError) as exc:
            if telemetry:
                telemetry.source_refresh(source.source_type, False, time.monotonic() - started, 0)
            source_registry.record_failure(source.id, timestamp, str(exc))
            logger.warning("Could not refresh source %s: %s", source.id, exc)
