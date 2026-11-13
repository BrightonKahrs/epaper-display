import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from PIL import Image

from epaper_display.catalog import Catalog
from epaper_display.api_client import refresh_source
from epaper_display.refresh_worker import refresh_due_sources
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
