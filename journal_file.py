"""
Shared reader for DrugsPRO journal exports.

Mirrors the app's own file reader (webpack module 67331) so a converter sees the
same text the app would. Four containers are handled:

  1. plain JSON                    browser download
  2. gzip bytes (1f 8b)            gzipped download
  3. "DRUGSPRO-GZ:v1:<base64>"     what localStorage / cloud sync stores
  4. 10-char digest line + JSON    the mobile app's backup shape
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

GZ_PREFIX = "DRUGSPRO-GZ:v1:"
BACKUP_VERSION = 1

_NATIVE_PREFIX = re.compile(r"^[a-f0-9]{10}\r?\n")
_DAY_KEY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_CLOCK = re.compile(r"^(\d{2}):(\d{2})$")


def decode_backup_bytes(data: bytes) -> str:
    """Decodes the raw bytes of a backup into text."""
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        return gzip.decompress(data).decode("utf-8")

    text = data.decode("utf-8").lstrip("\ufeff")
    if text.startswith(GZ_PREFIX):
        inner = base64.b64decode(text[len(GZ_PREFIX) :])
        return gzip.decompress(inner).decode("utf-8")
    return text


def read_backup_file(path: str | Path) -> dict[str, Any]:
    """Reads a backup file into the raw DrugsPRO payload."""
    raw = decode_backup_bytes(Path(path).read_bytes())
    text = _NATIVE_PREFIX.sub("", raw).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Backup is not a JSON object")
    if payload.get("v") != BACKUP_VERSION:
        raise ValueError(f"Unsupported backup version: {payload.get('v')!r}")
    return payload


def as_local_datetime(value: Any) -> datetime | None:
    """Best effort conversion of an epoch-ms / ISO string / datetime to local time."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(value / 1000)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed


def to_ms(value: Any) -> int | None:
    """Epoch milliseconds for a date-ish value, matching Date#getTime()."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    parsed = as_local_datetime(value)
    if parsed is None:
        return None
    return int(parsed.timestamp()) * 1000 + parsed.microsecond // 1000


def to_day_key(value: Any) -> str | None:
    """
    `YYYY-MM-DD` from local date parts, matching the app's own day key helper
    (module 29866 `Y`), which uses getFullYear/getMonth/getDate - not UTC.
    """
    parsed = as_local_datetime(value)
    if parsed is None:
        return None
    return f"{parsed.year:04d}-{parsed.month:02d}-{parsed.day:02d}"


def local_instant(day_key: Any, time_hhmm: Any = None, day_offset: int = 0) -> datetime | None:
    """Local datetime for `YYYY-MM-DD` plus an optional `HH:MM`, defaulting to noon."""
    day = _DAY_KEY.match(str(day_key or "").strip())
    if not day:
        return None

    clock = _CLOCK.match(str(time_hhmm or "").strip())
    hours, minutes = (int(clock.group(1)), int(clock.group(2))) if clock else (12, 0)

    try:
        base = datetime(int(day.group(1)), int(day.group(2)), int(day.group(3)), hours, minutes)
    except ValueError:
        return None
    return base + timedelta(days=day_offset)


def local_instant_ms(day_key: Any, time_hhmm: Any = None, day_offset: int = 0) -> int | None:
    return to_ms(local_instant(day_key, time_hhmm, day_offset))
