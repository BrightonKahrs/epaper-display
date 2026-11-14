import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import questionary
from PIL import Image
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from epaper_display.catalog import Catalog
from epaper_display.cli import _run_service, main
from epaper_display.config import AppConfig
from epaper_display.source_registry import ApiSourceRegistry


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


class PickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.config = self.root / "config.json"
        self.config.write_text(
            json.dumps({"data_dir": str(self.root / "state")}), encoding="utf-8"
        )
        self.environment = patch.dict(
            "os.environ", {"EPAPER_DATA_DIR": str(self.root / "state")}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.catalog = Catalog(
            self.root / "state" / "catalog.sqlite3", self.root / "state" / "gallery"
        )
        self.image = self.root / "slide.png"
        Image.new("RGB", (20, 20), "red").save(self.image)

    def run_pick(self, *, terminal: bool = True) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            patch("sys.stdin.isatty", return_value=terminal),
            patch("sys.stdout.isatty", return_value=terminal),
        ):
            result = main(["--config", str(self.config), "pick"])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_replace_and_delete_local_slide_commands(self) -> None:
        slide = self.catalog.add_image(self.image, "Original")
        replacement = self.root / "replacement.png"
        Image.new("RGB", (20, 20), "blue").save(replacement)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                main(["--config", str(self.config), "replace", slide.id, str(replacement)]), 0
            )
            self.assertEqual(main(["--config", str(self.config), "delete", slide.id]), 0)
        self.assertIn(f"Replaced {slide.id}: Original", output.getvalue())
        self.assertIn(f"Deleted {slide.id}", output.getvalue())
        self.assertEqual(self.catalog.list_slides(), [])

    def test_configure_and_list_direct_image_source(self) -> None:
        arguments = [
            "--config", str(self.config), "sources", "configure",
            "chart", "https://example.test/chart", "--type", "image",
            "--query-param", "width=1200", "--ttl-seconds", "900",
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 0)
            self.assertEqual(
                main(["--config", str(self.config), "sources", "disable", "chart"]), 0
            )
            self.assertEqual(main(arguments), 0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--config", str(self.config), "sources", "list"]), 0)
        listed = json.loads(output.getvalue())[0]
        self.assertEqual(listed["type"], "image")
        self.assertEqual(listed["query_params"], {"width": "1200"})
        self.assertEqual(listed["ttl_seconds"], 900)
        self.assertFalse(listed["enabled"])
        registry = ApiSourceRegistry(self.root / "state" / "api-specs.sqlite3")
        self.assertEqual(registry.get("chart").source_type, "image")

    def test_enable_disable_by_id_and_list_all(self) -> None:
        slide = self.catalog.add_image(self.image, "Local")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--config", str(self.config), "disable", slide.id]), 0)
            self.assertEqual(main(["--config", str(self.config), "list"]), 0)
            self.assertEqual(main(["--config", str(self.config), "list", "--all"]), 0)
        self.assertIn(f"{slide.id}\tLocal\tssh\tdisabled", output.getvalue())
        self.assertEqual(self.catalog.list_slides(), [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(self.config), "enable", slide.id]), 0)
        self.assertEqual([item.id for item in self.catalog.list_slides()], [slide.id])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(main(["--config", str(self.config), "disable", "missing"]), 2)
        self.assertIn("Slide not found", stderr.getvalue())

    def test_pause_resume_commands_report_persisted_status(self) -> None:
        slide = self.catalog.add_image(self.image)
        self.catalog.select_slide(slide.id, now=100, interval_seconds=1800)
        for command, expected_paused in (("pause", True), ("resume", False)):
            with self.subTest(command=command):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["--config", str(self.config), command]), 0)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(["--config", str(self.config), "status"]), 0)
                status = json.loads(output.getvalue())
                self.assertEqual(status["rotation_paused"], expected_paused)
                self.assertEqual(status["current_slide_id"], slide.id)
                self.assertEqual(status["next_transition_at"] is None, expected_paused)

    def test_enable_disable_picker_filters_and_supports_cancellation(self) -> None:
        first = self.catalog.add_image(self.image, "First", now=10)
        second = self.catalog.add_image(
            self.image, "API", source="api:weather", external_id="today", now=11
        )
        self.catalog.set_enabled(second.id, False)
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("epaper_display.cli.questionary.select") as menu,
        ):
            menu.return_value.ask.return_value = None
            self.assertEqual(main(["--config", str(self.config), "enable", "pick"]), 0)
            self.assertEqual([c.value for c in menu.call_args.kwargs["choices"]], [second.id])
            self.assertFalse(self.catalog.list_slides(include_all=True)[1].enabled)
            menu.return_value.ask.return_value = second.id
            self.assertEqual(main(["--config", str(self.config), "enable", "pick"]), 0)
            menu.return_value.ask.return_value = first.id
            self.assertEqual(main(["--config", str(self.config), "disable", "pick"]), 0)
            self.assertEqual(
                [c.value for c in menu.call_args.kwargs["choices"]], [first.id, second.id]
            )
        self.assertEqual([item.id for item in self.catalog.list_slides()], [second.id])

    def test_enable_expired_slide_warns_and_list_all_marks_expired(self) -> None:
        slide = self.catalog.add_image(self.image, "Expired", now=10, expires_at=50)
        self.catalog.set_enabled(slide.id, False)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--config", str(self.config), "enable", slide.id]), 0)
            self.assertEqual(main(["--config", str(self.config), "list", "--all"]), 0)
        self.assertIn("expired", output.getvalue())
        self.assertEqual(self.catalog.list_slides(), [])

    def test_replace_pick_uses_arrow_keys_and_excludes_api_slides(self) -> None:
        first = self.catalog.add_image(self.image, "First", now=10)
        self.catalog.add_image(
            self.image, "API", source="api:weather", external_id="today", now=11
        )
        second = self.catalog.add_image(self.image, "Second", now=12)
        self.catalog.select_slide(first.id, now=90, interval_seconds=1800)
        replacement = self.root / "replacement.png"
        Image.new("RGB", (20, 20), "blue").save(replacement)
        real_select = questionary.select
        output = io.StringIO()
        with create_pipe_input() as keyboard:
            keyboard.send_text("\x1b[B\r")

            def select(*args, **kwargs):
                return real_select(*args, **kwargs, input=keyboard, output=DummyOutput())

            with (
                contextlib.redirect_stdout(output),
                patch("sys.stdin.isatty", return_value=True),
                patch("sys.stdout.isatty", return_value=True),
                patch("epaper_display.cli.questionary.select", side_effect=select) as menu,
            ):
                result = main(["--config", str(self.config), "replace", "pick", str(replacement)])
        self.assertEqual(result, 0)
        self.assertIn(f"Replaced {second.id}: Second", output.getvalue())
        self.assertEqual(
            [choice.value for choice in menu.call_args.kwargs["choices"]],
            [first.id, second.id],
        )
        with Image.open(second.path) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(self.catalog.get_state("current_slide_id"), first.id)

    def test_delete_pick_requires_confirmation_and_preserves_cancelled_slide(self) -> None:
        slide = self.catalog.add_image(self.image)
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("epaper_display.cli.questionary.select") as menu,
            patch("epaper_display.cli.questionary.confirm") as confirm,
        ):
            menu.return_value.ask.return_value = slide.id
            confirm.return_value.ask.return_value = False
            self.assertEqual(main(["--config", str(self.config), "delete", "pick"]), 0)
            self.assertTrue(slide.path.exists())
            confirm.return_value.ask.return_value = True
            self.assertEqual(main(["--config", str(self.config), "delete", "pick"]), 0)
        self.assertFalse(slide.path.exists())
        self.assertEqual(self.catalog.list_slides(), [])
        self.assertFalse(confirm.call_args.kwargs["default"])

    def test_management_pickers_handle_cancellation_and_noninteractive_terminal(self) -> None:
        slide = self.catalog.add_image(self.image)
        for command in (["replace", "pick", str(self.image)], ["delete", "pick"]):
            with self.subTest(command=command):
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    patch("sys.stdin.isatty", return_value=True),
                    patch("sys.stdout.isatty", return_value=True),
                    patch("epaper_display.cli.questionary.select") as menu,
                ):
                    menu.return_value.ask.return_value = None
                    self.assertEqual(main(["--config", str(self.config), *command]), 0)
                stderr = io.StringIO()
                with (
                    patch("sys.stdin.isatty", return_value=False),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(main(["--config", str(self.config), *command]), 2)
                self.assertIn("ssh -t", stderr.getvalue())
                self.assertTrue(slide.path.exists())

    def test_arrow_keys_select_and_persist_slide_from_real_menu(self) -> None:
        first = self.catalog.add_image(self.image, "Same title", now=10)
        second = self.catalog.add_image(self.image, "Same title", now=20)
        self.catalog.add_image(self.image, "Expired", now=30, expires_at=50)
        self.catalog.select_slide(first.id, now=90, interval_seconds=1800)
        real_select = questionary.select

        with create_pipe_input() as keyboard:
            keyboard.send_text("\x1b[B\r")

            def select(*args, **kwargs):
                return real_select(*args, **kwargs, input=keyboard, output=DummyOutput())

            before_selection = time.time()
            with patch(
                "epaper_display.cli.questionary.select", side_effect=select
            ) as menu:
                result, stdout, stderr = self.run_pick()
            after_selection = time.time()

        self.assertEqual(result, 0, stderr)
        self.assertIn(f"Selected {second.id}: Same title", stdout)
        choices = menu.call_args.kwargs["choices"]
        self.assertEqual([choice.value for choice in choices], [first.id, second.id])
        self.assertNotEqual(choices[0].title, choices[1].title)
        self.assertEqual(menu.call_args.kwargs["default"], first.id)
        reopened = Catalog(self.catalog.database_path, self.catalog.gallery_dir)
        self.assertEqual(reopened.get_state("current_slide_id"), second.id)
        deadline = float(reopened.get_state("next_transition_at"))
        self.assertGreaterEqual(deadline, before_selection + 1800)
        self.assertLessEqual(deadline, after_selection + 1800)

    def test_cancellation_leaves_selection_and_deadline_unchanged(self) -> None:
        slide = self.catalog.add_image(self.image, now=10)
        self.catalog.select_slide(slide.id, now=90, interval_seconds=1800)
        for outcome in (None, EOFError()):
            with self.subTest(outcome=outcome):
                with patch("epaper_display.cli.questionary.select") as menu:
                    if isinstance(outcome, EOFError):
                        menu.return_value.ask.side_effect = outcome
                    else:
                        menu.return_value.ask.return_value = outcome
                    result, stdout, stderr = self.run_pick()
                self.assertEqual(result, 0, stderr)
                self.assertIn("cancelled", stdout)
                self.assertEqual(self.catalog.get_state("current_slide_id"), slide.id)
                self.assertEqual(float(self.catalog.get_state("next_transition_at")), 1890)

    def test_empty_gallery_reports_how_to_add_slides(self) -> None:
        with patch("epaper_display.cli.questionary.select") as menu:
            result, _, stderr = self.run_pick()
        self.assertEqual(result, 2)
        self.assertIn("No available slides", stderr)
        menu.assert_not_called()

    def test_noninteractive_terminal_reports_ssh_guidance(self) -> None:
        self.catalog.add_image(self.image)
        with patch("epaper_display.cli.questionary.select") as menu:
            result, _, stderr = self.run_pick(terminal=False)
        self.assertEqual(result, 2)
        self.assertIn("ssh -t", stderr)
        menu.assert_not_called()

    def test_slide_expiring_while_menu_is_open_cannot_be_selected(self) -> None:
        slide = self.catalog.add_image(self.image, now=10, expires_at=120)
        with (
            patch("epaper_display.cli.questionary.select") as menu,
            patch("epaper_display.cli.time.time", side_effect=[100, 150]),
        ):
            menu.return_value.ask.return_value = slide.id
            result, _, stderr = self.run_pick()
        self.assertEqual(result, 2)
        self.assertIn("Slide not found or expired", stderr)
        self.assertIsNone(self.catalog.get_state("current_slide_id"))


class RunOptionsTests(unittest.TestCase):
    def test_run_passes_opt_in_fast_flag_to_service(self) -> None:
        config = AppConfig(Path("unused"))
        for arguments, expected_fast in ((["run"], False), (["run", "--fast"], True)):
            with self.subTest(arguments=arguments):
                with (
                    patch("epaper_display.cli.AppConfig.load", return_value=config),
                    patch("epaper_display.cli._make_catalog") as catalog,
                    patch("epaper_display.cli._make_source_registry") as registry,
                    patch("epaper_display.cli._run_service") as run,
                ):
                    self.assertEqual(main(arguments), 0)
                    run.assert_called_once_with(
                        config,
                        catalog.return_value,
                        registry.return_value,
                        None,
                        fast=expected_fast,
                    )

    def test_service_passes_fast_flag_to_hardware_adapter(self) -> None:
        config = AppConfig(Path("unused"))
        catalog, registry = Mock(), Mock()
        with (
            patch("epaper_display.cli.InkyDisplay") as display,
            patch("epaper_display.cli.CarouselService") as service,
            patch("epaper_display.cli.signal.signal"),
        ):
            _run_service(config, catalog, registry, None, fast=True)
        display.assert_called_once_with(fast=True)
        service.assert_called_once_with(config, catalog, registry, display.return_value)
        service.return_value.run.assert_called_once_with()

    def test_fast_and_preview_cannot_be_combined(self) -> None:
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as error,
        ):
            main(["run", "--fast", "--preview", "preview.png"])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("not allowed with argument", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
