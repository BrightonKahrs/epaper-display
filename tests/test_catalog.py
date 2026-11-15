import sqlite3
import tempfile
import unittest
from contextlib import closing
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

    def test_queue_projection_matches_folder_scheduler_without_writing_state(self) -> None:
        for index, folder in enumerate((None, None, "space", "space/planets/moons", "travel")):
            self.catalog.add_image(self.image_path, str(index), folder=folder, now=100 + index)
        disabled = self.catalog.add_image(self.image_path, folder="space", now=106)
        self.catalog.set_enabled(disabled.id, False)
        self.catalog.add_image(self.image_path, folder="travel", expires_at=150, now=107)
        for scope in (None, "space", "space/planets"):
            with self.subTest(scope=scope):
                self.catalog.set_scope(scope, now=200, interval_seconds=1800)
                self.catalog.set_paused(True, now=200, interval_seconds=1800)
                with closing(sqlite3.connect(self.catalog.database_path)) as connection:
                    before = list(connection.iterdump())
                projected = self.catalog.snapshot(now=201, queue_limit=12)
                self.assertEqual(projected, self.catalog.snapshot(now=201, queue_limit=12))
                with closing(sqlite3.connect(self.catalog.database_path)) as connection:
                    self.assertEqual(before, list(connection.iterdump()))
                actual = [
                    self.catalog.advance(1, now=201, interval_seconds=1800).id
                    for _ in range(12)
                ]
                self.assertEqual([slide.id for slide in projected.upcoming], actual)
                self.assertNotIn(disabled.id, actual)
                self.assertEqual(len(projected.slides), 7)
                self.assertIn("space/planets", projected.folders)

    def test_projection_handles_uninitialized_selection_and_empty_scope(self) -> None:
        self.assertEqual(self.catalog.snapshot(now=100).upcoming, [])
        first = self.catalog.add_image(self.image_path, folder="space/moons", now=100)
        self.catalog.add_image(self.image_path, folder="space", now=101)
        projected = self.catalog.snapshot(now=102)
        self.assertIsNone(self.catalog.get_state("current_slide_id"))
        self.assertEqual(
            projected.upcoming[0].id,
            self.catalog.initialize_carousel(now=102, interval_seconds=1800).id,
        )
        self.catalog.set_scope("space/moons", now=103, interval_seconds=1800)
        self.catalog.set_enabled(first.id, False)
        self.assertEqual(self.catalog.snapshot(now=104).upcoming, [])
        with self.assertRaisesRegex(ValueError, "queue_limit"):
            self.catalog.snapshot(queue_limit=-1)

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

    def test_slide_enabled_state_survives_refresh_replacement_and_restart(self) -> None:
        local = self.catalog.add_image(self.image_path, "Local", now=100)
        api = self.catalog.add_image(
            self.image_path, "API", source="api:weather", external_id="today", now=100
        )
        self.catalog.set_enabled(local.id, False)
        self.catalog.set_enabled(api.id, False)
        self.assertFalse(self.catalog.replace_image(local.id, self.image_path).enabled)
        refreshed = self.catalog.add_image(
            self.image_path, "Updated", source="api:weather", external_id="today", now=200
        )
        self.assertFalse(refreshed.enabled)
        reopened = Catalog(self.root / "catalog.sqlite3", self.root / "gallery")
        self.assertEqual(reopened.list_slides(now=201), [])
        self.assertCountEqual(
            [(slide.id, slide.enabled) for slide in reopened.list_slides(now=201, include_all=True)],
            [(local.id, False), (api.id, False)],
        )
        self.assertTrue(reopened.set_enabled(local.id, True).enabled)
        self.assertEqual([slide.id for slide in reopened.list_slides(now=201)], [local.id])
        self.assertTrue(local.path.exists())
        with self.assertRaisesRegex(ValueError, "Slide not found"):
            reopened.set_enabled("missing", True)

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

    def test_pause_survives_restart_and_manual_controls_until_resume(self) -> None:
        first = self.catalog.add_image(self.image_path, now=100)
        second = self.catalog.add_image(self.image_path, now=101)
        self.catalog.initialize_carousel(now=200, interval_seconds=1800)
        self.catalog.set_paused(True, now=201, interval_seconds=1800)
        reopened = Catalog(self.root / "catalog.sqlite3", self.root / "gallery")
        self.assertEqual(reopened.get_state("rotation_paused"), "1")
        self.assertIsNone(reopened.advance_if_due(now=5000, interval_seconds=1800))
        self.assertEqual(reopened.get_state("current_slide_id"), first.id)
        self.assertEqual(reopened.get_state("next_transition_at"), "")
        self.assertEqual(
            reopened.initialize_carousel(now=5001, interval_seconds=1800).id, first.id
        )
        self.assertEqual(reopened.advance(1, now=5002, interval_seconds=1800).id, second.id)
        reopened.select_slide(first.id, now=5003, interval_seconds=1800)
        self.assertEqual(reopened.get_state("next_transition_at"), "")
        reopened.set_paused(False, now=6000, interval_seconds=1800)
        self.assertIsNone(reopened.advance_if_due(now=7799, interval_seconds=1800))
        self.assertEqual(
            reopened.advance_if_due(now=7800, interval_seconds=1800).id, second.id
        )

    def test_pause_empty_gallery_applies_when_first_slide_arrives(self) -> None:
        self.catalog.set_paused(True, now=100, interval_seconds=1800)
        slide = self.catalog.add_image(self.image_path, now=101)
        self.catalog.initialize_carousel(now=102, interval_seconds=1800)
        self.assertEqual(self.catalog.get_state("current_slide_id"), slide.id)
        self.assertEqual(self.catalog.get_state("next_transition_at"), "")
        self.assertIsNone(self.catalog.advance_if_due(now=5000, interval_seconds=1800))

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

    def test_loose_slides_each_get_a_turn_and_folders_cycle_through_all_descendants(self) -> None:
        loose1 = self.catalog.add_image(self.image_path, "Loose 1", now=100)
        space1 = self.catalog.add_image(self.image_path, "Space 1", folder="space", now=101)
        loose2 = self.catalog.add_image(self.image_path, "Loose 2", now=102)
        space2 = self.catalog.add_image(
            self.image_path, "Space 2", folder="space/planets/gas/giants", now=103
        )
        self.assertEqual(
            self.catalog.initialize_carousel(now=200, interval_seconds=10).id, loose1.id
        )
        sequence = [
            self.catalog.advance_if_due(now=210 + index * 10, interval_seconds=10).id
            for index in range(8)
        ]
        self.assertEqual(
            sequence,
            [loose2.id, space1.id, loose1.id, loose2.id, space2.id, loose1.id, loose2.id, space1.id],
        )

    def test_folder_turns_are_equal_and_resume_their_own_cursor_after_restart(self) -> None:
        space = [
            self.catalog.add_image(self.image_path, folder=folder, now=100 + index)
            for index, folder in enumerate(("space", "space/planets", "space/planets/moons"))
        ]
        travel = self.catalog.add_image(self.image_path, folder="travel/photos", now=104)
        self.assertEqual(self.catalog.initialize_carousel(200, 10).id, space[0].id)
        self.assertEqual(self.catalog.advance(1, 201, 10).id, travel.id)
        reopened = Catalog(self.catalog.database_path, self.catalog.gallery_dir)
        self.assertEqual(reopened.initialize_carousel(202, 10).id, travel.id)
        self.assertEqual(
            [reopened.advance(1, 203 + index, 10).id for index in range(6)],
            [space[1].id, travel.id, space[2].id, travel.id, space[0].id, travel.id],
        )
        reopened.select_slide(space[1].id, 300, 10)
        self.assertEqual(reopened.advance(1, 301, 10).id, travel.id)
        self.assertEqual(reopened.advance(1, 302, 10).id, space[2].id)

    def test_scope_applies_the_same_grouping_at_any_depth_and_persists(self) -> None:
        outside = self.catalog.add_image(self.image_path, now=100)
        parent = "space/planets"
        loose1 = self.catalog.add_image(self.image_path, folder=parent, now=101)
        loose2 = self.catalog.add_image(self.image_path, folder=parent, now=102)
        gas1 = self.catalog.add_image(self.image_path, folder=f"{parent}/gas", now=103)
        gas2 = self.catalog.add_image(self.image_path, folder=f"{parent}/gas/moons/deep", now=104)
        rocky = self.catalog.add_image(self.image_path, folder=f"{parent}/rocky", now=105)
        self.catalog.add_image(self.image_path, folder="space/planets-other", now=106)
        self.assertEqual(self.catalog.set_scope(parent, 200, 10).id, loose1.id)
        reopened = Catalog(self.catalog.database_path, self.catalog.gallery_dir)
        self.assertEqual(reopened.get_scope(), parent)
        self.assertIsNone(reopened.get_slide(outside.id, 201))
        with self.assertRaisesRegex(ValueError, "outside the current scope"):
            reopened.select_slide(outside.id, 201, 10)
        self.assertEqual(
            [reopened.advance(1, 201 + index, 10).id for index in range(7)],
            [loose2.id, gas1.id, rocky.id, loose1.id, loose2.id, gas2.id, rocky.id],
        )
        self.assertCountEqual(
            [slide.id for slide in reopened.list_slides(now=210, scoped=True)],
            [loose1.id, loose2.id, gas1.id, gas2.id, rocky.id],
        )
        reopened.set_scope(f"{parent}/gas", 300, 10)
        self.assertEqual(
            [reopened.advance(1, 301 + index, 10).id for index in range(4)],
            [gas2.id, gas1.id, gas2.id, gas1.id],
        )

    def test_leaf_scope_cycles_slides_without_folders_and_previous_works(self) -> None:
        first = self.catalog.add_image(self.image_path, folder="space", now=100)
        second = self.catalog.add_image(self.image_path, folder="space", now=101)
        self.catalog.set_scope("space", 200, 10)
        self.assertEqual(self.catalog.advance(-1, 201, 10).id, second.id)
        self.assertEqual(self.catalog.advance(1, 202, 10).id, first.id)
        self.assertEqual(self.catalog.advance(1, 203, 10).id, second.id)
        self.catalog.set_scope(None, 204, 10)
        self.assertIsNone(self.catalog.get_scope())
        self.assertEqual(self.catalog.advance(1, 205, 10).id, first.id)

    def test_empty_scope_stays_empty_and_recovers_without_leaking_other_slides(self) -> None:
        self.catalog.add_image(self.image_path, "Outside", now=100)
        inside = self.catalog.add_image(self.image_path, folder="space", now=101)
        self.catalog.set_scope("space", 200, 10)
        self.catalog.set_enabled(inside.id, False)
        self.assertIsNone(self.catalog.advance_if_due(210, 10))
        self.assertIsNone(self.catalog.initialize_carousel(211, 10))
        self.assertEqual(self.catalog.get_scope(), "space")
        with self.assertRaisesRegex(ValueError, "scope has no active slides"):
            self.catalog.advance(1, 212, 10)
        self.catalog.move_slide(inside.id, None)
        self.assertIsNone(self.catalog.set_scope("space", 213, 10))
        arrival = self.catalog.add_image(self.image_path, folder="space/new/deep", now=214)
        self.assertEqual(self.catalog.initialize_carousel(215, 10).id, arrival.id)

    def test_disabled_expired_deleted_and_moved_slides_do_not_get_folder_turns(self) -> None:
        loose = self.catalog.add_image(self.image_path, now=100)
        expired = self.catalog.add_image(
            self.image_path, folder="expired", expires_at=150, now=101
        )
        disabled = self.catalog.add_image(self.image_path, folder="disabled", now=102)
        first = self.catalog.add_image(self.image_path, folder="space/first", now=103)
        second = self.catalog.add_image(self.image_path, folder="space/second", now=104)
        self.catalog.set_enabled(disabled.id, False)
        self.catalog.initialize_carousel(200, 10)
        self.assertEqual(self.catalog.advance(1, 201, 10).id, first.id)
        self.catalog.delete_slide(first.id)
        self.assertEqual(self.catalog.advance(1, 202, 10).id, loose.id)
        self.assertEqual(self.catalog.advance(1, 203, 10).id, second.id)
        self.catalog.set_scope("space", 204, 10)
        self.catalog.move_slide(second.id, "travel")
        self.assertIsNone(self.catalog.get_slide(second.id, 205))
        self.assertIsNone(self.catalog.initialize_carousel(205, 10))
        self.assertIsNone(self.catalog.get_slide(expired.id, 205))
        self.catalog.set_scope(None, 206, 10)
        self.assertEqual(self.catalog.advance(1, 207, 10).id, second.id)

    def test_logical_folder_moves_preserve_files_identity_and_api_refresh_membership(self) -> None:
        local = self.catalog.add_image(self.image_path, "Local", folder="space/planets", now=100)
        api = self.catalog.add_image(
            self.image_path, source="api:weather", external_id="today", now=101
        )
        original_bytes = local.path.read_bytes()
        moved = self.catalog.move_slide(local.id, "art/photos")
        self.assertEqual((moved.path, moved.created_at, moved.title),
                         (local.path, local.created_at, local.title))
        self.assertEqual(moved.path.read_bytes(), original_bytes)
        self.assertEqual(self.catalog.replace_image(local.id, self.image_path).folder, "art/photos")
        self.catalog.move_slide(api.id, "weather/today")
        refreshed = self.catalog.add_image(
            self.image_path, source="api:weather", external_id="today", now=200
        )
        self.assertEqual(refreshed.folder, "weather/today")
        self.assertIsNone(self.catalog.move_slide(local.id, None).folder)
        self.assertEqual(self.catalog.list_folders(), ["weather", "weather/today"])
        with self.assertRaisesRegex(ValueError, "Slide not found"):
            self.catalog.move_slide("missing", "space")

    def test_folder_paths_are_normalized_and_scope_is_segment_aware(self) -> None:
        slide = self.catalog.add_image(
            self.image_path, folder=" /Space_100%\\Planets/ ", now=100
        )
        self.assertEqual(slide.folder, "Space_100%/Planets")
        self.catalog.add_image(self.image_path, folder="Space_100%/Planets-extra", now=101)
        self.catalog.set_scope("Space_100%/Planets", 200, 10)
        self.assertEqual([s.id for s in self.catalog.list_slides(201, scoped=True)], [slide.id])
        for folder in ("space/../art", "./space", "space//art", "space/\nart", "space/ /art"):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                self.catalog.move_slide(slide.id, folder)
        with self.assertRaisesRegex(ValueError, "Folder not found"):
            self.catalog.set_scope("missing", 201, 10)
        self.assertEqual(self.catalog.get_scope(), "Space_100%/Planets")

    def test_existing_database_migrates_slides_to_root_without_losing_state(self) -> None:
        path = self.root / "legacy.sqlite3"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE slides (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL,
                    source TEXT NOT NULL, external_id TEXT, created_at REAL NOT NULL,
                    expires_at REAL, enabled INTEGER NOT NULL DEFAULT 1
                );
                INSERT INTO slides VALUES ('old', 'Original', 'source.png', 'ssh', NULL, 100, NULL, 1);
                CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO state VALUES ('current_slide_id', 'old');
                INSERT INTO state VALUES ('rotation_paused', '1');
                """
            )
        migrated = Catalog(path, self.catalog.gallery_dir)
        slide = migrated.initialize_carousel(200, 10)
        self.assertEqual((slide.id, slide.folder, slide.title), ("old", None, "Original"))
        self.assertEqual(migrated.get_state("rotation_paused"), "1")
        self.assertEqual(migrated.get_state("next_transition_at"), "")
        self.assertIsNone(migrated.get_scope())
        self.assertEqual(migrated.move_slide("old", "space").folder, "space")


if __name__ == "__main__":
    unittest.main()
