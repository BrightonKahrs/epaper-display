from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse


@dataclass(frozen=True)
class ApiSource:
    id: str
    url: str
    ttl_seconds: int = 3600
    query_params: dict[str, str] = field(default_factory=dict)
    token_env: str | None = None
    slides_key: str = "slides"
    id_key: str = "id"
    title_key: str = "title"
    image_url_key: str = "image_url"
    expires_at_key: str = "expires_at"
    timeout_seconds: int = 20
    enabled: bool = True

    def validate(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Source id must be a non-empty string.")
        if not isinstance(self.url, str):
            raise ValueError(f"Source {self.id!r} URL must be absolute HTTP(S).")
        parsed_url = urlparse(self.url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            raise ValueError(f"Source {self.id!r} URL must be absolute HTTP(S).")
        if (
            isinstance(self.ttl_seconds, bool)
            or not isinstance(self.ttl_seconds, int)
            or self.ttl_seconds < 1
            or isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds < 1
        ):
            raise ValueError("Source TTL and request timeout must be positive.")
        if self.token_env is not None and (
            not isinstance(self.token_env, str) or not self.token_env.strip()
        ):
            raise ValueError("token_env must be a non-empty environment variable name.")
        if not isinstance(self.query_params, dict) or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            for key, value in self.query_params.items()
        ):
            raise ValueError("Query parameter names and values must be strings.")
        if not all(
            isinstance(key, str) and key
            for key in (self.slides_key, self.id_key, self.title_key, self.image_url_key)
        ):
            raise ValueError("Response mapping keys must be non-empty strings.")
        if not isinstance(self.expires_at_key, str):
            raise ValueError("expires_at_key must be a string.")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean.")


class ApiSourceRegistry:
    """Persists API request specifications and their independent TTL state."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_specs (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    query_params_json TEXT NOT NULL,
                    token_env TEXT,
                    slides_key TEXT NOT NULL,
                    id_key TEXT NOT NULL,
                    title_key TEXT NOT NULL,
                    image_url_key TEXT NOT NULL,
                    expires_at_key TEXT NOT NULL,
                    timeout_seconds INTEGER NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at REAL NOT NULL DEFAULT (unixepoch()),
                    updated_at REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS refresh_state (
                    source_id TEXT PRIMARY KEY REFERENCES source_specs(id) ON DELETE CASCADE,
                    last_success_at REAL,
                    next_refresh_at REAL NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _source_from_row(self, row: sqlite3.Row) -> ApiSource:
        return ApiSource(
            id=row["id"],
            url=row["url"],
            ttl_seconds=row["ttl_seconds"],
            query_params=json.loads(row["query_params_json"]),
            token_env=row["token_env"],
            slides_key=row["slides_key"],
            id_key=row["id_key"],
            title_key=row["title_key"],
            image_url_key=row["image_url_key"],
            expires_at_key=row["expires_at_key"],
            timeout_seconds=row["timeout_seconds"],
            enabled=bool(row["enabled"]),
        )

    def save(self, source: ApiSource) -> None:
        source.validate()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_specs (
                    id, url, ttl_seconds, query_params_json, token_env, slides_key,
                    id_key, title_key, image_url_key, expires_at_key, timeout_seconds,
                    enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    url = excluded.url,
                    ttl_seconds = excluded.ttl_seconds,
                    query_params_json = excluded.query_params_json,
                    token_env = excluded.token_env,
                    slides_key = excluded.slides_key,
                    id_key = excluded.id_key,
                    title_key = excluded.title_key,
                    image_url_key = excluded.image_url_key,
                    expires_at_key = excluded.expires_at_key,
                    timeout_seconds = excluded.timeout_seconds,
                    enabled = excluded.enabled,
                    updated_at = unixepoch()
                """,
                (
                    source.id,
                    source.url,
                    source.ttl_seconds,
                    json.dumps(source.query_params, sort_keys=True),
                    source.token_env,
                    source.slides_key,
                    source.id_key,
                    source.title_key,
                    source.image_url_key,
                    source.expires_at_key,
                    source.timeout_seconds,
                    int(source.enabled),
                ),
            )
            connection.execute(
                """
                INSERT INTO refresh_state (source_id, next_refresh_at)
                VALUES (?, 0)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_success_at = NULL,
                    next_refresh_at = 0,
                    failure_count = 0,
                    last_error = NULL
                """,
                (source.id,),
            )

    def get(self, source_id: str) -> ApiSource | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_specs WHERE id = ?", (source_id,)
            ).fetchone()
        return self._source_from_row(row) if row else None

    def list_sources(self, enabled_only: bool = False) -> list[ApiSource]:
        query = "SELECT * FROM source_specs"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [self._source_from_row(row) for row in rows]

    def set_enabled(self, source_id: str, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean.")
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE source_specs SET enabled = ?, updated_at = unixepoch() WHERE id = ?",
                (int(enabled), source_id),
            )
            if result.rowcount == 0:
                raise KeyError(f"Unknown API source: {source_id}")
            if enabled:
                connection.execute(
                    "UPDATE refresh_state SET next_refresh_at = 0 WHERE source_id = ?",
                    (source_id,),
                )

    def remove(self, source_id: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM source_specs WHERE id = ?", (source_id,)
            )
            if result.rowcount == 0:
                raise KeyError(f"Unknown API source: {source_id}")

    def is_due(self, source_id: str, now: float) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT next_refresh_at FROM refresh_state WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return row is None or row["next_refresh_at"] <= now

    def record_success(self, source_id: str, now: float, ttl_seconds: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO refresh_state
                    (source_id, last_success_at, next_refresh_at, failure_count, last_error)
                VALUES (?, ?, ?, 0, NULL)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_success_at = excluded.last_success_at,
                    next_refresh_at = excluded.next_refresh_at,
                    failure_count = 0,
                    last_error = NULL
                """,
                (source_id, now, now + ttl_seconds),
            )

    def record_failure(self, source_id: str, now: float, error: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT failure_count FROM refresh_state WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            failure_count = (row["failure_count"] if row else 0) + 1
            retry_delay = min(60 * 2 ** min(failure_count - 1, 6), 3600)
            connection.execute(
                """
                INSERT INTO refresh_state
                    (source_id, next_refresh_at, failure_count, last_error)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    next_refresh_at = excluded.next_refresh_at,
                    failure_count = excluded.failure_count,
                    last_error = excluded.last_error
                """,
                (source_id, now + retry_delay, failure_count, error),
            )

    def refresh_status(self) -> dict[str, dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_specs.id, source_specs.enabled, refresh_state.*
                FROM source_specs LEFT JOIN refresh_state
                    ON refresh_state.source_id = source_specs.id
                ORDER BY source_specs.id
                """
            ).fetchall()
        return {
            row["id"]: {
                "enabled": bool(row["enabled"]),
                "last_success_at": row["last_success_at"],
                "next_refresh_at": row["next_refresh_at"],
                "failure_count": row["failure_count"] or 0,
                "last_error": row["last_error"],
            }
            for row in rows
        }
