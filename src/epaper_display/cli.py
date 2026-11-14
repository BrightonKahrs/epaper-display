from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import questionary

from epaper_display import __version__
from epaper_display.catalog import Catalog
from epaper_display.config import AppConfig
from epaper_display.display import InkyDisplay, PreviewDisplay
from epaper_display.refresh_worker import refresh_due_sources
from epaper_display.service import CarouselService
from epaper_display.source_registry import ApiSource, ApiSourceRegistry
from epaper_display.telemetry import create_telemetry


def _timestamp(value: str | None) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def _add_slide_target(parser: argparse.ArgumentParser) -> None:
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("slide_id", nargs="?", help="Slide ID.")
    target.add_argument("--pick", action="store_true", help="Choose a slide interactively.")


def _parser() -> argparse.ArgumentParser:
    executable = Path(sys.argv[0]).stem
    parser = argparse.ArgumentParser(
        prog=executable if executable in ("epaper-display", "epd") else "epaper-display"
    )
    parser.add_argument("--config", type=Path, help="Path to the JSON configuration file.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Add one or more local image files to the gallery.")
    add.add_argument("images", nargs="+", type=Path)
    add.add_argument("--folder", help="Logical folder path, such as space/planets (default: root).")

    list_command = commands.add_parser("list", help="List active slides.")
    list_command.add_argument(
        "--all", action="store_true", help="Include disabled and expired slides."
    )
    list_command.add_argument("--scoped", action="store_true", help="Show only the current scope.")
    move = commands.add_parser("move", help="Move a slide to a logical folder without moving its file.")
    _add_slide_target(move)
    destination = move.add_mutually_exclusive_group(required=True)
    destination.add_argument("--folder", help="Destination folder path; created automatically.")
    destination.add_argument("--root", action="store_true", help="Remove the slide's folder assignment.")
    scope = commands.add_parser("scope", help="Show or change the carousel's playback folder.")
    scope_target = scope.add_mutually_exclusive_group()
    scope_target.add_argument("folder", nargs="?", help="Folder and descendants to play.")
    scope_target.add_argument("--pick", action="store_true", help="Choose a folder interactively.")
    scope_target.add_argument("--all", action="store_true", help="Play the whole gallery.")
    replace = commands.add_parser("replace", help="Replace the image for a local slide.")
    _add_slide_target(replace)
    replace.add_argument("image", type=Path)
    delete = commands.add_parser("delete", help="Delete a local slide and its gallery image.")
    _add_slide_target(delete)
    for action in ("enable", "disable"):
        command = commands.add_parser(action, help=f"{action.capitalize()} a slide.")
        _add_slide_target(command)
    display = commands.add_parser("display", help="Select a slide for display.")
    _add_slide_target(display)
    commands.add_parser("pick", help="Compatibility alias for 'display --pick'.")
    select = commands.add_parser("show", help="Compatibility alias for 'display <slide-id>'.")
    select.add_argument("slide_id")
    commands.add_parser("next", help="Advance to the next slide.")
    commands.add_parser("previous", help="Select the previous slide.")
    commands.add_parser("status", help="Show carousel and API source status.")
    commands.add_parser("pause", help="Pause automatic slide rotation.")
    commands.add_parser("resume", help="Resume automatic rotation with a fresh interval.")

    run = commands.add_parser("run", help="Run the carousel and API refresh worker.")
    display_options = run.add_mutually_exclusive_group()
    display_options.add_argument(
        "--preview",
        type=Path,
        help="Save rendered output to a PNG instead of using the Inky hardware.",
    )
    display_options.add_argument(
        "--fast",
        action="store_true",
        help="Use experimental Spectra 6 fast refresh (colour shifts; long-term effects unknown).",
    )
    refresh = commands.add_parser(
        "refresh", help="Refresh configured API sources if their TTL expired."
    )
    refresh.add_argument("--force", action="store_true", help="Refresh all enabled sources now.")

    sources = commands.add_parser("sources", help="Manage persisted API slide sources.")
    source_commands = sources.add_subparsers(dest="source_command", required=True)
    source_commands.add_parser("list", help="List configured API sources.")
    configure = source_commands.add_parser(
        "configure", help="Create or replace an API source specification."
    )
    configure.add_argument("source_id")
    configure.add_argument("url")
    configure.add_argument(
        "--type", dest="source_type", choices=("json", "image"), default="json",
        help="Source response format (default: json). Use image for a direct image URL.",
    )
    configure.add_argument("--ttl-seconds", type=int, default=3600)
    configure.add_argument("--query-param", action="append", default=[], metavar="KEY=VALUE")
    configure.add_argument("--token-env")
    configure.add_argument("--slides-key", default="slides")
    configure.add_argument("--id-key", default="id")
    configure.add_argument("--title-key", default="title")
    configure.add_argument("--image-url-key", default="image_url")
    configure.add_argument("--expires-at-key", default="expires_at")
    configure.add_argument(
        "--folder", help="Default slide folder; '/' assigns the unfiled root."
    )
    configure.add_argument(
        "--folder-key", default="folder",
        help="JSON folder field path (default: folder); use an empty string to ignore it.",
    )
    configure.add_argument("--timeout-seconds", type=int, default=20)
    for action in ("enable", "disable", "remove"):
        source_action = source_commands.add_parser(action)
        source_action.add_argument("source_id")
    return parser


