from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from epaper_display.catalog import Catalog, normalize_folder
from epaper_display.source_registry import ApiSource, ApiSourceRegistry


_MAX_FEED_BYTES = 2 * 1024 * 1024
_MAX_IMAGE_BYTES = 25 * 1024 * 1024
_MISSING = object()


def _mapped_value(value: Any, key_path: str, default: Any = None) -> Any:
    if not key_path:
        return value
    for key in key_path.split("."):
        if not isinstance(value, dict):
            return default
        value = value.get(key, default)
    return value


def _request_url(source: ApiSource) -> str:
    parsed = urlparse(source.url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend(source.query_params.items())
    return urlunparse(parsed._replace(query=urlencode(query)))


def _read_source(source: ApiSource, token: str | None) -> list[dict[str, Any]]:
    headers = {"Accept": "application/json", "User-Agent": "epaper-display/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(_request_url(source), headers=headers)
    with urlopen(request, timeout=source.timeout_seconds) as response:
        body = response.read(_MAX_FEED_BYTES + 1)
    if len(body) > _MAX_FEED_BYTES:
        raise ValueError(f"Source {source.id!r} response exceeds {_MAX_FEED_BYTES} bytes.")
    payload = json.loads(body)
    entries = _mapped_value(payload, source.slides_key)
    if not isinstance(entries, list):
        raise ValueError(
            f"Source {source.id!r} must return an array at {source.slides_key!r}."
        )
    if not all(isinstance(entry, dict) for entry in entries):
        raise ValueError(f"Source {source.id!r} slide entries must be JSON objects.")
    return entries


def _parse_expiration(value: Any) -> float | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expires_at must be an ISO-8601 string.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include a timezone.")
    return parsed.timestamp()


def _parse_folder(value: Any) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError("Slide folder must be a string or null.")
    return normalize_folder(value)


def _same_origin(first_url: str, second_url: str) -> bool:
    first = urlparse(first_url)
    second = urlparse(second_url)
    first_port = first.port or (443 if first.scheme == "https" else 80)
    second_port = second.port or (443 if second.scheme == "https" else 80)
    return (
        first.scheme == second.scheme
        and first.hostname == second.hostname
        and first_port == second_port
    )


def _download_image(
    url: str, catalog: Catalog, timeout: int, token: str | None
) -> Path:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Image URL must be absolute HTTP(S): {url}")
    headers = {"User-Agent": "epaper-display/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        content = response.read(_MAX_IMAGE_BYTES + 1)
    if len(content) > _MAX_IMAGE_BYTES:
        raise ValueError(f"Image exceeds {_MAX_IMAGE_BYTES} bytes: {url}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".source-download-", dir=catalog.gallery_dir
    )
    os.close(descriptor)
    path = Path(temporary_name)
    try:
        path.write_bytes(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _import_image(
    source: ApiSource,
    catalog: Catalog,
    url: str,
    external_id: str,
    title: str,
    expires_at: float | None,
    now: float,
    token: str | None,
    *,
    folder: str | None = None,
    update_folder: bool = False,
) -> None:
    image_path = _download_image(url, catalog, source.timeout_seconds, token)
    try:
        catalog.add_image(
            image_path, title, source=f"api:{source.id}",
            external_id=external_id, expires_at=expires_at, now=now,
            folder=folder, update_folder=update_folder,
        )
    finally:
        image_path.unlink(missing_ok=True)


def refresh_source(
    source: ApiSource,
    catalog: Catalog,
    source_registry: ApiSourceRegistry,
    now: float | None = None,
) -> int:
    timestamp = now if now is not None else time.time()
    source.validate()
    token = os.environ.get(source.token_env) if source.token_env else None
    if source.token_env and not token:
        raise ValueError(
            f"Source {source.id!r} requires environment variable {source.token_env}."
        )
    if source.source_type == "image":
        _import_image(
            source, catalog, _request_url(source), "image", source.id, None, timestamp, token,
            folder=source.folder, update_folder=source.folder is not None,
        )
        source_registry.record_success(source.id, timestamp, source.ttl_seconds)
        return 1
    entries = _read_source(source, token)
    imported = 0
    for entry in entries:
        external_id = _mapped_value(entry, source.id_key)
        image_url = _mapped_value(entry, source.image_url_key)
        if (
            isinstance(external_id, bool)
            or not isinstance(external_id, (str, int))
            or not str(external_id).strip()
            or not isinstance(image_url, str)
            or not image_url.strip()
        ):
            raise ValueError(
                f"Source {source.id!r} slide entries require id and image URL fields."
            )
        title = _mapped_value(entry, source.title_key)
        if title is not None and not isinstance(title, str):
            raise ValueError(f"Source {source.id!r} slide titles must be strings.")
        expiration = (
            _mapped_value(entry, source.expires_at_key)
            if source.expires_at_key
            else None
        )
        folder = _mapped_value(entry, source.folder_key, _MISSING) if source.folder_key else _MISSING
        if folder is _MISSING and source.folder is not None:
            folder = source.folder
        update_folder = folder is not _MISSING
        parsed_folder = _parse_folder(folder) if update_folder else None
        resolved_image_url = urljoin(source.url, image_url)
        image_token = token if _same_origin(source.url, resolved_image_url) else None
        _import_image(
            source, catalog, resolved_image_url, str(external_id),
            title or str(external_id), _parse_expiration(expiration), timestamp, image_token,
            folder=parsed_folder, update_folder=update_folder,
        )
        imported += 1
    source_registry.record_success(source.id, timestamp, source.ttl_seconds)
    return imported
