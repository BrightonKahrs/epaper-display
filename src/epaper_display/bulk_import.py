from __future__ import annotations

import logging
import sqlite3
import stat
import tempfile
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from PIL import Image

from epaper_display.catalog import Catalog, Slide, SUPPORTED_IMAGE_SUFFIXES, normalize_folder


logger = logging.getLogger(__name__)
MAX_ENTRIES = 10_000
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def _archive_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    parts = normalized.rstrip("/").split("/")
    if (
        normalized.startswith("/")
        or any(part in ("", ".", "..") or ":" in part for part in parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError(f"Unsafe ZIP entry path: {name!r}")
    normalize_folder("/".join(parts))
    return PurePosixPath(*parts)


def _folder(path: PurePosixPath, preserve_folders: bool, root: str | None) -> str | None:
    if not preserve_folders:
        return None
    parent = path.parts[:-1]
    if root is not None:
        parent = parent[1:]
    return normalize_folder("/".join(parent))


def _check_sizes(sizes: list[int]) -> None:
    if any(size > MAX_IMAGE_BYTES for size in sizes):
        raise ValueError("Bulk import images must be at most 64 MiB each.")
    if sum(sizes) > MAX_TOTAL_BYTES:
        raise ValueError("Bulk import image data must be at most 1 GiB in total.")


def _zip_entries(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES:
        raise ValueError("Bulk imports support at most 10,000 ZIP entries.")
    images = []
    seen: set[PurePosixPath] = set()
    for entry in entries:
        path = _archive_path(entry.filename)
        mode = entry.external_attr >> 16
        if stat.S_ISLNK(mode) or (
            stat.S_IFMT(mode) and not stat.S_ISREG(mode) and not stat.S_ISDIR(mode)
        ):
            raise ValueError(f"ZIP links and special files are not supported: {entry.filename}")
        if entry.is_dir():
            continue
        if path in seen:
            raise ValueError(f"Duplicate ZIP entry: {entry.filename}")
        seen.add(path)
        if "__MACOSX" in path.parts or path.name.startswith("._"):
            logger.info("Skipping ZIP metadata: %s", entry.filename)
        elif path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            logger.info("Skipping non-image: %s", entry.filename)
        elif entry.flag_bits & 1:
            raise ValueError(f"Encrypted ZIP images are not supported: {entry.filename}")
        else:
            images.append((entry, path))
    _check_sizes([entry.file_size for entry, _ in images])
    return sorted(images, key=lambda item: str(item[1]))


def bulk_add(
    catalog: Catalog, source: Path, *,
    preserve_folders: bool = False, ignore_root: bool = False,
) -> Iterator[Slide]:
    """Import sequentially, extracting at most one ZIP image into temporary storage."""
    if ignore_root and not preserve_folders:
        raise ValueError("--ignore-root requires --preserve-folders.")
    source = source.expanduser().resolve()
    imported = 0
    current = str(source)
    try:
        if source.is_dir():
            files: list[tuple[Path, PurePosixPath]] = []
            for index, path in enumerate(source.rglob("*"), 1):
                if index > MAX_ENTRIES:
                    raise ValueError("Bulk imports support at most 10,000 directory entries.")
                if path.is_symlink():
                    raise ValueError(f"Directory links are not supported: {path}")
                if path.is_file():
                    relative = PurePosixPath(source.name, *path.relative_to(source).parts)
                    if "__MACOSX" in relative.parts or relative.name.startswith("._"):
                        logger.info("Skipping directory metadata: %s", path)
                    elif path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
                        normalize_folder(str(relative))
                        files.append((path, relative))
                    else:
                        logger.info("Skipping non-image: %s", path)
                elif not path.is_dir():
                    raise ValueError(f"Directory special files are not supported: {path}")
            _check_sizes([path.stat().st_size for path, _ in files])
            if not files:
                raise ValueError("No supported images found.")
            for path, relative in sorted(files, key=lambda item: str(item[1])):
                current = str(relative)
                slide = catalog.add_image(
                    path, title=path.stem,
                    folder=_folder(relative, preserve_folders, source.name if ignore_root else None),
                )
                imported += 1
                yield slide
        else:
            with zipfile.ZipFile(source) as archive:
                images = _zip_entries(archive)
                if not images:
                    raise ValueError("No supported images found.")
                root = None
                if ignore_root:
                    roots = {path.parts[0] for _, path in images if len(path.parts) > 1}
                    if len(roots) != 1 or any(len(path.parts) == 1 for _, path in images):
                        raise ValueError("--ignore-root requires one common enclosing folder in the ZIP.")
                    root = next(iter(roots))
                with tempfile.TemporaryDirectory(prefix="epaper-import-") as temporary:
                    for entry, relative in images:
                        current = str(relative)
                        path = Path(temporary) / ("image" + relative.suffix.lower())
                        with archive.open(entry) as reader, path.open("wb") as writer:
                            written = 0
                            while chunk := reader.read(64 * 1024):
                                written += len(chunk)
                                if written > MAX_IMAGE_BYTES:
                                    raise ValueError("Extracted image exceeds the 64 MiB limit.")
                                writer.write(chunk)
                        slide = catalog.add_image(
                            path, title=relative.stem,
                            folder=_folder(relative, preserve_folders, root),
                        )
                        path.unlink()
                        imported += 1
                        yield slide
    except (
        OSError, ValueError, sqlite3.Error, zipfile.BadZipFile, zlib.error, EOFError,
        RuntimeError, NotImplementedError, Image.DecompressionBombError,
    ) as exc:
        raise ValueError(
            f"Bulk import failed at {current!r} after {imported} image(s) imported: {exc}. "
            "Already imported slides remain in the gallery."
        ) from exc