def _make_catalog(config: AppConfig) -> Catalog:
    return Catalog(config.image_metadata_database_path, config.gallery_dir)


def _make_source_registry(config: AppConfig) -> ApiSourceRegistry:
    return ApiSourceRegistry(config.api_specs_database_path)


def _pick_slide(
    catalog: Catalog, now: float, *, local_only: bool = False,
    enabled: bool | None = None, management: bool = False,
) -> str | None:
    slides = catalog.list_slides(
        now, include_all=management or local_only or enabled is not None,
        scoped=not (management or local_only or enabled is not None),
    )
    if local_only:
        slides = [slide for slide in slides if slide.source == "ssh"]
    if enabled is not None:
        slides = [slide for slide in slides if slide.enabled == enabled]
    if not slides:
        if enabled is not None:
            raise ValueError(f"No {'enabled' if enabled else 'disabled'} slides.")
        if local_only:
            raise ValueError("No available local slides. Add images with 'epaper-display add'.")
        raise ValueError(
            "No available slides in the current scope. Add images with 'epaper-display add' "
            "or change scope with 'epaper-display scope --all'."
        )
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError(
            "'pick' requires an interactive terminal. Over SSH, use 'ssh -t', "
            "or use 'epaper-display list' and a slide ID."
        )
    current_id = catalog.get_state("current_slide_id")
    choices = [
        questionary.Choice(
            f"{slide.title} ({slide.id})"
            + f" [folder: {slide.folder or '/'}]"
            + (" [disabled]" if not slide.enabled else "")
            + (" [expired]" if slide.expires_at is not None and slide.expires_at <= now else ""),
            value=slide.id,
        )
        for slide in slides
    ]
    default_id = current_id if any(slide.id == current_id for slide in slides) else None
    try:
        return questionary.select(
            "Choose a slide (arrow keys, Enter to select, Ctrl+C to cancel):",
            choices=choices,
            default=default_id,
        ).ask(kbi_msg="")
    except EOFError:
        return None


def _pick_scope(catalog: Catalog) -> str | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError(
            "'scope --pick' requires an interactive terminal. Over SSH, use 'ssh -t', "
            "or use 'epaper-display scope <folder>' or 'scope --all'."
        )
    folders = catalog.list_folders()
    current = catalog.get_scope() or "/"
    if current != "/" and current not in folders:
        folders.append(current)
        folders.sort()
    choices = [questionary.Choice("/ (whole gallery)", value="/")] + [
        questionary.Choice(folder, value=folder) for folder in folders
    ]
    try:
        return questionary.select(
            "Choose a playback scope (includes all descendants):",
            choices=choices, default=current,
        ).ask(kbi_msg="")
    except EOFError:
        return None


