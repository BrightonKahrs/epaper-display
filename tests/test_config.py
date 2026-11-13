import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from epaper_display.config import AppConfig


class ConfigTests(unittest.TestCase):
    def test_default_configuration_uses_thirty_minute_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "config.json"
            path.write_text('{"data_dir": "data"}', encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                config = AppConfig.load(path)

        self.assertEqual(config.interval_seconds, 1800)
        self.assertEqual(config.display_orientation, "portrait")
        self.assertEqual(config.portrait_rotation_degrees, 90)
        self.assertNotEqual(
            config.api_specs_database_path, config.image_metadata_database_path
        )

    def test_feed_definitions_are_not_accepted_in_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {
                                "id": "bad",
                                "url": "https://example.test",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "API source registry"):
                AppConfig.load(path)

    def test_portrait_rotation_is_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "data_dir": "data",
                        "display_orientation": "portrait",
                        "portrait_rotation_degrees": 270,
                    }
                ),
                encoding="utf-8",
            )
            config = AppConfig.load(path)

        self.assertEqual(config.display_orientation, "portrait")
        self.assertEqual(config.portrait_rotation_degrees, 270)


if __name__ == "__main__":
    unittest.main()
