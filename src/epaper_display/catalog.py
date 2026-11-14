from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from PIL import Image, UnidentifiedImageError


_IMAGE_EXTENSIONS = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "GIF": ".gif",
    "TIFF": ".tiff",
}


@dataclass(frozen=True)
class Slide:
    id: str
    title: str
    path: Path
    source: str
    created_at: float
    expires_at: float | None
    enabled: bool = True


class Catalog:
    def __init__(self, database_path: Path, gallery_dir: Path):
        self.database_path = database_path
        self.gallery_dir = gallery_dir
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.gallery_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS slides (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    source TEXT NOT NULL,
                    external_id TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE UNIQUE INDEX IF NOT EXISTS slides_external_identity
                    ON slides(source, external_id)
                    WHERE external_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS slides_order
                    ON slides(enabled, created_at, id);
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _slide_from_row(self, row: sqlite3.Row) -> Slide:
        return Slide(
            id=row["id"],
            title=row["title"],
            path=self.gallery_dir / row["filename"],
            source=row["source"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            enabled=bool(row["enabled"]),
        )

    def _active_rows(self, connection: sqlite3.Connection, now: float) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT * FROM slides
            WHERE enabled = 1 AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at, id
            """,
            (now,),
        ).fetchall()

    def _store_image(
        self,
        source_path: Path,
        slide_id: str,
        title: str,
        source: str,
        external_id: str | None,
        expires_at: float | None,
        created_at: float,
        *,
        replace_local: bool = False,
    ) -> Slide:
        try:
            with Image.open(source_path) as image:
                image.verify()
                image_format = image.format
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError(f"Not a readable image: {source_path}") from exc
        extension = _IMAGE_EXTENSIONS.get(image_format or "")
        if extension is None:
            raise ValueError(f"Unsupported image format: {image_format}")

        filename = f"{slide_id}{extension}"
        destination = self.gallery_dir / filename
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{slide_id}-", suffix=extension, dir=self.gallery_dir
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        backup_path: Path | None = None
        old_filename: str | None = None
        replaced = False
        try:
            shutil.copyfile(source_path, temporary_path)
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                old = connection.execute(
                    "SELECT * FROM slides WHERE id = ?", (slide_id,)
                ).fetchone()
                if replace_local:
                    self._require_local_slide(old, slide_id)
                    title = old["title"]
                    created_at = old["created_at"]
                    expires_at = old["expires_at"]
                old_filename = old["filename"] if old else None
                if destination.exists():
                    descriptor, backup_name = tempfile.mkstemp(
                        prefix=f".{slide_id}-backup-", dir=self.gallery_dir
                    )
                    os.close(descriptor)
                    candidate_backup = Path(backup_name)
                    try:
                        shutil.copyfile(destination, candidate_backup)
                        backup_path = candidate_backup
                    except Exception:
                        candidate_backup.unlink(missing_ok=True)
                        raise
                os.replace(temporary_path, destination)
                replaced = True
                connection.execute(
                    """
                    INSERT INTO slides
                        (id, title, filename, source, external_id, created_at, expires_at, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        filename = excluded.filename,
                        source = excluded.source,
                        external_id = excluded.external_id,
                        expires_at = excluded.expires_at
                    """,
                    (
                        slide_id,
                        title,
                        filename,
                        source,
                        external_id,
                        created_at,
                        expires_at,
                    ),
                )
                enabled = bool(old["enabled"]) if old else True
                if replace_local:
                    connection.execute(
                        """
                        INSERT INTO state(key, value) VALUES (?, '1')
                        ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1
                        """,
                        (f"slide_revision:{slide_id}",),
                    )
        except Exception:
            temporary_path.unlink(missing_ok=True)
            if replaced and backup_path is not None:
                os.replace(backup_path, destination)
            elif replaced:
                destination.unlink()
            elif backup_path is not None:
                backup_path.unlink(missing_ok=True)
            raise
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)
        if old_filename and old_filename != filename:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT filename FROM slides WHERE id = ?", (slide_id,)
                ).fetchone()
                if current is None or current["filename"] != old_filename:
                    (self.gallery_dir / old_filename).unlink(missing_ok=True)
        return Slide(slide_id, title, destination, source, created_at, expires_at, enabled)

    @staticmethod
    def _require_local_slide(row: sqlite3.Row | None, slide_id: str) -> None:
        if row is None:
            raise ValueError(f"Slide not found: {slide_id}")
        if row["source"] != "ssh":
            raise ValueError(f"Only local slides can be replaced or deleted: {slide_id}")

    def replace_image(self, slide_id: str, source_path: Path) -> Slide:
        try:
            valid_id = str(uuid.UUID(slide_id)) == slide_id
        except ValueError:
            valid_id = False
        if not valid_id:
            raise ValueError(f"Invalid slide ID: {slide_id}")
        source_path = source_path.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        return self._store_image(
            source_path, slide_id, "", "ssh", None, None,
            datetime.now().timestamp(), replace_local=True,
        )

    def delete_slide(self, slide_id: str) -> None:
        backup_path: Path | None = None
        image_path: Path | None = None
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM slides WHERE id = ?", (slide_id,)
                ).fetchone()
                self._require_local_slide(row, slide_id)
                image_path = self.gallery_dir / row["filename"]
                descriptor, name = tempfile.mkstemp(
                    prefix=f".{slide_id}-deleted-", dir=self.gallery_dir
                )
                os.close(descriptor)
                backup_path = Path(name)
                try:
                    os.replace(image_path, backup_path)
                except OSError:
                    backup_path.unlink(missing_ok=True)
                    backup_path = None
                    raise
                connection.execute("DELETE FROM slides WHERE id = ?", (slide_id,))
                connection.execute(
                    "DELETE FROM state WHERE key = ?", (f"slide_revision:{slide_id}",)
                )
        except Exception:
            if backup_path is not None and image_path is not None:
                os.replace(backup_path, image_path)
            raise
        if backup_path is not None:
            backup_path.unlink()

    def add_image(
        self,
        source_path: Path,
        title: str | None = None,
        *,
        source: str = "ssh",
        external_id: str | None = None,
        expires_at: float | None = None,
        now: float | None = None,
    ) -> Slide:
        source_path = source_path.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        created_at = now if now is not None else datetime.now().timestamp()
        slide_id = (
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:{external_id}"))
            if external_id is not None
            else str(uuid.uuid4())
        )
        return self._store_image(
            source_path,
            slide_id,
            title or source_path.stem,
            source,
            external_id,
            expires_at,
            created_at,
        )

    def list_slides(self, now: float | None = None, *, include_all: bool = False) -> list[Slide]:
        timestamp = now if now is not None else datetime.now().timestamp()
        with self._connect() as connection:
            if include_all:
                rows = connection.execute(
                    "SELECT * FROM slides ORDER BY created_at, id"
                ).fetchall()
            else:
                rows = self._active_rows(connection, timestamp)
        return [self._slide_from_row(row) for row in rows]

    def set_enabled(self, slide_id: str, enabled: bool) -> Slide:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM slides WHERE id = ?", (slide_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Slide not found: {slide_id}")
            connection.execute(
                "UPDATE slides SET enabled = ? WHERE id = ?", (int(enabled), slide_id)
            )
            updated = connection.execute(
                "SELECT * FROM slides WHERE id = ?", (slide_id,)
            ).fetchone()
        return self._slide_from_row(updated)

    def get_slide(self, slide_id: str, now: float | None = None) -> Slide | None:
        timestamp = now if now is not None else datetime.now().timestamp()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM slides
                WHERE id = ? AND enabled = 1
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (slide_id, timestamp),
            ).fetchone()
        return self._slide_from_row(row) if row else None

    def get_state(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_paused(self, paused: bool, now: float, interval_seconds: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO state(key, value) VALUES ('rotation_paused', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("1" if paused else "0",),
            )
            current_id = self._state_value(connection, "current_slide_id")
            self._write_carousel_state(
                connection, current_id, now + interval_seconds if current_id else None
            )

    def _write_carousel_state(
        self,
        connection: sqlite3.Connection,
        slide_id: str | None,
        next_transition_at: float | None,
    ) -> None:
        if self._state_value(connection, "rotation_paused") == "1":
            next_transition_at = None
        values = {
            "current_slide_id": slide_id or "",
            "next_transition_at": (
                str(next_transition_at) if next_transition_at is not None else ""
            ),
        }
        connection.executemany(
            """
            INSERT INTO state(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            values.items(),
        )

    def initialize_carousel(self, now: float, interval_seconds: int) -> Slide | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            slides = self._active_rows(connection, now)
            current_id = self._state_value(connection, "current_slide_id")
            current = next((slide for slide in slides if slide["id"] == current_id), None)
            if current is None and slides:
                current = slides[0]
            slide_id = current["id"] if current else None
            self._write_carousel_state(connection, slide_id, now + interval_seconds if slide_id else None)
        return self._slide_from_row(current) if current else None

    def _state_value(self, connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def select_slide(self, slide_id: str, now: float, interval_seconds: int) -> Slide:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            slides = self._active_rows(connection, now)
            selected = next((slide for slide in slides if slide["id"] == slide_id), None)
            if selected is None:
                raise ValueError(f"Slide not found or expired: {slide_id}")
            self._write_carousel_state(connection, slide_id, now + interval_seconds)
        return self._slide_from_row(selected)

    def advance(self, direction: int, now: float, interval_seconds: int) -> Slide:
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or 1")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            slides = self._active_rows(connection, now)
            if not slides:
                raise ValueError("The gallery has no active slides.")
            current_id = self._state_value(connection, "current_slide_id")
            current_index = next(
                (index for index, slide in enumerate(slides) if slide["id"] == current_id),
                -1 if direction > 0 else 0,
            )
            selected = slides[(current_index + direction) % len(slides)]
            self._write_carousel_state(
                connection, selected["id"], now + interval_seconds
            )
        return self._slide_from_row(selected)

    def advance_if_due(self, now: float, interval_seconds: int) -> Slide | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._state_value(connection, "rotation_paused") == "1":
                return None
            next_transition = self._state_value(connection, "next_transition_at")
            if not next_transition or float(next_transition) > now:
                return None
            slides = self._active_rows(connection, now)
            if not slides:
                self._write_carousel_state(connection, None, None)
                return None
            current_id = self._state_value(connection, "current_slide_id")
            current_index = next(
                (index for index, slide in enumerate(slides) if slide["id"] == current_id),
                -1,
            )
            selected = slides[(current_index + 1) % len(slides)]
            self._write_carousel_state(
                connection, selected["id"], now + interval_seconds
            )
        return self._slide_from_row(selected)