def _run_service(
    config: AppConfig,
    catalog: Catalog,
    source_registry: ApiSourceRegistry,
    preview: Path | None,
    *,
    fast: bool = False,
) -> None:
    display = (
        PreviewDisplay(
            preview, config.display_orientation, config.portrait_rotation_degrees
        )
        if preview
        else InkyDisplay(fast=fast)
    )
    telemetry = create_telemetry()
    try:
        service = CarouselService(config, catalog, source_registry, display, telemetry=telemetry)

        def stop_service(signum: int, frame: object) -> None:
            service.stop()

        signal.signal(signal.SIGTERM, stop_service)
        signal.signal(signal.SIGINT, stop_service)
        service.run()
    finally:
        if telemetry:
            telemetry.shutdown()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parser().parse_args(argv)
    try:
        config = AppConfig.load(args.config)
        catalog = _make_catalog(config)
        source_registry = _make_source_registry(config)
        now = time.time()

        if args.command == "add":
            for image in args.images:
                slide = catalog.add_image(image, folder=args.folder)
                print(f"{slide.id}\t{slide.title}")
            return 0
        if args.command == "list":
            for slide in catalog.list_slides(now, include_all=args.all, scoped=args.scoped):
                line = f"{slide.id}\t{slide.title}\t{slide.source}"
                if args.all:
                    line += f"\t{'enabled' if slide.enabled else 'disabled'}"
                    if slide.expires_at is not None and slide.expires_at <= now:
                        line += "\texpired"
                line += f"\t{slide.folder or '/'}"
                print(line)
            return 0
        if args.command == "move":
            slide_id = args.slide_id
            if args.pick:
                slide_id = _pick_slide(catalog, now, management=True)
                if slide_id is None:
                    print("Move cancelled.")
                    return 0
            slide = catalog.move_slide(slide_id, args.folder)
            print(f"Moved {slide.id}: {slide.title} -> {slide.folder or '/'}")
            return 0
        if args.command == "scope":
            folder = args.folder
            if args.pick:
                folder = _pick_scope(catalog)
                if folder is None:
                    print("Scope selection cancelled.")
                    return 0
            if args.all or folder is not None:
                catalog.set_scope(folder, time.time(), config.interval_seconds)
            print(f"Playback scope: {catalog.get_scope() or '/'} (includes all descendants)")
            return 0
        if args.command in ("enable", "disable"):
            slide_id = args.slide_id
            if args.pick:
                slide_id = _pick_slide(catalog, now, enabled=args.command == "disable")
                if slide_id is None:
                    print(f"{args.command.capitalize()} cancelled.")
                    return 0
            slide = catalog.set_enabled(slide_id, args.command == "enable")
            print(f"{args.command.capitalize()}d {slide.id}: {slide.title}")
            if slide.enabled and slide.expires_at is not None and slide.expires_at <= now:
                print("The slide is expired and will not appear until its source refreshes it.")
            return 0
        if args.command == "replace":
            slide_id = args.slide_id
            if args.pick:
                slide_id = _pick_slide(catalog, now, local_only=True)
                if slide_id is None:
                    print("Replacement cancelled.")
                    return 0
            slide = catalog.replace_image(slide_id, args.image)
            print(f"Replaced {slide.id}: {slide.title}")
            return 0
        if args.command == "delete":
            slide_id = args.slide_id
            if args.pick:
                slide_id = _pick_slide(catalog, now, local_only=True)
                if slide_id is None:
                    print("Deletion cancelled.")
                    return 0
                try:
                    confirmed = questionary.confirm(
                        f"Delete slide {slide_id} permanently?", default=False
                    ).ask(kbi_msg="")
                except EOFError:
                    confirmed = False
                if not confirmed:
                    print("Deletion cancelled.")
                    return 0
            catalog.delete_slide(slide_id)
            print(f"Deleted {slide_id}.")
            return 0
        if args.command in ("display", "pick", "show"):
            interactive = args.command == "pick" or (
                args.command == "display" and args.pick
            )
            if interactive:
                slide_id = _pick_slide(catalog, now)
                if slide_id is None:
                    print("Selection cancelled.")
                    return 0
                now = time.time()
            else:
                slide_id = args.slide_id
            slide = catalog.select_slide(slide_id, now, config.interval_seconds)
            print(f"Selected {slide.id}: {slide.title}")
            return 0
        if args.command == "next":
            slide = catalog.advance(1, now, config.interval_seconds)
            print(f"Selected {slide.id}: {slide.title}")
            return 0
        if args.command == "previous":
            slide = catalog.advance(-1, now, config.interval_seconds)
            print(f"Selected {slide.id}: {slide.title}")
            return 0
        if args.command in ("pause", "resume"):
            catalog.set_paused(args.command == "pause", now, config.interval_seconds)
            print(
                "Automatic rotation paused. Manual slide controls remain available."
                if args.command == "pause"
                else "Automatic rotation resumed with a fresh interval."
            )
            return 0
        if args.command == "status":
            state = {
                "data_dir": str(config.data_dir),
                "api_specs_database": str(config.api_specs_database_path),
                "image_metadata_database": str(config.image_metadata_database_path),
                "slide_count": len(catalog.list_slides(now)),
                "scope": catalog.get_scope() or "/",
                "scoped_slide_count": len(catalog.list_slides(now, scoped=True)),
                "current_slide_id": catalog.get_state("current_slide_id"),
                "rotation_paused": catalog.get_state("rotation_paused") == "1",
                "next_transition_at": _timestamp(catalog.get_state("next_transition_at")),
                "api_sources": source_registry.refresh_status(),
            }
            print(json.dumps(state, indent=2))
            return 0
        if args.command == "refresh":
            telemetry = create_telemetry()
            try:
                refresh_due_sources(
                    source_registry, catalog, now, force=args.force, telemetry=telemetry
                )
            finally:
                if telemetry:
                    telemetry.shutdown()
            return 0
        if args.command == "sources":
            if args.source_command == "list":
                print(
                    json.dumps(
                        [
                            {
                                "id": source.id,
                                "url": source.url,
                                "type": source.source_type,
                                "enabled": source.enabled,
                                "ttl_seconds": source.ttl_seconds,
                                "query_params": source.query_params,
                                "token_env": source.token_env,
                                "slides_key": source.slides_key,
                                "id_key": source.id_key,
                                "title_key": source.title_key,
                                "image_url_key": source.image_url_key,
                                "expires_at_key": source.expires_at_key,
                                "folder": source.folder,
                                "folder_key": source.folder_key,
                                "timeout_seconds": source.timeout_seconds,
                            }
                            for source in source_registry.list_sources()
                        ],
                        indent=2,
                    )
                )
                return 0
            if args.source_command == "configure":
                query_params: dict[str, str] = {}
                existing_source = source_registry.get(args.source_id)
                for item in args.query_param:
                    key, separator, value = item.partition("=")
                    if not separator or not key:
                        raise ValueError(f"Invalid query parameter {item!r}; use KEY=VALUE.")
                    if key in query_params:
                        raise ValueError(f"Query parameter {key!r} was provided more than once.")
                    query_params[key] = value
                source_registry.save(
                    ApiSource(
                        id=args.source_id,
                        url=args.url,
                        source_type=args.source_type,
                        ttl_seconds=args.ttl_seconds,
                        query_params=query_params,
                        token_env=args.token_env,
                        slides_key=args.slides_key,
                        id_key=args.id_key,
                        title_key=args.title_key,
                        image_url_key=args.image_url_key,
                        expires_at_key=args.expires_at_key,
                        folder=args.folder,
                        folder_key=args.folder_key,
                        timeout_seconds=args.timeout_seconds,
                        enabled=existing_source.enabled if existing_source else True,
                    )
                )
                print(f"Configured API source {args.source_id}.")
                return 0
            if args.source_command in ("enable", "disable"):
                source_registry.set_enabled(
                    args.source_id, enabled=args.source_command == "enable"
                )
                print(f"{args.source_command.capitalize()}d API source {args.source_id}.")
                return 0
            if args.source_command == "remove":
                source_registry.remove(args.source_id)
                print(f"Removed API source {args.source_id}.")
                return 0
        if args.command == "run":
            _run_service(config, catalog, source_registry, args.preview, fast=args.fast)
            return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"epaper-display: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
