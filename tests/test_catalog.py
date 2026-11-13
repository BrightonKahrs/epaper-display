import tempfile
import unittest
from pathlib import Path

from PIL import Image

from epaper_display.catalog import Catalog


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.catalog = Catalog(self.root / "catalog.sqlite3", self.root / "gallery")
        self.image_path = self.root / "source.png"
        Image.new("RGB", (40, 30), "red").save(self.image_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_import_persists_slide_and_file(self) -> None:
        slide = self.catalog.add_image(self.image_path, "Red test", now=100)
        reopened = Catalog(self.root / "catalog.sqlite3", self.root / "gallery")

        self.assertEqual(reopened.list_slides(now=101)[0].id, slide.id)
        self.assertTrue(slide.path.is_file())
        self.assertEqual(slide.title, "Red test")

    def test_external_refresh_updates_existing_slide(self) -> None:
        first = self.catalog.add_image(
            self.image_path, "First", source="api:weather", external_id="today", now=100
        )
        Image.new("RGB", (40, 30), "blue").save(self.image_path)
        updated = self.catalog.add_image(
            self.image_path, "Updated", source="api:weather", external_id="today", now=200
        )

        self.assertEqual(first.id, updated.id)
        self.assertEqual(len(self.catalog.list_slides(now=201)), 1)
        self.assertEqual(updated.title, "Updated")
        with Image.open(updated.path) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))

    def test_expired_slide_is_not_eligible(self) -> None:
        slide = self.catalog.add_image(self.image_path, expires_at=150, now=100)

        self.assertEqual([item.id for item in self.catalog.list_slides(now=149)], [slide.id])
        self.assertEqual(self.catalog.list_slides(now=150), [])
        self.assertIsNone(self.catalog.get_slide(slide.id, now=150))

    def test_carousel_selection_and_deadline_survive_restart(self) -> None:
        first = self.catalog.add_image(self.image_path, now=100)
        second = self.catalog.add_image(self.image_path, now=101)
        self.catalog.initialize_carousel(now=200, interval_seconds=1800)
        selected = self.catalog.advance(1, now=201, interval_seconds=1800)

        self.assertEqual(selected.id, second.id)
        reopened = Catalog(self.root / "catalog.sqlite3", self.root / "gallery")
        resumed = reopened.initialize_carousel(now=10_000, interval_seconds=1800)

        self.assertEqual(resumed.id, second.id)
        self.assertEqual(reopened.get_state("next_transition_at"), "11800")
        next_slide = reopened.advance_if_due(now=11_800, interval_seconds=1800)
        self.assertEqual(next_slide.id, first.id)

    def test_invalid_image_is_rejected(self) -> None:
        invalid = self.root / "not-image.png"
        invalid.write_text("not an image", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.catalog.add_image(invalid)

    def test_replace_preserves_identity_and_replaces_gallery_file(self) -> None:
        original = self.catalog.add_image(self.image_path, "Original", now=100)
        replacement = self.root / "replacement.bmp"
        Image.new("RGB", (40, 30), "blue").save(replacement)

        updated = self.catalog.replace_image(original.id, replacement)

        self.assertEqual((updated.id, updated.title, updated.created_at),
                         (original.id, "Original", 100))
        self.assertEqual(len(self.catalog.list_slides(now=101)), 1)
        self.assertFalse(original.path.exists())
        with Image.open(updated.path) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))

    def test_invalid_replacements_and_api_operations_leave_existing_slide_untouched(self) -> None:
        local = self.catalog.add_image(self.image_path, now=100)
        api = self.catalog.add_image(
            self.image_path, source="api:weather", external_id="today", now=100
        )
        invalid = self.root / "invalid.png"
        invalid.write_text("invalid", encoding="utf-8")
        for slide_id, path in ((local.id, invalid), (api.id, self.image_path),
                               ("../bad-id", self.image_path),
                               ("00000000-0000-0000-0000-000000000000", self.image_path)):
            with self.subTest(slide_id=slide_id), self.assertRaises(ValueError):
                self.catalog.replace_image(slide_id, path)
        with self.assertRaises(ValueError):
            self.catalog.delete_slide(api.id)
        with self.assertRaises(ValueError):
            self.catalog.delete_slide("missing")
        self.assertTrue(local.path.exists())
        self.assertTrue(api.path.exists())
        self.assertEqual(len(self.catalog.list_slides(now=101)), 2)

    def test_delete_removes_only_selected_gallery_entry(self) -> None:
        first = self.catalog.add_image(self.image_path, now=100)
        second = self.catalog.add_image(self.image_path, now=101)
        self.catalog.delete_slide(first.id)
        self.assertFalse(first.path.exists())
        self.assertTrue(second.path.exists())
        self.assertEqual([slide.id for slide in self.catalog.list_slides(now=102)], [second.id])


if __name__ == "__main__":
    unittest.main()
