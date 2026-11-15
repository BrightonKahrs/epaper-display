import contextlib
import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from epaper_display.bulk_import import bulk_add
from epaper_display.catalog import Catalog
from epaper_display.cli import main


class BulkImportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog = Catalog(self.root / "state" / "catalog.sqlite3", self.root / "state" / "gallery")
        buffer = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(buffer, format="PNG")
        self.image = buffer.getvalue()
        self.archive = self.root / "upload.zip"

    def make_zip(self, names: list[str]) -> None:
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                archive.writestr(name, self.image)

    def test_zip_preserves_structure_and_includes_root_by_default(self) -> None:
        self.make_zip([
            "epaper-zip/space/planets/venus.png",
            "epaper-zip/space/planets/moons/moon.PNG",
            "epaper-zip/cover.png",
        ])
        slides = list(bulk_add(self.catalog, self.archive, preserve_folders=True))
        self.assertEqual(
            [(slide.title, slide.folder) for slide in slides],
            [("cover", "epaper-zip"), ("moon", "epaper-zip/space/planets/moons"),
             ("venus", "epaper-zip/space/planets")],
        )
        self.assertTrue(self.archive.exists())
        reopened = Catalog(self.catalog.database_path, self.catalog.gallery_dir)
        self.assertEqual(len(reopened.list_slides()), 3)
        for slide in slides:
            with Image.open(slide.path) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))

    def test_ignore_root_maps_example_and_root_image_to_unfiled(self) -> None:
        self.make_zip(["epaper-zip/space/planets/venus.png", "epaper-zip/cover.png"])
        slides = list(bulk_add(self.catalog, self.archive, preserve_folders=True, ignore_root=True))
        self.assertEqual([(slide.title, slide.folder) for slide in slides],
                         [("cover", None), ("venus", "space/planets")])

    def test_directory_import_uses_selected_directory_not_tmp_as_root(self) -> None:
        directory = self.root / "tmp" / "epaper-zip"
        image = directory / "space" / "planets" / "venus.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(self.image)
        for ignore, expected in ((False, "epaper-zip/space/planets"), (True, "space/planets")):
            with self.subTest(ignore_root=ignore):
                slides = list(bulk_add(self.catalog, directory, preserve_folders=True, ignore_root=ignore))
                self.assertEqual(slides[0].folder, expected)
                self.assertTrue(image.exists())

    def test_default_flattens_and_non_images_and_mac_metadata_are_reported(self) -> None:
        self.make_zip(["space/venus.png", "__MACOSX/._venus.png", "space/notes.txt"])
        with self.assertLogs("epaper_display.bulk_import", level="INFO") as logs:
            slides = list(bulk_add(self.catalog, self.archive))
        self.assertEqual(len(slides), 1)
        self.assertIsNone(slides[0].folder)
        self.assertIn("Skipping non-image", " ".join(logs.output))
        self.assertIn("Skipping ZIP metadata", " ".join(logs.output))

    def test_unsafe_zip_paths_and_links_fail_before_any_import(self) -> None:
        for name in ("../escape.png", "/absolute.png", "C:\\escape.png", "a/../escape.png", "a//bad.png"):
            with self.subTest(name=name):
                self.make_zip(["valid.png", name])
                with self.assertRaisesRegex(ValueError, "Unsafe ZIP entry"):
                    list(bulk_add(self.catalog, self.archive))
                self.assertEqual(self.catalog.list_slides(), [])
        entry = zipfile.ZipInfo("link.png")
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr(entry, "outside.png")
        with self.assertRaisesRegex(ValueError, "links and special files"):
            list(bulk_add(self.catalog, self.archive))
        self.assertEqual(self.catalog.list_slides(), [])

    def test_ignore_root_requires_preservation_and_single_enclosing_folder(self) -> None:
        for names in (["venus.png"], ["one/venus.png", "two/moon.png"]):
            self.make_zip(names)
            with self.assertRaisesRegex(ValueError, "common enclosing folder"):
                list(bulk_add(self.catalog, self.archive, preserve_folders=True, ignore_root=True))
        with self.assertRaisesRegex(ValueError, "requires --preserve-folders"):
            list(bulk_add(self.catalog, self.archive, ignore_root=True))
        self.assertEqual(self.catalog.list_slides(), [])

    def test_windows_zip_separators_are_normalized(self) -> None:
        self.make_zip(["epaper-zip\\space\\planets\\venus.png"])
        slide = list(bulk_add(
            self.catalog, self.archive, preserve_folders=True, ignore_root=True,
        ))[0]
        self.assertEqual(slide.folder, "space/planets")
        self.assertEqual(slide.title, "venus")

    def test_directory_links_fail_before_import(self) -> None:
        directory = self.root / "input"
        directory.mkdir()
        (directory / "venus.png").write_bytes(self.image)
        with patch("epaper_display.bulk_import.Path.is_symlink", return_value=True):
            with self.assertRaisesRegex(ValueError, "Directory links"):
                list(bulk_add(self.catalog, directory))
        self.assertEqual(self.catalog.list_slides(), [])

    def test_size_entry_limits_and_invalid_archives_leave_gallery_empty(self) -> None:
        self.make_zip(["root/venus.png", "root/moon.png"])
        for limit, value in (("MAX_IMAGE_BYTES", 1), ("MAX_TOTAL_BYTES", 1), ("MAX_ENTRIES", 1)):
            with self.subTest(limit=limit), patch(f"epaper_display.bulk_import.{limit}", value):
                with self.assertRaisesRegex(ValueError, "Bulk import failed"):
                    list(bulk_add(self.catalog, self.archive))
        self.archive.write_text("not a zip", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "File is not a zip file"):
            list(bulk_add(self.catalog, self.archive))
        self.make_zip(["notes.txt"])
        with self.assertRaisesRegex(ValueError, "No supported images"):
            list(bulk_add(self.catalog, self.archive))
        self.assertEqual(self.catalog.list_slides(), [])

    def test_image_failure_reports_partial_import_and_cleans_temporary_files(self) -> None:
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("a.png", self.image)
            archive.writestr("b.png", "not an image")
        temporary = tempfile.TemporaryDirectory
        extracted: list[Path] = []

        def track_temporary(*args, **kwargs):
            directory = temporary(*args, **kwargs)
            extracted.append(Path(directory.name))
            return directory

        with patch("epaper_display.bulk_import.tempfile.TemporaryDirectory", side_effect=track_temporary):
            with self.assertRaisesRegex(ValueError, "after 1 image\\(s\\) imported"):
                list(bulk_add(self.catalog, self.archive))
        self.assertEqual([slide.title for slide in self.catalog.list_slides()], ["a"])
        self.assertTrue(all(not directory.exists() for directory in extracted))

    def test_cli_bulk_add_flags_print_persistent_folder_assignments(self) -> None:
        self.make_zip(["epaper-zip/space/planets/venus.png"])
        config = self.root / "config.json"
        config.write_text(json.dumps({"data_dir": str(self.root / "state")}), encoding="utf-8")
        for flag, expected in (("--include-root", "epaper-zip/space/planets"), ("--ignore-root", "space/planets")):
            stdout = io.StringIO()
            with (
                patch.dict("os.environ", {"EPAPER_DATA_DIR": str(self.root / "state")}),
                contextlib.redirect_stdout(stdout),
            ):
                result = main(["--config", str(config), "bulk-add", str(self.archive), "--preserve-folders", flag])
            self.assertEqual(result, 0)
            self.assertIn(f"\tvenus\t{expected}", stdout.getvalue())
            self.assertIn("Imported 1 image(s).", stdout.getvalue())
        self.assertEqual(len(self.catalog.list_slides()), 2)


if __name__ == "__main__":
    unittest.main()
