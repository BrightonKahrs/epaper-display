from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from epaper_display import __version__

if TYPE_CHECKING:
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.metrics import MeterProvider


class Telemetry:
    """Records bounded, non-sensitive events for the running carousel."""

    def __init__(self, meter_provider: MeterProvider, logger_provider: LoggerProvider) -> None:
        from opentelemetry._logs import SeverityNumber

        self._meter_provider = meter_provider
        self._logger_provider = logger_provider
        meter = meter_provider.get_meter("epaper_display", __version__)
        self._renders = meter.create_counter(
            "epaper.display.refreshes", unit="{refresh}", description="Display refresh attempts"
        )
        self._render_duration = meter.create_histogram(
            "epaper.display.refresh.duration", unit="s", description="Display refresh duration"
        )
        self._source_refreshes = meter.create_counter(
            "epaper.source.refreshes", unit="{refresh}", description="Source refresh attempts"
        )
        self._source_duration = meter.create_histogram(
            "epaper.source.refresh.duration", unit="s", description="Source refresh duration"
        )
        self._source_slides = meter.create_counter(
            "epaper.source.slides", unit="{slide}", description="Slides imported from sources"
        )
        self._logger = logger_provider.get_logger("epaper_display", __version__)
        self._info = SeverityNumber.INFO
        self._error = SeverityNumber.ERROR
        self._logger.emit(body="Carousel telemetry initialized", severity_number=self._info)

    def display_refresh(self, success: bool, duration: float) -> None:
        attributes = {"outcome": "success" if success else "error"}
        self._renders.add(1, attributes)
        self._render_duration.record(duration, attributes)
        self._logger.emit(
            body="Display refresh completed" if success else "Display refresh failed",
            severity_number=self._info if success else self._error,
            attributes={"outcome": attributes["outcome"], "duration_seconds": duration},
        )

    def source_refresh(self, source_type: str, success: bool, duration: float, count: int) -> None:
        attributes = {"source.type": source_type, "outcome": "success" if success else "error"}
        self._source_refreshes.add(1, attributes)
        self._source_duration.record(duration, attributes)
        if count:
            self._source_slides.add(count, {"source.type": source_type})
        self._logger.emit(
            body="Source refresh completed" if success else "Source refresh failed",
            severity_number=self._info if success else self._error,
            attributes={
                "source_type": source_type,
                "outcome": attributes["outcome"],
                "duration_seconds": duration,
                "slides_imported": count,
            },
        )

    def shutdown(self) -> None:
        try:
            self._meter_provider.shutdown()
        finally:
            self._logger_provider.shutdown()


def create_telemetry() -> Telemetry | None:
    exporter = os.environ.get("EPD_OTEL_EXPORTER", "none").strip().lower()
    if exporter == "none":
        return None
    if exporter not in ("console", "otlp"):
        raise ValueError("EPD_OTEL_EXPORTER must be 'none', 'console', or 'otlp'.")
    if exporter == "otlp":
        common = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        logs = os.environ.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "").strip() or common
        metrics = os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "").strip() or common
        if not logs or not metrics:
            raise ValueError(
                "OTLP requires OTEL_EXPORTER_OTLP_ENDPOINT or both signal-specific endpoints."
            )
        for endpoint in (logs, metrics):
            parsed = urlparse(endpoint)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("OTLP endpoints must be absolute HTTP(S) URLs.")
    try:
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import (
            BatchLogRecordProcessor,
            ConsoleLogRecordExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
        from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
        from opentelemetry.sdk.resources import Resource

        if exporter == "otlp":
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    except ImportError as exc:
        raise ValueError(
            "OpenTelemetry dependencies are missing; install the 'otel' extra."
        ) from exc

    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME") or "epaper-display",
            "service.version": __version__,
        }
    )
    log_exporter = (
        ConsoleLogRecordExporter(out=sys.stderr) if exporter == "console" else OTLPLogExporter()
    )
    metric_exporter = (
        ConsoleMetricExporter(out=sys.stderr) if exporter == "console" else OTLPMetricExporter()
    )
    reader = PeriodicExportingMetricReader(metric_exporter)
    duration_buckets = ExplicitBucketHistogramAggregation(
        boundaries=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60]
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[reader],
        views=[View(instrument_name="epaper.*.refresh.duration", aggregation=duration_buckets)],
    )
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    return Telemetry(meter_provider, logger_provider)
