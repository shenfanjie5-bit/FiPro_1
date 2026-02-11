from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver


DB_PATH = Path(os.getenv('WORKFLOW_CHECKPOINT_DB', 'checkpoint.db'))
_CONN: sqlite3.Connection | None = None
_CHECKPOINTER: SqliteSaver | None = None

def get_checkpointer() -> SqliteSaver:
    global _CONN, _CHECKPOINTER
    if _CHECKPOINTER is None:
        _CONN = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        _CHECKPOINTER = SqliteSaver(_CONN)
    return _CHECKPOINTER


def get_latest_checkpoint(thread_id: str) -> dict[str, Any] | None:
    config = {'configurable': {'thread_id': thread_id}}
    checkpoint_tuple = get_checkpointer().get_tuple(config)
    if checkpoint_tuple is None:
        return None
    values = checkpoint_tuple.checkpoint.get('channel_values', {})
    return values if isinstance(values, dict) else None
