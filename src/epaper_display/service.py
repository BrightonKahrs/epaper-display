from __future__ import annotations

import logging
import threading
import time

from epaper_display.catalog import Catalog
from epaper_display.config import AppConfig
from epaper_display.display import Display, show_slide
from epaper_display.refresh_worker import refresh_due_sources
from epaper_display.source_registry import ApiSourceRegistry


logger = logging.getLogger(__name__)


class CarouselService:
    def __init__(
        self,
        config: AppConfig,
        catalog: Catalog,
        source_registry: ApiSourceRegistry,
        display: Display,
        poll_seconds: float = 1,
    ):
        self.config = config
        self.catalog = catalog
        self.source_registry = source_registry
        self.display = display
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._last_rendered_id: str | None = None
        self._last_rendered_revision: str | None = None
        self._failed_slide_id: str | None = None
        self._failed_slide_revision: str | None = None
        self._retry_after = 0.0

    def stop(self) -> None:
        self._stop.set()

    def tick(self, now: float | None = None) -> None:
        timestamp = now if now is not None else time.time()
        self.catalog.advance_if_due(timestamp, self.config.interval_seconds)
        current_id = self.catalog.get_state("current_slide_id")
        if not current_id:
            slide = self.catalog.initialize_carousel(
                timestamp, self.config.interval_seconds
            )
        else:
            slide = self.catalog.get_slide(current_id, timestamp)
        if slide is None:
            slide = self.catalog.initialize_carousel(
                timestamp, self.config.interval_seconds
            )
        if slide is None:
            self._last_rendered_id = None
            self._last_rendered_revision = None
            return
        revision = self.catalog.get_state(f"slide_revision:{slide.id}")
        if slide.id == self._last_rendered_id and revision == self._last_rendered_revision:
            return
        if (
            slide.id == self._failed_slide_id
            and revision == self._failed_slide_revision
            and timestamp < self._retry_after
        ):
            return
        try:
            show_slide(
                self.display,
                slide.path,
                self.config.display_orientation,
                self.config.portrait_rotation_degrees,
            )
        except Exception:
            self._failed_slide_id = slide.id
            self._failed_slide_revision = revision
            self._retry_after = timestamp + 30
            logger.exception("Could not display slide %s", slide.id)
            return
        self._last_rendered_id = slide.id
        self._last_rendered_revision = revision
        self._failed_slide_id = None
        self._failed_slide_revision = None
        self._retry_after = 0.0
        logger.info("Displaying slide %s: %s", slide.id, slide.title)

    def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            refresh_due_sources(self.source_registry, self.catalog)
            self._stop.wait(self.config.refresh_poll_seconds)

    def run(self) -> None:
        self.catalog.initialize_carousel(time.time(), self.config.interval_seconds)
        threading.Thread(
            target=self._refresh_loop,
            name="feed-refresh",
            daemon=True,
        ).start()
        logger.info("Carousel service started")
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.poll_seconds)
