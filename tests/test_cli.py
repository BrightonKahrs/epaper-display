import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from epaper_display.cli import main


class CliTests(unittest.TestCase):
    def test_api_sources_can_be_configured_listed_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config.json"
            config.write_text(
                json.dumps({"data_dir": str(root / "state")}), encoding="utf-8"
            )

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(config),
                            "sources",
                            "configure",
                            "weather",
                            "https://example.test/api/slides",
                            "--ttl-seconds",
                            "1200",
                            "--query-param",
                            "city=Philadelphia",
                            "--token-env",
                            "WEATHER_TOKEN",
                        ]
                    ),
                    0,
                )
            listed = io.StringIO()
            with contextlib.redirect_stdout(listed):
                self.assertEqual(
                    main(["--config", str(config), "sources", "list"]), 0
                )

            sources = json.loads(listed.getvalue())
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["query_params"], {"city": "Philadelphia"})
            self.assertEqual(sources[0]["token_env"], "WEATHER_TOKEN")
            self.assertEqual(sources[0]["ttl_seconds"], 1200)

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(config),
                            "sources",
                            "disable",
                            "weather",
                        ]
                    ),
                    0,
                )
            listed = io.StringIO()
            with contextlib.redirect_stdout(listed):
                self.assertEqual(
                    main(["--config", str(config), "sources", "list"]), 0
                )
            self.assertFalse(json.loads(listed.getvalue())[0]["enabled"])

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(config),
                            "sources",
                            "enable",
                            "weather",
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(config),
                            "sources",
                            "remove",
                            "weather",
                        ]
                    ),
                    0,
                )

            listed = io.StringIO()
            with contextlib.redirect_stdout(listed):
                self.assertEqual(
                    main(["--config", str(config), "sources", "list"]), 0
                )
            self.assertEqual(json.loads(listed.getvalue()), [])

            self.assertTrue((root / "state" / "api-specs.sqlite3").is_file())
            self.assertTrue((root / "state" / "catalog.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
