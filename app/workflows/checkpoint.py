from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver


logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv('WORKFLOW_CHECKPOINT_DB', 'checkpoint.db'))
_CONN: sqlite3.Connection | None = None
_CHECKPOINTER: SqliteSaver | None = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, '1' if default else '0')).strip().lower()
    return raw in {'1', 'true', 'yes', 'on'}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(minimum, value)


def _ensure_db_parent(path: Path) -> None:
    parent = path.parent
    if str(parent) and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def _db_total_bytes(path: Path) -> int:
    total = 0
    for candidate in (path, path.with_name(f'{path.name}-wal'), path.with_name(f'{path.name}-shm')):
        if candidate.exists() and candidate.is_file():
            try:
                total += candidate.stat().st_size
            except OSError:
                continue
    return total


def checkpoint_db_stats() -> dict[str, Any]:
    return {
        'path': str(DB_PATH),
        'exists': DB_PATH.exists(),
        'total_bytes': _db_total_bytes(DB_PATH),
    }


def _maintain_checkpoint_db_if_needed(path: Path) -> None:
    if not _env_bool('WORKFLOW_CHECKPOINT_AUTO_MAINTAIN', default=True):
        return
    if not path.exists() or not path.is_file():
        return

    threshold_mb = _env_int('WORKFLOW_CHECKPOINT_COMPACT_THRESHOLD_MB', default=512, minimum=1)
    threshold_bytes = threshold_mb * 1024 * 1024
    before_bytes = _db_total_bytes(path)
    if before_bytes < threshold_bytes:
        return

    try:
        conn = sqlite3.connect(path, timeout=30)
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.execute('VACUUM')
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning('checkpoint.maintenance.failed path=%s error=%s', path, exc)
        return

    after_bytes = _db_total_bytes(path)
    logger.info(
        'checkpoint.maintenance.completed path=%s before_bytes=%s after_bytes=%s threshold_mb=%s',
        path,
        before_bytes,
        after_bytes,
        threshold_mb,
    )


def get_checkpointer() -> SqliteSaver:
    global _CONN, _CHECKPOINTER
    if _CHECKPOINTER is None:
        _ensure_db_parent(DB_PATH)
        _maintain_checkpoint_db_if_needed(DB_PATH)
        _CONN = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        _CONN.execute('PRAGMA journal_mode=WAL')
        _CONN.execute('PRAGMA synchronous=NORMAL')
        _CHECKPOINTER = SqliteSaver(_CONN)
    return _CHECKPOINTER


def get_latest_checkpoint(thread_id: str) -> dict[str, Any] | None:
    config = {'configurable': {'thread_id': thread_id}}
    checkpoint_tuple = get_checkpointer().get_tuple(config)
    if checkpoint_tuple is None:
        return None
    values = checkpoint_tuple.checkpoint.get('channel_values', {})
    return values if isinstance(values, dict) else None
