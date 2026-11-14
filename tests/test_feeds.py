import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from PIL import Image

from epaper_display.catalog import Catalog
from epaper_display.api_client import refresh_source
from epaper_display.config import AppConfig
from epaper_display.display import PreviewDisplay
from epaper_display.refresh_worker import refresh_due_sources
from epaper_display.service import CarouselService
from epaper_display.source_registry import ApiSource, ApiSourceRegistry


class FeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.catalog_path = root / "image-metadata.sqlite3"
        self.sources_path = root / "api-specs.sqlite3"
        self.catalog = Catalog(self.catalog_path, root / "gallery")
        self.sources = ApiSourceRegistry(self.sources_path)
        self.source = ApiSource("weather", "https://example.test/slides", 3600)
        self.sources.save(self.source)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_source_is_not_requested_until_ttl_expires(self) -> None:
        self.sources.record_success("weather", now=100, ttl_seconds=3600)

        with patch("epaper_display.refresh_worker.refresh_source") as refresh:
            refresh_due_sources(self.sources, self.catalog, now=200)
            refresh_due_sources(self.sources, self.catalog, now=3700)

        refresh.assert_called_once_with(self.source, self.catalog, self.sources, 3700)

    def test_direct_image_refresh_updates_stable_slide_and_current_display(self) -> None:
        source = ApiSource(
            "chart", "https://example.test/chart?format=png", ttl_seconds=1200,
            source_type="image", query_params={"width": "1200", "height": "1600"},
            token_env="TEST_IMAGE_TOKEN", timeout_seconds=15,
        )
        self.sources.save(source)
        preview = self.catalog_path.parent / "preview.png"
        service = CarouselService(
            AppConfig(self.catalog_path.parent), self.catalog, self.sources, PreviewDisplay(preview)
        )
        first_id = None
        revision = None
        for now, color in ((100, "red"), (200, "blue"), (300, "blue")):
            buffer = io.BytesIO()
            Image.new("RGB", (1200, 1600), color).save(buffer, format="PNG")
            with (
                patch.dict(os.environ, {"TEST_IMAGE_TOKEN": "test-token"}),
                patch("epaper_display.api_client.urlopen", return_value=io.BytesIO(buffer.getvalue())) as request,
            ):
                self.assertEqual(refresh_source(source, self.catalog, self.sources, now=now), 1)
            self.assertEqual(request.call_count, 1)
            sent = request.call_args.args[0]
            self.assertEqual(sent.get_header("Authorization"), "Bearer test-token")
            self.assertEqual(request.call_args.kwargs["timeout"], 15)
            self.assertEqual(
                parse_qs(urlparse(sent.full_url).query),
                {"format": ["png"], "width": ["1200"], "height": ["1600"]},
            )
            slides = self.catalog.list_slides(now=now)
            self.assertEqual(len(slides), 1)
            if first_id is None:
                first_id = slides[0].id
            self.assertEqual(slides[0].id, first_id)
            self.assertEqual(slides[0].title, "chart")
            self.assertEqual(slides[0].created_at, 100)
            service.tick(now=now)
            with Image.open(preview) as image:
                self.assertEqual(
                    image.getpixel((600, 800)), (255, 0, 0) if color == "red" else (0, 0, 255)
                )
            current_revision = self.catalog.get_state(f"slide_revision:{first_id}")
            if now == 300:
                self.assertEqual(current_revision, revision)
            revision = current_revision
        self.assertEqual(self.sources.refresh_status()["chart"]["next_refresh_at"], 1500)
        self.assertEqual(list(self.catalog.gallery_dir.glob(".source-download-*")), [])

    def test_direct_image_failure_preserves_cached_slide_and_records_backoff(self) -> None:
        self.sources.set_enabled("weather", False)
        source = ApiSource("image", "https://example.test/image", source_type="image")
        self.sources.save(source)
        buffer = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(buffer, format="PNG")
        with patch("epaper_display.api_client.urlopen", return_value=io.BytesIO(buffer.getvalue())):
            refresh_source(source, self.catalog, self.sources, now=100)
        original = self.catalog.list_slides(now=100)[0]
        for content in (b"<html>not an image</html>", b'{"error":"unavailable"}'):
            with patch("epaper_display.api_client.urlopen", return_value=io.BytesIO(content)):
                with self.assertLogs("epaper_display.refresh_worker", level="WARNING"):
                    refresh_due_sources(self.sources, self.catalog, now=200, force=True)
            self.assertTrue(self.sources.refresh_status()["image"]["last_error"])
            self.assertGreater(self.sources.refresh_status()["image"]["next_refresh_at"], 200)
            self.assertEqual(self.catalog.list_slides(now=201)[0].id, original.id)
            with Image.open(original.path) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(list(self.catalog.gallery_dir.glob(".source-download-*")), [])

    def test_direct_image_missing_token_and_size_limit_are_rejected(self) -> None:
        source = ApiSource(
            "image", "https://example.test/image", source_type="image",
            token_env="MISSING_IMAGE_TOKEN",
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("epaper_display.api_client.urlopen") as request,
            self.assertRaisesRegex(ValueError, "requires environment variable"),
        ):
            refresh_source(source, self.catalog, self.sources)
        request.assert_not_called()
        source = ApiSource("image", "https://example.test/image", source_type="image")
        with (
            patch("epaper_display.api_client._MAX_IMAGE_BYTES", 10),
            patch("epaper_display.api_client.urlopen", return_value=io.BytesIO(b"x" * 11)),
            self.assertRaisesRegex(ValueError, "Image exceeds"),
        ):
            refresh_source(source, self.catalog, self.sources)
        self.assertEqual(self.catalog.list_slides(), [])

    def test_direct_image_worker_honors_ttl_and_source_disable(self) -> None:
        self.sources.set_enabled("weather", False)
        source = ApiSource("image", "https://example.test/image", source_type="image")
        self.sources.save(source)
        self.sources.record_success("image", now=100, ttl_seconds=3600)
        with patch("epaper_display.refresh_worker.refresh_source") as refresh:
            refresh_due_sources(self.sources, self.catalog, now=200)
            refresh.assert_not_called()
            refresh_due_sources(self.sources, self.catalog, now=3700)
            refresh.assert_called_once_with(source, self.catalog, self.sources, 3700)
            refresh.reset_mock()
            self.sources.set_enabled("image", False)
            refresh_due_sources(self.sources, self.catalog, now=7400, force=True)
            refresh.assert_not_called()

    def test_existing_registry_migrates_json_sources_without_losing_state(self) -> None:
        self.sources.record_success("weather", now=100, ttl_seconds=3600)
        with closing(sqlite3.connect(self.sources_path)) as connection:
            connection.execute("ALTER TABLE source_specs DROP COLUMN source_type")
            connection.commit()
        reopened = ApiSourceRegistry(self.sources_path)
        self.assertEqual(reopened.get("weather"), self.source)
        self.assertEqual(reopened.refresh_status()["weather"]["next_refresh_at"], 3700)
        image_source = ApiSource("chart", "https://example.test/chart", source_type="image")
        reopened.save(image_source)
        self.assertEqual(ApiSourceRegistry(self.sources_path).get("chart"), image_source)
        with self.assertRaisesRegex(ValueError, "Source type"):
            reopened.save(ApiSource("bad", "https://example.test", source_type="html"))

    def test_configurable_mapping_downloads_images_and_updates_stable_slides(self) -> None:
        image_buffer = io.BytesIO()
        Image.new("RGB", (20, 20), "green").save(image_buffer, format="PNG")
        image_data = image_buffer.getvalue()
        payload = json.dumps(
            {
                "data": {
                    "cards": [
                        {
                            "content": {"uuid": "today", "label": "Current conditions"},
                            "assets": {"image": "/images/current.png"},
                            "valid_until": "2026-11-14T00:00:00Z",
                        },
                        {
                            "content": {"uuid": "cdn-today", "label": "CDN image"},
                            "assets": {"image": "https://cdn.example.test/current.png"},
                        },
                    ]
                }
            }
        ).encode()
        response_data = [payload, image_data, image_data]
        source = ApiSource(
            "weather",
            "https://example.test/api/slides?lang=en",
            3600,
            query_params={"city": "Philadelphia"},
            token_env="TEST_FEED_TOKEN",
            slides_key="data.cards",
            id_key="content.uuid",
            title_key="content.label",
            image_url_key="assets.image",
            expires_at_key="valid_until",
        )
        self.sources.save(source)

        def fake_urlopen(request, timeout):
            return io.BytesIO(response_data.pop(0))

        with patch.dict(os.environ, {"TEST_FEED_TOKEN": "test-token"}):
            with patch(
                "epaper_display.api_client.urlopen", side_effect=fake_urlopen
            ) as request:
                imported = refresh_source(source, self.catalog, self.sources, now=100)

        self.assertEqual(imported, 2)
        slides = self.catalog.list_slides(now=101)
        self.assertEqual(len(slides), 2)
        self.assertEqual(
            {slide.title for slide in slides}, {"Current conditions", "CDN image"}
        )
        self.assertTrue(all(slide.source == "api:weather" for slide in slides))
        self.assertEqual(request.call_count, 3)
        feed_request = request.call_args_list[0].args[0]
        self.assertEqual(
            parse_qs(urlparse(feed_request.full_url).query),
            {"lang": ["en"], "city": ["Philadelphia"]},
        )
        self.assertEqual(
            request.call_args_list[1].args[0].get_header("Authorization"),
            "Bearer test-token",
        )
        self.assertIsNone(request.call_args_list[2].args[0].get_header("Authorization"))
        self.assertEqual(
            self.sources.refresh_status()["weather"]["last_success_at"], 100
        )

    def test_api_specs_and_slide_metadata_use_separate_databases(self) -> None:
        reopened_sources = ApiSourceRegistry(self.sources_path)
        self.assertEqual(reopened_sources.get("weather"), self.source)

        connection = sqlite3.connect(self.sources_path)
        try:
            source_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
        connection = sqlite3.connect(self.catalog_path)
        try:
            catalog_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()

        self.assertIn("source_specs", source_tables)
        self.assertIn("refresh_state", source_tables)
        self.assertNotIn("slides", source_tables)
        self.assertIn("slides", catalog_tables)
        self.assertNotIn("source_specs", catalog_tables)

    def test_source_failures_use_bounded_exponential_backoff(self) -> None:
        self.sources.record_failure("weather", now=100, error="first")
        first_status = self.sources.refresh_status()["weather"]
        self.sources.record_failure("weather", now=200, error="second")
        second_status = self.sources.refresh_status()["weather"]

        self.assertEqual(first_status["next_refresh_at"], 160)
        self.assertEqual(second_status["next_refresh_at"], 320)
        self.assertEqual(second_status["failure_count"], 2)


if __name__ == "__main__":
    unittest.main()
