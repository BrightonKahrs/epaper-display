from __future__ import annotations

import logging
import threading
import time

from epaper_display.catalog import Catalog
from epaper_display.config import AppConfig
from epaper_display.display import Display, PreviewDisplay, show_slide
from epaper_display.refresh_worker import refresh_due_sources
from epaper_display.source_registry import ApiSourceRegistry
from epaper_display.telemetry import Telemetry


logger = logging.getLogger(__name__)


class CarouselService:
    def __init__(
        self,
        config: AppConfig,
        catalog: Catalog,
        source_registry: ApiSourceRegistry,
        display: Display,
        poll_seconds: float = 1,
        telemetry: Telemetry | None = None,
    ):
        self.config = config
        self.catalog = catalog
        self.source_registry = source_registry
        self.display = display
        self.poll_seconds = poll_seconds
        self.telemetry = telemetry
        self._stop = threading.Event()
        self._last_rendered_id: str | None = None
        self._last_rendered_revision: str | None = None
        self._failed_slide_id: str | None = None
        self._failed_slide_revision: str | None = None
        self._retry_after = 0.0
        self._heartbeat_at = 0.0

    def stop(self) -> None:
        self._stop.set()

    def tick(self, now: float | None = None) -> None:
        timestamp = now if now is not None else time.time()
        if timestamp - self._heartbeat_at >= 5:
            self.catalog.update_runtime_state({
                "service_status": "running", "service_heartbeat_at": str(timestamp),
            })
            self._heartbeat_at = timestamp
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
        started = time.monotonic()
        self.catalog.update_runtime_state({
            "service_status": "running",
            "service_heartbeat_at": str(timestamp),
            "rendering_slide_id": slide.id,
            "display_target": "preview" if isinstance(self.display, PreviewDisplay) else "panel",
        })
        try:
            show_slide(
                self.display,
                slide.path,
                self.config.display_orientation,
                self.config.portrait_rotation_degrees,
            )
        except Exception as exc:
            self.catalog.update_runtime_state({
                "service_heartbeat_at": str(timestamp if now is not None else time.time()),
                "rendering_slide_id": "",
                "display_error": f"{type(exc).__name__}: {exc}"[:500],
            })
            if self.telemetry:
                self.telemetry.display_refresh(False, time.monotonic() - started)
            self._failed_slide_id = slide.id
            self._failed_slide_revision = revision
            self._retry_after = timestamp + 30
            logger.exception("Could not display slide %s", slide.id)
            return
        if self.telemetry:
            self.telemetry.display_refresh(True, time.monotonic() - started)
        self.catalog.update_runtime_state({
            "service_heartbeat_at": str(timestamp if now is not None else time.time()),
            "displayed_slide_id": slide.id,
            "displayed_slide_title": slide.title,
            "displayed_slide_revision": revision or "",
            "displayed_at": str(timestamp if now is not None else time.time()),
            "rendering_slide_id": "",
            "display_error": "",
        })
        self._last_rendered_id = slide.id
        self._last_rendered_revision = revision
        self._failed_slide_id = None
        self._failed_slide_revision = None
        self._retry_after = 0.0
        logger.info("Displaying slide %s: %s", slide.id, slide.title)

    def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            refresh_due_sources(self.source_registry, self.catalog, telemetry=self.telemetry)
            self._stop.wait(self.config.refresh_poll_seconds)

    def run(self) -> None:
        self.catalog.initialize_carousel(time.time(), self.config.interval_seconds)
        worker = threading.Thread(
            target=self._refresh_loop,
            name="feed-refresh",
            daemon=True,
        )
        worker.start()
        logger.info("Carousel service started")
        try:
            self.catalog.update_runtime_state({
                "service_status": "running", "rendering_slide_id": "", "display_error": "",
                "service_heartbeat_at": str(time.time()),
            })
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(self.poll_seconds)
        finally:
            self._stop.set()
            worker.join(timeout=5)
            if worker.is_alive():
                logger.warning("Source refresh worker did not stop within 5 seconds")
            self.catalog.update_runtime_state({
                "service_status": "stopped", "rendering_slide_id": "",
                "service_heartbeat_at": str(time.time()),
            })
