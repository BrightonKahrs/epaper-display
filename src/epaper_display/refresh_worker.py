from __future__ import annotations

import json
import logging
import time
from urllib.error import URLError

from epaper_display.api_client import refresh_source
from epaper_display.catalog import Catalog
from epaper_display.source_registry import ApiSourceRegistry


logger = logging.getLogger(__name__)


def refresh_due_sources(
    source_registry: ApiSourceRegistry,
    catalog: Catalog,
    now: float | None = None,
    force: bool = False,
) -> None:
    timestamp = now if now is not None else time.time()
    for source in source_registry.list_sources(enabled_only=True):
        if not force and not source_registry.is_due(source.id, timestamp):
            continue
        try:
            count = refresh_source(source, catalog, source_registry, timestamp)
            logger.info("Refreshed source %s: imported %d slide(s)", source.id, count)
        except (OSError, ValueError, URLError, json.JSONDecodeError) as exc:
            source_registry.record_failure(source.id, timestamp, str(exc))
            logger.warning("Could not refresh source %s: %s", source.id, exc)
