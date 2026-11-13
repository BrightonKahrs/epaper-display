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


def _timestamp(value: str | None) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="epaper-display")
    parser.add_argument("--config", type=Path, help="Path to the JSON configuration file.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Add one or more local image files to the gallery.")
    add.add_argument("images", nargs="+", type=Path)

    commands.add_parser("list", help="List non-expired slides.")
    commands.add_parser("pick", help="Choose a slide from an interactive arrow-key menu.")
    select = commands.add_parser("show", help="Select a slide by ID.")
    select.add_argument("slide_id")
    commands.add_parser("next", help="Advance to the next slide.")
    commands.add_parser("previous", help="Select the previous slide.")
    commands.add_parser("status", help="Show carousel and API source status.")

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
    configure.add_argument("--ttl-seconds", type=int, default=3600)
    configure.add_argument("--query-param", action="append", default=[], metavar="KEY=VALUE")
    configure.add_argument("--token-env")
    configure.add_argument("--slides-key", default="slides")
    configure.add_argument("--id-key", default="id")
    configure.add_argument("--title-key", default="title")
    configure.add_argument("--image-url-key", default="image_url")
    configure.add_argument("--expires-at-key", default="expires_at")
    configure.add_argument("--timeout-seconds", type=int, default=20)
    for action in ("enable", "disable", "remove"):
        source_action = source_commands.add_parser(action)
        source_action.add_argument("source_id")
    return parser


def _make_catalog(config: AppConfig) -> Catalog:
    return Catalog(config.image_metadata_database_path, config.gallery_dir)


def _make_source_registry(config: AppConfig) -> ApiSourceRegistry:
    return ApiSourceRegistry(config.api_specs_database_path)


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
    service = CarouselService(config, catalog, source_registry, display)

    def stop_service(signum: int, frame: object) -> None:
        service.stop()

    signal.signal(signal.SIGTERM, stop_service)
    signal.signal(signal.SIGINT, stop_service)
    service.run()


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
                slide = catalog.add_image(image)
                print(f"{slide.id}\t{slide.title}")
            return 0
        if args.command == "list":
            for slide in catalog.list_slides(now):
                print(f"{slide.id}\t{slide.title}\t{slide.source}")
            return 0
        if args.command == "pick":
            slides = catalog.list_slides(now)
            if not slides:
                raise ValueError("No available slides. Add images with 'epaper-display add'.")
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError(
                    "'pick' requires an interactive terminal. Over SSH, use 'ssh -t', "
                    "or use 'epaper-display list' and 'epaper-display show <slide-id>'."
                )
            current_id = catalog.get_state("current_slide_id")
            choices = [
                questionary.Choice(f"{slide.title} ({slide.id})", value=slide.id)
                for slide in slides
            ]
            default_id = current_id if any(slide.id == current_id for slide in slides) else None
            try:
                selected_id = questionary.select(
                    "Choose a slide (arrow keys, Enter to select, Ctrl+C to cancel):",
                    choices=choices,
                    default=default_id,
                ).ask(kbi_msg="")
            except EOFError:
                selected_id = None
            if selected_id is None:
                print("Selection cancelled.")
                return 0
            slide = catalog.select_slide(selected_id, time.time(), config.interval_seconds)
            print(f"Selected {slide.id}: {slide.title}")
            return 0
        if args.command == "show":
            slide = catalog.select_slide(args.slide_id, now, config.interval_seconds)
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
        if args.command == "status":
            state = {
                "data_dir": str(config.data_dir),
                "api_specs_database": str(config.api_specs_database_path),
                "image_metadata_database": str(config.image_metadata_database_path),
                "slide_count": len(catalog.list_slides(now)),
                "current_slide_id": catalog.get_state("current_slide_id"),
                "next_transition_at": _timestamp(catalog.get_state("next_transition_at")),
                "api_sources": source_registry.refresh_status(),
            }
            print(json.dumps(state, indent=2))
            return 0
        if args.command == "refresh":
            refresh_due_sources(source_registry, catalog, now, force=args.force)
            return 0
        if args.command == "sources":
            if args.source_command == "list":
                print(
                    json.dumps(
                        [
                            {
                                "id": source.id,
                                "url": source.url,
                                "enabled": source.enabled,
                                "ttl_seconds": source.ttl_seconds,
                                "query_params": source.query_params,
                                "token_env": source.token_env,
                                "slides_key": source.slides_key,
                                "id_key": source.id_key,
                                "title_key": source.title_key,
                                "image_url_key": source.image_url_key,
                                "expires_at_key": source.expires_at_key,
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
                        ttl_seconds=args.ttl_seconds,
                        query_params=query_params,
                        token_env=args.token_env,
                        slides_key=args.slides_key,
                        id_key=args.id_key,
                        title_key=args.title_key,
                        image_url_key=args.image_url_key,
                        expires_at_key=args.expires_at_key,
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
