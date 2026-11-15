import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from epaper_display.catalog import Catalog
from epaper_display.config import AppConfig
from epaper_display.display import PreviewDisplay
from epaper_display.service import CarouselService
from epaper_display.source_registry import ApiSourceRegistry


class ServiceTests(unittest.TestCase):
    def test_reported_render_state_tracks_success_failure_and_empty_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog = Catalog(root / "catalog.sqlite3", root / "gallery")
            sources = ApiSourceRegistry(root / "sources.sqlite3")
            image = root / "source.png"
            Image.new("RGB", (20, 20), "red").save(image)
            first = catalog.add_image(image, "First", now=100)
            second = catalog.add_image(image, "Second", now=101)
            service = CarouselService(AppConfig(root), catalog, sources, PreviewDisplay(root / "preview.png"))
            service.tick(now=200)
            control = Catalog(catalog.database_path, catalog.gallery_dir)
            self.assertEqual(control.get_state("displayed_slide_id"), first.id)
            self.assertEqual(control.get_state("display_target"), "preview")
            self.assertEqual(control.get_state("displayed_at"), "200")
            control.select_slide(second.id, now=201, interval_seconds=1800)

            def failing_display(*args) -> None:
                state = control.snapshot(now=202).state
                self.assertEqual(state["displayed_slide_id"], first.id)
                self.assertEqual(state["rendering_slide_id"], second.id)
                raise OSError("panel unavailable")

            with patch("epaper_display.service.show_slide", side_effect=failing_display), self.assertLogs(
                "epaper_display.service", level="ERROR"
            ):
                service.tick(now=202)
            state = control.snapshot(now=202).state
            self.assertEqual(state["current_slide_id"], second.id)
            self.assertEqual(state["displayed_slide_id"], first.id)
            self.assertEqual(state["rendering_slide_id"], "")
            self.assertIn("panel unavailable", state["display_error"])
            with patch("epaper_display.service.show_slide") as display:
                service.tick(now=207)
                display.assert_not_called()
            self.assertEqual(control.get_state("service_heartbeat_at"), "207")
            service.tick(now=232)
            self.assertEqual(control.get_state("displayed_slide_id"), second.id)
            self.assertEqual(control.get_state("display_error"), "")
            control.set_enabled(first.id, False)
            control.set_enabled(second.id, False)
            service.tick(now=233)
            self.assertFalse(control.get_state("current_slide_id"))
            self.assertEqual(control.get_state("displayed_slide_id"), second.id)

    def test_clean_service_shutdown_reports_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog = Catalog(root / "catalog.sqlite3", root / "gallery")
            service = CarouselService(
                AppConfig(root), catalog, ApiSourceRegistry(root / "sources.sqlite3"),
                PreviewDisplay(root / "preview.png"),
            )
            with patch.object(service, "tick", side_effect=service.stop):
                service.run()
            self.assertEqual(catalog.get_state("service_status"), "stopped")
            self.assertEqual(catalog.get_state("rendering_slide_id"), "")
            self.assertGreater(float(catalog.get_state("service_heartbeat_at")), 0)

    def test_running_service_observes_scope_and_folder_moves_even_while_paused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog = Catalog(root / "catalog.sqlite3", root / "gallery")
            sources = ApiSourceRegistry(root / "api-specs.sqlite3")
            preview = root / "preview.png"
            red, blue = root / "red.png", root / "blue.png"
            Image.new("RGB", (20, 20), "red").save(red)
            Image.new("RGB", (20, 20), "blue").save(blue)
            outside = catalog.add_image(red, now=100)
            inside = catalog.add_image(blue, folder="space/planets/moons", now=101)
            service = CarouselService(AppConfig(root), catalog, sources, PreviewDisplay(preview))
            service.tick(now=200)
            self.assertEqual(catalog.get_state("current_slide_id"), outside.id)
            control = Catalog(catalog.database_path, catalog.gallery_dir)
            control.set_paused(True, 201, 1800)
            control.set_scope("space", 202, 1800)
            service.tick(now=203)
            self.assertEqual(catalog.get_state("current_slide_id"), inside.id)
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (0, 0, 255))
            control.move_slide(inside.id, None)
            service.tick(now=204)
            self.assertFalse(catalog.get_state("current_slide_id"))
            self.assertEqual(catalog.get_scope(), "space")
            control.move_slide(outside.id, "space/planets")
            service.tick(now=205)
            self.assertEqual(catalog.get_state("current_slide_id"), outside.id)
            self.assertEqual(catalog.get_state("next_transition_at"), "")
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (255, 0, 0))

    def test_paused_service_holds_slide_and_allows_manual_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog = Catalog(root / "catalog.sqlite3", root / "gallery")
            sources = ApiSourceRegistry(root / "api-specs.sqlite3")
            preview = root / "preview.png"
            red, blue = root / "red.png", root / "blue.png"
            Image.new("RGB", (20, 20), "red").save(red)
            Image.new("RGB", (20, 20), "blue").save(blue)
            first = catalog.add_image(red, now=100)
            second = catalog.add_image(blue, now=101)
            service = CarouselService(
                AppConfig(root), catalog, sources, PreviewDisplay(preview)
            )
            service.tick(now=102)
            catalog.set_paused(True, now=103, interval_seconds=1800)
            service.tick(now=5000)
            self.assertEqual(catalog.get_state("current_slide_id"), first.id)
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (255, 0, 0))
            catalog.select_slide(second.id, now=5001, interval_seconds=1800)
            service.tick(now=5002)
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (0, 0, 255))
            catalog.set_paused(False, now=6000, interval_seconds=1800)
            service.tick(now=7799)
            self.assertEqual(catalog.get_state("current_slide_id"), second.id)
            service.tick(now=7800)
            self.assertEqual(catalog.get_state("current_slide_id"), first.id)

    def test_disabling_current_slide_switches_and_reenabling_restores_eligibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog = Catalog(root / "catalog.sqlite3", root / "gallery")
            sources = ApiSourceRegistry(root / "api-specs.sqlite3")
            preview = root / "preview.png"
            red, blue = root / "red.png", root / "blue.png"
            Image.new("RGB", (20, 20), "red").save(red)
            Image.new("RGB", (20, 20), "blue").save(blue)
            first = catalog.add_image(red, now=100)
            second = catalog.add_image(blue, now=101)
            service = CarouselService(
                AppConfig(root, interval_seconds=1800), catalog, sources, PreviewDisplay(preview)
            )
            service.tick(now=102)
            catalog.set_enabled(first.id, False)
            service.tick(now=103)
            self.assertEqual(catalog.get_state("current_slide_id"), second.id)
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (0, 0, 255))
            catalog.set_enabled(second.id, False)
            service.tick(now=104)
            self.assertFalse(catalog.get_state("current_slide_id"))
            catalog.set_enabled(first.id, True)
            service.tick(now=105)
            self.assertEqual(catalog.get_state("current_slide_id"), first.id)
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (255, 0, 0))

    def test_replacing_and_deleting_current_slide_updates_running_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog = Catalog(root / "catalog.sqlite3", root / "gallery")
            sources = ApiSourceRegistry(root / "api-specs.sqlite3")
            preview = root / "preview.png"
            red, blue, green = (root / f"{color}.png" for color in ("red", "blue", "green"))
            for path, color in ((red, "red"), (blue, "blue"), (green, "green")):
                Image.new("RGB", (20, 20), color).save(path)
            first = catalog.add_image(red, now=100)
            second = catalog.add_image(green, now=101)
            service = CarouselService(
                AppConfig(root, interval_seconds=1800), catalog, sources, PreviewDisplay(preview)
            )
            service.tick(now=102)
            catalog.replace_image(first.id, blue)
            service.tick(now=103)
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (0, 0, 255))
            catalog.delete_slide(first.id)
            service.tick(now=104)
            self.assertEqual(catalog.get_state("current_slide_id"), second.id)
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (0, 128, 0))
            catalog.delete_slide(second.id)
            service.tick(now=105)
            self.assertFalse(catalog.get_state("current_slide_id"))

    def test_service_starts_when_first_slide_arrives_after_boot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog = Catalog(root / "catalog.sqlite3", root / "gallery")
            sources = ApiSourceRegistry(root / "api-specs.sqlite3")
            preview = root / "preview.png"
            config = AppConfig(root, interval_seconds=1800)
            service = CarouselService(
                config, catalog, sources, PreviewDisplay(preview)
            )

            service.tick(now=100)
            self.assertFalse(preview.exists())

            first_image = root / "first.png"
            Image.new("RGB", (20, 20), "red").save(first_image)
            slide = catalog.add_image(first_image, now=101)
            service.tick(now=102)

            self.assertEqual(catalog.get_state("current_slide_id"), slide.id)
            self.assertTrue(preview.exists())

    def test_service_draws_manual_selection_and_rotates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog = Catalog(root / "catalog.sqlite3", root / "gallery")
            red_image = root / "red.png"
            blue_image = root / "blue.png"
            Image.new("RGB", (20, 20), "red").save(red_image)
            Image.new("RGB", (20, 20), "blue").save(blue_image)
            first = catalog.add_image(red_image, now=100)
            second = catalog.add_image(blue_image, now=101)
            catalog.initialize_carousel(now=200, interval_seconds=1800)
            preview = root / "preview.png"
            config = AppConfig(root, interval_seconds=1800)
            sources = ApiSourceRegistry(root / "api-specs.sqlite3")
            service = CarouselService(
                config, catalog, sources, PreviewDisplay(preview)
            )

            service.tick(now=201)
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (255, 0, 0))

            catalog.select_slide(second.id, now=202, interval_seconds=1800)
            service.tick(now=202)
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (0, 0, 255))

            service.tick(now=2002)
            self.assertEqual(catalog.get_state("current_slide_id"), first.id)
            with Image.open(preview) as image:
                self.assertEqual(image.getpixel((800, 600)), (255, 0, 0))


if __name__ == "__main__":
    unittest.main()
