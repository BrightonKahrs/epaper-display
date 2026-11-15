from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from epaper_display.catalog import Catalog
from epaper_display.config import AppConfig
from epaper_display.source_registry import ApiSourceRegistry

HAS_TUI = importlib.util.find_spec("textual") is not None
if HAS_TUI:
    from textual.screen import ModalScreen
    from textual.widgets import Button, DataTable, Input, Static, Tree
    from epaper_display.dashboard import Dashboard


@unittest.skipUnless(HAS_TUI, "Install the tui extra to exercise dashboard interactions")
class DashboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = AppConfig(self.root, interval_seconds=1800)
        self.catalog = Catalog(self.root / "catalog.sqlite3", self.root / "gallery")
        self.sources = ApiSourceRegistry(self.root / "sources.sqlite3")
        self.image = self.root / "image.png"
        Image.new("RGB", (20, 20), "red").save(self.image)
        self.first = self.catalog.add_image(self.image, "City", now=100)
        self.second = self.catalog.add_image(self.image, "Jupiter", folder="space/planets", now=101)
        self.api = self.catalog.add_image(
            self.image, "Weather", source="api:weather", external_id="today", folder="weather", now=102,
        )
        self.catalog.initialize_carousel(time.time(), 1800)

    def dashboard(self) -> Dashboard:
        return Dashboard(self.config, self.catalog, self.sources)

    async def test_add_button_imports_to_entered_folder_without_changing_playback(self) -> None:
        app = self.dashboard()
        before = self.catalog.snapshot().state
        async with app.run_test(size=(80, 24)) as pilot:
            for button in app.query("#controls Button"):
                self.assertLessEqual(button.region.right, 80)
            await pilot.click("#add")
            app.screen.query_one("#add-image", Input).value = str(self.image)
            app.screen.query_one("#add-folder", Input).value = "space/new/moons"
            await pilot.click("#add-confirm")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, ModalScreen)
            slides = self.catalog.list_slides()
            self.assertEqual(len(slides), 4)
            self.assertEqual(slides[-1].folder, "space/new/moons")
            self.assertIn("space/new", self.catalog.list_folders())
            self.assertEqual(self.catalog.snapshot().state, before)

    async def test_add_keyboard_defaults_to_browsed_folder_and_allows_unfiled(self) -> None:
        app = self.dashboard()
        async with app.run_test(size=(80, 24)) as pilot:
            tree = app.query_one("#folders", Tree)
            tree.select_node(next(node for node in tree.root.children if node.data == "space"))
            await pilot.pause()
            app.query_one("#slides", DataTable).focus()
            await pilot.press("a")
            self.assertEqual(app.screen.query_one("#add-folder", Input).value, "space")
            app.screen.query_one("#add-image", Input).value = str(self.image)
            app.screen.query_one("#add-folder", Input).value = ""
            await pilot.press("enter")
            self.assertEqual(len(self.catalog.list_slides()), 4)
            self.assertIsNone(self.catalog.list_slides()[-1].folder)

    async def test_add_in_empty_gallery_errors_keep_inputs_and_cancel_does_not_import(self) -> None:
        self.catalog.delete_slide(self.first.id)
        self.catalog.delete_slide(self.second.id)
        self.catalog.set_enabled(self.api.id, False)
        app = self.dashboard()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.click("#add")
            await pilot.press("enter")
            self.assertIn("Enter an image path", str(app.screen.query_one("#add-error", Static).render()))
            app.screen.query_one("#add-image", Input).value = str(self.root / "missing.png")
            await pilot.press("enter")
            self.assertIsInstance(app.screen, ModalScreen)
            self.assertIn("Could not add slide", str(app.screen.query_one("#add-error", Static).render()))
            app.screen.query_one("#add-image", Input).value = str(self.image)
            app.screen.query_one("#add-folder", Input).value = "../invalid"
            await pilot.press("enter")
            self.assertIsInstance(app.screen, ModalScreen)
            self.assertEqual(app.screen.query_one("#add-image", Input).value, str(self.image))
            await pilot.press("escape")
            self.assertEqual(self.catalog.list_slides(), [])
            await pilot.click("#add")
            app.screen.query_one("#add-image", Input).value = str(self.image)
            await pilot.click("#add-confirm")
            self.assertEqual(len(self.catalog.list_slides()), 1)

    async def test_mouse_browsing_does_not_select_until_explicit_display_and_keyboard_controls_work(self) -> None:
        app = self.dashboard()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.click("#slides", offset=(2, 3))
            self.assertEqual(app._selected_id, self.second.id)
            self.assertEqual(self.catalog.get_state("current_slide_id"), self.first.id)
            await pilot.press("enter")
            self.assertEqual(self.catalog.get_state("current_slide_id"), self.second.id)
            self.assertIsNone(self.catalog.get_state("displayed_slide_id"))
            await pilot.press("space")
            self.assertEqual(self.catalog.get_state("rotation_paused"), "1")
            await pilot.press("p")
            self.assertEqual(self.catalog.get_state("current_slide_id"), self.first.id)
            await pilot.press("n")
            self.assertEqual(self.catalog.get_state("current_slide_id"), self.second.id)
            await pilot.click("#pause")
            self.assertEqual(self.catalog.get_state("rotation_paused"), "0")

    async def test_double_click_displays_clicked_slide_and_display_button_works(self) -> None:
        app = self.dashboard()
        async with app.run_test(size=(80, 24)) as pilot:
            self.assertEqual(str(app.query_one("#display", Button).label), "Display")
            await pilot.click("#slides", offset=(2, 3), times=2)
            self.assertEqual(self.catalog.get_state("current_slide_id"), self.second.id)
            self.assertIsNone(self.catalog.get_state("displayed_slide_id"))
            await pilot.click("#slides", offset=(2, 2))
            self.assertEqual(self.catalog.get_state("current_slide_id"), self.second.id)
            await pilot.click("#display")
            self.assertEqual(self.catalog.get_state("current_slide_id"), self.first.id)

    async def test_double_click_header_and_empty_area_does_not_display_highlighted_slide(self) -> None:
        app = self.dashboard()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.click("#slides", offset=(2, 3))
            await pilot.click("#slides", offset=(2, 1), times=2)
            await pilot.click("#slides", offset=(2, 7), times=2)
            self.assertEqual(self.catalog.get_state("current_slide_id"), self.first.id)

    async def test_double_click_respects_disabled_expired_and_scope_eligibility(self) -> None:
        self.catalog.set_enabled(self.second.id, False)
        self.catalog.add_image(self.image, "Expired", expires_at=150, now=103)
        self.catalog.set_scope("weather", time.time(), 1800)
        app = self.dashboard()
        async with app.run_test(size=(120, 36)) as pilot:
            with patch.object(app, "notify", wraps=app.notify) as notify:
                for row in (2, 3, 5):
                    await pilot.click("#slides", offset=(2, row), times=2)
                    self.assertEqual(self.catalog.get_state("current_slide_id"), self.api.id)
                errors = [call for call in notify.call_args_list if call.kwargs.get("title") == "Action failed"]
                self.assertEqual(len(errors), 3)

    async def test_folder_browsing_search_and_scope_are_independent(self) -> None:
        app = self.dashboard()
        async with app.run_test(size=(120, 36)) as pilot:
            tree = app.query_one("#folders", Tree)
            tree.focus()
            await pilot.press("home", "down", "enter")
            self.assertEqual(app._browse_folder, "space")
            self.assertIsNone(self.catalog.get_scope())
            self.assertEqual([slide.id for slide in app._visible_slides], [self.second.id])
            await pilot.click("#apply-scope")
            self.assertEqual(self.catalog.get_scope(), "space")
            tree.select_node(tree.root)
            await pilot.pause()
            self.assertEqual(self.catalog.get_scope(), "space")
            app.query_one("#slides", DataTable).focus()
            await pilot.press("slash", *"weather")
            self.assertEqual([slide.id for slide in app._visible_slides], [self.api.id])
            self.assertTrue(app.query_one("#display", Button).disabled)
            self.assertEqual(self.catalog.get_scope(), "space")
            await pilot.press("escape", "s")
            self.assertIsNone(self.catalog.get_scope())
            await pilot.press("enter")
            self.assertEqual(self.catalog.get_state("current_slide_id"), self.api.id)

    async def test_move_enable_and_cancel_are_safe_in_a_small_terminal(self) -> None:
        app = self.dashboard()
        async with app.run_test(size=(80, 24)) as pilot:
            for name in ("display", "slides", "apply-scope", "move", "delete"):
                region = app.query_one(f"#{name}").region
                self.assertGreater(region.height, 0)
                self.assertLessEqual(region.bottom, 24)
                self.assertLessEqual(region.right, 80)
            self.assertGreaterEqual(app.query_one("#slides", DataTable).region.height, 7)
            for button in app.query("#controls Button"):
                self.assertGreaterEqual(button.content_size.width, len(str(button.label)))
            await pilot.click("#move")
            app.screen.query_one("#dialog-input", Input).value = "space/new/moons"
            await pilot.press("enter")
            self.assertEqual(self.catalog.get_slide(self.first.id).folder, "space/new/moons")
            self.assertIn("space/new", self.catalog.list_folders())
            await pilot.click("#move")
            app.screen.query_one("#dialog-input", Input).value = "not-applied"
            await pilot.press("escape")
            self.assertEqual(self.catalog.get_slide(self.first.id).folder, "space/new/moons")
            app.query_one("#slides", DataTable).focus()
            await pilot.press("e")
            self.assertIsNone(self.catalog.get_slide(self.first.id))
            await pilot.press("e")
            self.assertIsNotNone(self.catalog.get_slide(self.first.id))

    async def test_browsing_a_folder_with_no_search_matches_clears_stale_rows(self) -> None:
        app = self.dashboard()
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one("#search", Input).value = "Jupiter"
            await pilot.pause()
            self.assertEqual(app.query_one("#slides", DataTable).row_count, 1)
            tree = app.query_one("#folders", Tree)
            tree.select_node(next(node for node in tree.root.children if node.data == "weather"))
            await pilot.pause()
            self.assertEqual(app.query_one("#slides", DataTable).row_count, 0)
            self.assertIsNone(app._selected())
            self.assertTrue(app.query_one("#display", Button).disabled)
            app.query_one("#search", Input).value = ""
            await pilot.pause()
            self.assertEqual(app._selected_id, self.api.id)

    async def test_replacement_delete_confirmation_and_api_restrictions(self) -> None:
        replacement = self.root / "replacement.png"
        Image.new("RGB", (20, 20), "blue").save(replacement)
        app = self.dashboard()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.click("#replace")
            app.screen.query_one("#dialog-input", Input).value = str(replacement)
            await pilot.press("enter")
            with Image.open(self.first.path) as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))
            await pilot.click("#delete")
            await pilot.press("escape")
            self.assertIsNotNone(self.catalog.get_slide(self.first.id))
            await pilot.click("#delete")
            await pilot.click("#delete-confirm")
            self.assertIsNone(self.catalog.get_slide(self.first.id))
            self.assertFalse(self.first.path.exists())
            app.query_one("#search", Input).value = "Weather"
            await pilot.pause()
            self.assertEqual(app._selected_id, self.api.id)
            self.assertTrue(app.query_one("#replace", Button).disabled)
            self.assertTrue(app.query_one("#delete", Button).disabled)
            app.query_one("#slides", DataTable).focus()
            await pilot.press("r", "delete")
            self.assertNotIsInstance(app.screen, ModalScreen)
            self.assertIsNotNone(self.catalog.get_slide(self.api.id))

    async def test_failed_actions_and_read_errors_are_visible_and_recoverable(self) -> None:
        app = self.dashboard()
        async with app.run_test(size=(120, 36)) as pilot:
            with patch.object(app, "notify", wraps=app.notify) as notify:
                await pilot.click("#replace")
                app.screen.query_one("#dialog-input", Input).value = str(self.root / "missing.png")
                await pilot.press("enter")
                self.assertTrue(any(call.kwargs.get("title") == "Action failed" for call in notify.call_args_list))
                self.assertTrue(self.first.path.exists())
                with patch.object(self.catalog, "snapshot", side_effect=sqlite3.OperationalError("locked")):
                    app.refresh_data()
                self.assertIn("Could not refresh dashboard", str(app.query_one("#playback", Static).render()))
                app.refresh_data()
                self.assertIsNone(app._read_error)

    async def test_reported_service_state_and_polling_do_not_mutate_playback_on_exit(self) -> None:
        self.catalog.update_runtime_state({
            "service_status": "running", "service_heartbeat_at": str(time.time()),
            "displayed_slide_id": self.first.id, "displayed_slide_title": "City",
            "rendering_slide_id": self.second.id,
        })
        before = self.catalog.snapshot().state
        app = self.dashboard()
        async with app.run_test(size=(120, 36)) as pilot:
            status = str(app.query_one("#playback", Static).render())
            self.assertIn("Running", status)
            self.assertIn("Last displayed: City", status)
            self.assertIn("Refreshing: Jupiter", status)
            app.query_one("#slides", DataTable).move_cursor(row=1)
            await pilot.pause(1.1)
            self.assertEqual(app._selected_id, self.second.id)
            self.assertEqual(before, self.catalog.snapshot().state)
            await pilot.press("q")
        self.assertEqual(before, self.catalog.snapshot().state)

    async def test_empty_stale_stopped_and_failed_preview_states(self) -> None:
        app = self.dashboard()
        async with app.run_test(size=(80, 24)) as pilot:
            self.assertIn("Unknown", str(app.query_one("#playback", Static).render()))
            self.catalog.update_runtime_state({
                "service_status": "running", "service_heartbeat_at": str(time.time() - 91),
                "displayed_slide_title": "Saved image", "display_target": "preview",
                "display_error": "driver failed",
            })
            app.refresh_data()
            status = str(app.query_one("#playback", Static).render())
            self.assertIn("No recent heartbeat", status)
            self.assertIn("Preview saved: Saved image", status)
            self.assertIn("Refresh failed: driver failed", status)
            self.catalog.update_runtime_state({"service_status": "stopped"})
            for slide in (self.first, self.second, self.api):
                self.catalog.set_enabled(slide.id, False)
            app.refresh_data()
            self.assertIn("Stopped", str(app.query_one("#playback", Static).render()))
            self.assertIn("No eligible slides", str(app.query_one("#queue", Static).render()))
            self.assertTrue(app.query_one("#next", Button).disabled)
            self.assertTrue(app.query_one("#display", Button).disabled)


if __name__ == "__main__":
    unittest.main()
