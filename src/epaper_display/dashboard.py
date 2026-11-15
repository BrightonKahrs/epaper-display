from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Select, Static, Tree
from textual.widgets.tree import TreeNode
from rich.text import Text

from epaper_display.catalog import CarouselSnapshot, Catalog, Slide
from epaper_display.config import AppConfig
from epaper_display.source_registry import ApiSourceRegistry


class SlideTable(DataTable):
    class DisplayRequested(Message):
        def __init__(self, slide_id: str) -> None:
            super().__init__()
            self.slide_id = slide_id

    def on_click(self, event: events.Click) -> None:
        if event.button != 1 or event.chain != 2:
            return
        row = event.style.meta.get("row", -1)
        column = event.style.meta.get("column", -1)
        if 0 <= row < self.row_count and column >= 0:
            slide_id = self.ordered_rows[row].key.value
            if slide_id is not None:
                self.post_message(self.DisplayRequested(slide_id))


class ValueDialog(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self, title: str, prompt: str, value: str = "", folders: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.heading, self.prompt, self.value, self.folders = title, prompt, value, folders

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text(self.heading), classes="dialog-title")
            yield Static(Text(self.prompt))
            if self.folders is not None:
                options = [("/", "/")] + [(folder, folder) for folder in self.folders]
                yield Select(options, value=self.value or "/", allow_blank=False, id="folder-options")
            yield Input(
                value=self.value, id="dialog-input",
                placeholder="Folder path or /" if self.folders is not None else "Image path on the Pi",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="dialog-cancel")
                yield Button("Apply", variant="primary", id="dialog-apply")

    def on_mount(self) -> None:
        self.query_one("#dialog-input", Input).focus()

    @on(Select.Changed, "#folder-options")
    def choose_folder(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.query_one("#dialog-input", Input).value = event.value

    @on(Input.Submitted, "#dialog-input")
    def submit(self) -> None:
        self.dismiss(self.query_one("#dialog-input", Input).value)

    @on(Button.Pressed)
    def button(self, event: Button.Pressed) -> None:
        if event.button.id == "dialog-apply":
            self.submit()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class DeleteDialog(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, slide: Slide) -> None:
        super().__init__()
        self.slide = slide

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Delete slide permanently?", classes="dialog-title")
            yield Static(Text(f"{self.slide.title}\n{self.slide.id}\nThe gallery image will be removed."))
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="delete-cancel")
                yield Button("Delete", variant="error", id="delete-confirm")

    def on_mount(self) -> None:
        self.query_one("#delete-cancel", Button).focus()

    @on(Button.Pressed)
    def button(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class Dashboard(App[None]):
    """An SSH control surface; the separate carousel remains the only renderer."""

    TITLE = "E-paper display"
    SUB_TITLE = "Carousel control"
    CSS = """
    Screen { background: $surface; }
    #playback { height: 4; padding: 0 1; background: $panel; }
    #controls { height: 3; padding: 0 1; }
    Button { min-width: 10; width: 10; margin-right: 1; }
    #workspace { height: 1fr; min-height: 7; }
    #folders-pane { width: 28; min-width: 18; padding: 0 1; }
    #folders { height: 1fr; border: solid $border; }
    #apply-scope { width: 100%; margin: 0; }
    #slides-pane { width: 1fr; padding-right: 1; }
    #search { height: 3; }
    #slides { height: 1fr; min-height: 2; border: solid $border; }
    #queue { height: 7; padding: 0 1; border-top: solid $border; }
    #details { height: 2; padding: 0 1; color: $text-muted; }
    #sources { height: 1; padding: 0 1; color: $text-muted; }
    Screen.compact #folders-pane { width: 20; }
    Screen.compact #queue { height: 4; }
    Screen.compact #details { display: none; }
    Screen.compact Button { min-width: 8; width: 8; padding: 0; margin: 0; border: none; }
    Screen.compact Button:focus { text-style: bold underline; }
    ModalScreen { align: center middle; background: $background 80%; }
    .dialog { width: 66; max-width: 95%; height: auto; padding: 1 2; border: thick $accent; background: $surface; }
    .dialog-title { text-style: bold; margin-bottom: 1; }
    .dialog Input, .dialog Select { margin-top: 1; }
    .dialog-buttons { height: 3; margin-top: 1; align-horizontal: right; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+q", "close_dashboard", show=False, priority=True),
        Binding("enter", "display_slide", "Display", priority=True),
        Binding("space", "pause", "Pause/resume"),
        Binding("n", "next_slide", "Next"),
        Binding("p", "previous_slide", "Previous", show=False),
        Binding("s", "scope", "Scope"),
        Binding("m", "move_slide", "Move", show=False),
        Binding("e", "toggle_slide", "Enable/disable", show=False),
        Binding("r", "replace_slide", "Replace", show=False),
        Binding("delete", "delete_slide", "Delete", show=False),
        Binding("slash", "search", "Search"),
        Binding("escape", "browse", "Browse", show=False),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, config: AppConfig, catalog: Catalog, sources: ApiSourceRegistry) -> None:
        super().__init__()
        self.config, self.catalog, self.sources = config, catalog, sources
        self._snapshot = CarouselSnapshot(slides=[], state={}, upcoming=[], folders=[])
        self._browse_folder: str | None = None
        self._selected_id: str | None = None
        self._visible_slides: list[Slide] = []
        self._table_signature: tuple[tuple[str, str, str, str], ...] | None = None
        self._folder_signature: tuple[str, ...] | None = None
        self._source_checked_at = 0.0
        self._read_error: str | None = None
        self.theme = "textual-dark"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Loading carousel state...", id="playback", markup=False)
        with Horizontal(id="controls"):
            yield Button("Display", variant="primary", id="display")
            yield Button("Previous", id="previous")
            yield Button("Next", id="next")
            yield Button("Pause", id="pause")
            yield Button("Move", id="move")
            yield Button("Disable", id="toggle")
            yield Button("Replace", id="replace")
            yield Button("Delete", id="delete", variant="error")
            yield Button("Help", id="help")
        with Horizontal(id="workspace"):
            with Vertical(id="folders-pane"):
                yield Tree("All folders", data=None, id="folders")
                yield Button("Scope folder", id="apply-scope", tooltip="Use the browsed folder for playback")
            with Vertical(id="slides-pane"):
                yield Input(placeholder="Search slides, folders, sources or IDs", id="search")
                yield SlideTable(id="slides", cursor_type="row", zebra_stripes=True)
                yield Static(id="queue", markup=False)
        yield Static(id="details", markup=False)
        yield Static(id="sources", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#slides", DataTable).add_columns("Slide", "Folder", "State")
        self.refresh_data()
        self.query_one("#slides", DataTable).focus()
        self.set_interval(1, self.refresh_data)

    def on_resize(self) -> None:
        self.screen.set_class(self.size.width < 95 or self.size.height < 32, "compact")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self.screen, ModalScreen):
            return False
        if isinstance(self.focused, Input):
            return action in ("browse", "close_dashboard")
        if action == "display_slide":
            return isinstance(self.focused, DataTable)
        return True

    def refresh_data(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        try:
            self._snapshot = self.catalog.snapshot()
            self._refresh_folders()
            self._refresh_table()
            self._refresh_playback()
            self._refresh_details()
            if time.time() - self._source_checked_at >= 5:
                statuses = self.sources.refresh_status()
                enabled = sum(bool(status["enabled"]) for status in statuses.values())
                failures = [name for name, status in statuses.items() if status["last_error"]]
                text = f"API sources: {enabled} enabled"
                if failures:
                    text += " | Refresh errors: " + ", ".join(failures)
                self.query_one("#sources", Static).update(Text(text))
                self._source_checked_at = time.time()
            self._read_error = None
        except (OSError, ValueError, sqlite3.Error) as exc:
            message = f"Could not refresh dashboard: {exc}"
            self.query_one("#playback", Static).update(Text(message))
            if message != self._read_error:
                self.notify(message, severity="error", timeout=10)
            self._read_error = message

    def _refresh_folders(self) -> None:
        paths = self._snapshot.folders
        signature = tuple(paths)
        if signature == self._folder_signature:
            return
        if self._browse_folder not in paths:
            self._browse_folder = None
        tree = self.query_one("#folders", Tree)
        expanded: set[str | None] = set()

        def remember(node: TreeNode[str | None]) -> None:
            if node.is_expanded:
                expanded.add(node.data)
            for child in node.children:
                remember(child)

        remember(tree.root)
        tree.clear()
        nodes = {"": tree.root}
        scope = self._snapshot.state.get("scope", "")
        for path in signature:
            parent, _, name = path.rpartition("/")
            nodes[path] = nodes[parent].add(
                Text(name), data=path,
                expand=path in expanded or scope == path or scope.startswith(path + "/"),
            )
        tree.root.expand()
        self._folder_signature = signature

    def _refresh_table(self) -> None:
        search = self.query_one("#search", Input).value.casefold().strip()
        now = time.time()
        self._visible_slides = [
            slide for slide in self._snapshot.slides
            if Catalog.in_scope(slide.folder, self._browse_folder)
            and search in f"{slide.title} {slide.folder or '/'} {slide.source} {slide.id}".casefold()
        ]
        state = self._snapshot.state
        next_id = self._snapshot.upcoming[0].id if self._snapshot.upcoming else None
        rows = []
        for slide in self._visible_slides:
            flags = []
            if not slide.enabled:
                flags.append("disabled")
            if slide.expires_at is not None and slide.expires_at <= now:
                flags.append("expired")
            if slide.id == state.get("displayed_slide_id"):
                flags.append("displayed")
            if slide.id == state.get("current_slide_id"):
                flags.append("selected")
            if slide.id == next_id:
                flags.append("next")
            rows.append((slide.id, slide.title, slide.folder or "/", ", ".join(flags) or "enabled"))
        signature = tuple(rows)
        if signature != self._table_signature:
            table = self.query_one("#slides", DataTable)
            table.clear()
            for slide_id, title, folder, flags in rows:
                table.add_row(Text(title), Text(folder), Text(flags), key=slide_id)
            target = self._selected_id or state.get("current_slide_id")
            row_index = next((i for i, row in enumerate(rows) if row[0] == target), 0)
            if rows:
                table.move_cursor(row=row_index)
                self._selected_id = rows[row_index][0]
            else:
                self._selected_id = None
            self._table_signature = signature
        table = self.query_one("#slides", DataTable)
        table.border_title = f"Slides ({len(rows)})"
        table.border_subtitle = "No matching slides" if not rows else ""

    def _refresh_playback(self) -> None:
        state = self._snapshot.state
        by_id = {slide.id: slide for slide in self._snapshot.slides}

        def title(slide_id: str | None) -> str:
            slide = by_id.get(slide_id or "")
            return slide.title if slide else slide_id or "None"

        heartbeat = float(state.get("service_heartbeat_at") or 0)
        if state.get("service_status") == "stopped":
            health = "Stopped"
        elif not heartbeat:
            health = "Unknown - start or restart the carousel"
        elif time.time() - heartbeat > 90:
            health = "No recent heartbeat - service may be offline or blocked"
        else:
            health = "Running"
        paused = state.get("rotation_paused") == "1"
        selected = state.get("current_slide_id")
        displayed = state.get("displayed_slide_title") or title(state.get("displayed_slide_id"))
        deadline = float(state.get("next_transition_at") or 0)
        remaining = max(0, int(deadline - time.time()))
        countdown = "Paused" if paused else f"{remaining // 60}m {remaining % 60:02d}s" if deadline else "Not scheduled"
        next_slide = self._snapshot.upcoming[0].title if self._snapshot.upcoming else "No eligible slides"
        target = "Preview saved" if state.get("display_target") == "preview" else "Last displayed"
        lines = [
            f"{health} | Scope: {state.get('scope') or '/'} | Rotation: {'paused' if paused else 'running'}",
            f"{target}: {displayed} | Selected: {title(selected)}",
            f"On deck: {next_slide} | Next transition: {countdown}",
        ]
        if state.get("rendering_slide_id"):
            lines.append(f"Refreshing: {title(state['rendering_slide_id'])}")
        elif state.get("display_error"):
            lines.append("Refresh failed: " + state["display_error"])
        elif not self._snapshot.slides:
            lines.append("Gallery empty. Add an image with epd add <path> in another terminal.")
        else:
            lines.append("Browse freely; Display changes playback. Scope folder changes the active scope.")
        self.query_one("#playback", Static).update(
            Text("\n".join(lines), overflow="ellipsis", no_wrap=True)
        )
        count = 2 if self.screen.has_class("compact") else 5
        queue = ["UPCOMING - projected from current eligibility"]
        queue.extend(
            f"{index}. {slide.title}  [{slide.folder or '/'}]"
            for index, slide in enumerate(self._snapshot.upcoming[:count], 1)
        )
        if not self._snapshot.upcoming:
            queue.append("No eligible slides in this scope.")
        self.query_one("#queue", Static).update(
            Text("\n".join(queue), overflow="ellipsis", no_wrap=True)
        )
        self.query_one("#pause", Button).label = "Resume" if paused else "Pause"
        self.query_one("#next", Button).disabled = not self._snapshot.upcoming
        self.query_one("#previous", Button).disabled = not self._snapshot.upcoming

    def _selected(self) -> Slide | None:
        return next((slide for slide in self._visible_slides if slide.id == self._selected_id), None)

    def _refresh_details(self) -> None:
        slide = self._selected()
        self.query_one("#details", Static).update(
            Text(f"{slide.id} | {slide.source}\n{slide.path}" if slide else "No slide selected.")
        )
        local = slide is not None and slide.source == "ssh"
        for name in ("replace", "delete"):
            self.query_one(f"#{name}", Button).disabled = not local
        for name in ("move", "toggle"):
            self.query_one(f"#{name}", Button).disabled = slide is None
        self.query_one("#toggle", Button).label = "Disable" if slide and slide.enabled else "Enable"
        self.query_one("#display", Button).disabled = not (
            slide and slide.enabled
            and (slide.expires_at is None or slide.expires_at > time.time())
            and Catalog.in_scope(slide.folder, self._snapshot.state.get("scope", ""))
        )

    @on(Tree.NodeSelected, "#folders")
    def browse_folder(self, event: Tree.NodeSelected[str | None]) -> None:
        self._browse_folder = event.node.data
        self._refresh_table()
        self._refresh_details()

    @on(Input.Changed, "#search")
    def filter_slides(self) -> None:
        self._refresh_table()
        self._refresh_details()

    @on(DataTable.RowHighlighted, "#slides")
    def highlight(self, event: DataTable.RowHighlighted) -> None:
        if not any(slide.id == event.row_key.value for slide in self._visible_slides):
            return
        self._selected_id = event.row_key.value
        self._refresh_details()

    @on(SlideTable.DisplayRequested)
    async def display_clicked_slide(self, event: SlideTable.DisplayRequested) -> None:
        self._selected_id = event.slide_id
        await self.action_display_slide()

    @on(Button.Pressed)
    async def button(self, event: Button.Pressed) -> None:
        actions = {
            "display": self.action_display_slide, "next": self.action_next_slide,
            "previous": self.action_previous_slide, "pause": self.action_pause,
            "apply-scope": self.action_scope, "move": self.action_move_slide,
            "toggle": self.action_toggle_slide, "replace": self.action_replace_slide,
            "delete": self.action_delete_slide,
        }
        if event.button.id == "help":
            self.action_help()
        elif event.button.id in actions:
            await actions[event.button.id]()

    async def _perform(self, operation: Callable[[], object], success: str) -> None:
        try:
            await asyncio.to_thread(operation)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.notify(str(exc), title="Action failed", severity="error", timeout=10)
        else:
            self.notify(success, timeout=3)
        self.refresh_data()

    async def action_display_slide(self) -> None:
        slide = self._selected()
        if slide:
            await self._perform(
                lambda: self.catalog.select_slide(slide.id, time.time(), self.config.interval_seconds),
                "Slide selected. The carousel will display it when ready.",
            )

    async def action_next_slide(self) -> None:
        await self._perform(
            lambda: self.catalog.advance(1, time.time(), self.config.interval_seconds), "Next slide selected."
        )

    async def action_previous_slide(self) -> None:
        await self._perform(
            lambda: self.catalog.advance(-1, time.time(), self.config.interval_seconds), "Previous slide selected."
        )

    async def action_pause(self) -> None:
        paused = self._snapshot.state.get("rotation_paused") == "1"
        await self._perform(
            lambda: self.catalog.set_paused(not paused, time.time(), self.config.interval_seconds),
            "Rotation resumed." if paused else "Rotation paused.",
        )

    async def action_scope(self) -> None:
        folder = self._browse_folder
        await self._perform(
            lambda: self.catalog.set_scope(folder, time.time(), self.config.interval_seconds),
            f"Playback scope: {folder or '/'}",
        )

    async def action_toggle_slide(self) -> None:
        slide = self._selected()
        if slide:
            await self._perform(
                lambda: self.catalog.set_enabled(slide.id, not slide.enabled),
                "Slide disabled." if slide.enabled else "Slide enabled.",
            )

    async def action_move_slide(self) -> None:
        slide = self._selected()
        if slide is None:
            return

        async def apply(value: str | None) -> None:
            if value is not None:
                await self._perform(lambda: self.catalog.move_slide(slide.id, value), "Slide moved.")

        folders = sorted({path for path in self._folder_signature or ()})
        self.push_screen(ValueDialog(
            "Move slide", "Choose or enter a folder; / means unfiled. API assignments may override moves.",
            slide.folder or "/", folders,
        ), apply)

    async def action_replace_slide(self) -> None:
        slide = self._selected()
        if slide is None:
            return
        if slide.source != "ssh":
            self.notify("Only local slides can be replaced.", severity="warning")
            return

        async def apply(value: str | None) -> None:
            if value is not None:
                await self._perform(
                    lambda: self.catalog.replace_image(slide.id, Path(value)),
                    "Image replaced. The carousel will refresh it when ready.",
                )

        self.push_screen(ValueDialog("Replace image", "Enter an existing image path on the Pi."), apply)

    async def action_delete_slide(self) -> None:
        slide = self._selected()
        if slide is None:
            return
        if slide.source != "ssh":
            self.notify("Only local slides can be deleted; disable an API slide instead.", severity="warning")
            return

        async def apply(confirmed: bool | None) -> None:
            if confirmed:
                await self._perform(lambda: self.catalog.delete_slide(slide.id), "Slide deleted.")

        self.push_screen(DeleteDialog(slide), apply)

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_browse(self) -> None:
        self.query_one("#slides", DataTable).focus()

    def action_close_dashboard(self) -> None:
        self.exit()

    def action_help(self) -> None:
        self.notify(
            "Click a row or use arrows to browse; Enter/Display or double-click displays it. "
            "Space: pause/resume; n/p: next/previous; /: search; s: scope browsed folder; "
            "m: move; e: enable/disable; r: replace; Delete: delete; Tab: change pane; q: quit. "
            "Upcoming assumes current eligibility. Last displayed means last successful driver refresh. "
            "Closing the dashboard does not stop the carousel.",
            title="Dashboard controls", timeout=20,
        )


def run_dashboard(config: AppConfig, catalog: Catalog, sources: ApiSourceRegistry) -> None:
    Dashboard(config, catalog, sources).run(mouse=True)
