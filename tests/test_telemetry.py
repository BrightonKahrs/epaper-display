import contextlib
import io
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from epaper_display.catalog import Catalog
from epaper_display.config import AppConfig
from epaper_display.display import PreviewDisplay
from epaper_display.refresh_worker import refresh_due_sources
from epaper_display.service import CarouselService
from epaper_display.source_registry import ApiSource, ApiSourceRegistry
from epaper_display.telemetry import create_telemetry

try:
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
except ImportError:
    ExportLogsServiceRequest = None
    ExportMetricsServiceRequest = None


class TelemetryConfigurationTests(unittest.TestCase):
    def test_disabled_by_default_and_invalid_configuration_fails_explicitly(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(create_telemetry())
        for environment, message in (
            ({"EPD_OTEL_EXPORTER": "invalid"}, "EPD_OTEL_EXPORTER"),
            ({"EPD_OTEL_EXPORTER": "otlp"}, "requires"),
            ({"EPD_OTEL_EXPORTER": "otlp", "OTEL_EXPORTER_OTLP_ENDPOINT": "not-a-url"},
             "HTTP"),
        ):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(ValueError, message):
                    create_telemetry()

    def test_missing_optional_dependencies_are_reported_only_when_enabled(self) -> None:
        with patch.dict(sys.modules, {"opentelemetry.sdk._logs": None}):
            with patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(create_telemetry())
            with patch.dict(os.environ, {"EPD_OTEL_EXPORTER": "console"}, clear=True):
                with self.assertRaisesRegex(ValueError, "install the 'otel' extra"):
                    create_telemetry()


@unittest.skipIf(ExportLogsServiceRequest is None, "optional OpenTelemetry dependencies not installed")
class TelemetryIntegrationTests(unittest.TestCase):
    def test_console_records_real_display_and_source_results_without_sensitive_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"EPD_OTEL_EXPORTER": "console"}, clear=True
        ):
            root = Path(temporary)
            catalog = Catalog(root / "catalog.sqlite3", root / "gallery")
            sources = ApiSourceRegistry(root / "sources.sqlite3")
            sources.save(ApiSource("secret-source-id", "https://example.test/secret-url"))
            image = root / "secret-slide-name.png"
            Image.new("RGB", (20, 20), "red").save(image)
            catalog.add_image(image)
            second = catalog.add_image(image)
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                telemetry = create_telemetry()
                try:
                    service = CarouselService(
                        AppConfig(root), catalog, sources, PreviewDisplay(root / "preview.png"),
                        telemetry=telemetry,
                    )
                    service.tick(now=100)
                    catalog.select_slide(second.id, now=101, interval_seconds=1800)
                    with (
                        patch("epaper_display.service.show_slide", side_effect=OSError("private-error")),
                        patch("epaper_display.service.logger.exception"),
                    ):
                        service.tick(now=101)
                    with patch("epaper_display.refresh_worker.refresh_source", return_value=2):
                        refresh_due_sources(sources, catalog, now=101, telemetry=telemetry)
                    with (
                        patch("epaper_display.refresh_worker.logger.warning"),
                        patch(
                            "epaper_display.refresh_worker.refresh_source",
                            side_effect=ValueError("secret-token"),
                        ),
                    ):
                        refresh_due_sources(sources, catalog, now=102, force=True, telemetry=telemetry)
                finally:
                    telemetry.shutdown()
            exported = output.getvalue()
            for name in (
                "Carousel telemetry initialized", "Display refresh completed", "Display refresh failed",
                "Source refresh completed", "Source refresh failed",
                "epaper.display.refreshes", "epaper.display.refresh.duration",
                "epaper.source.refreshes", "epaper.source.refresh.duration",
                "epaper.source.slides",
            ):
                self.assertIn(name, exported)
            for secret in ("secret-source-id", "secret-url", "secret-token", "secret-slide-name", "private-error"):
                self.assertNotIn(secret, exported)

    def test_otlp_http_exports_protobuf_logs_and_metrics_to_collector(self) -> None:
        received = []

        class Collector(BaseHTTPRequestHandler):
            def do_POST(self):
                content = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, self.headers["Content-Type"], content))
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            for endpoints in (
                {"OTEL_EXPORTER_OTLP_ENDPOINT": base_url},
                {
                    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": f"{base_url}/custom/logs",
                    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": f"{base_url}/custom/metrics",
                },
            ):
                environment = {
                    "EPD_OTEL_EXPORTER": "otlp",
                    "OTEL_SERVICE_NAME": "test-epaper",
                    **endpoints,
                }
                with patch.dict(os.environ, environment, clear=True):
                    telemetry = create_telemetry()
                    telemetry.display_refresh(True, 0.125)
                    telemetry.source_refresh("image", False, 0.5, 0)
                    telemetry.shutdown()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        paths = {path for path, _, _ in received}
        self.assertIn("/v1/logs", paths)
        self.assertIn("/v1/metrics", paths)
        self.assertIn("/custom/logs", paths)
        self.assertIn("/custom/metrics", paths)
        for path, content_type, payload in received:
            self.assertIn("application/x-protobuf", content_type)
            if path in ("/v1/logs", "/custom/logs"):
                request = ExportLogsServiceRequest.FromString(payload)
                self.assertTrue(request.resource_logs)
                self.assertIn("test-epaper", str(request))
                self.assertIn("Display refresh completed", str(request))
                self.assertIn("Source refresh failed", str(request))
            elif path in ("/v1/metrics", "/custom/metrics"):
                request = ExportMetricsServiceRequest.FromString(payload)
                self.assertTrue(request.resource_metrics)
                self.assertIn("test-epaper", str(request))
                self.assertIn("epaper.display.refreshes", str(request))
                self.assertIn("epaper.source.refreshes", str(request))
                metrics = {
                    metric.name: metric
                    for resource in request.resource_metrics
                    for scope in resource.scope_metrics
                    for metric in scope.metrics
                }
                display_duration = metrics["epaper.display.refresh.duration"].histogram.data_points[0]
                self.assertIn(0.05, display_duration.explicit_bounds)
                self.assertEqual(display_duration.count, 1)


if __name__ == "__main__":
    unittest.main()
