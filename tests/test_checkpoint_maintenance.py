from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

from app.workflows import checkpoint as checkpoint_module


def _reload_checkpoint_module(monkeypatch: pytest.MonkeyPatch, db_path: Path):
    monkeypatch.setenv('WORKFLOW_CHECKPOINT_DB', str(db_path))
    monkeypatch.setenv('WORKFLOW_CHECKPOINT_AUTO_MAINTAIN', '1')
    monkeypatch.setenv('WORKFLOW_CHECKPOINT_COMPACT_THRESHOLD_MB', '1')
    if checkpoint_module._CONN is not None:
        checkpoint_module._CONN.close()
    reloaded = importlib.reload(checkpoint_module)
    return reloaded


def test_checkpoint_auto_maintenance_triggers_on_large_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / 'checkpoint.db'
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, payload TEXT)')
    big_payload = 'x' * 2048
    conn.executemany('INSERT INTO t(payload) VALUES (?)', [(big_payload,) for _ in range(2500)])
    conn.commit()
    conn.execute('DELETE FROM t')
    conn.commit()
    conn.close()

    module = _reload_checkpoint_module(monkeypatch, db_path)
    size_before = module.checkpoint_db_stats()['total_bytes']
    module.get_checkpointer()
    size_after = module.checkpoint_db_stats()['total_bytes']

    assert size_before > 0
    assert size_after <= size_before
    if module._CONN is not None:
        module._CONN.close()
        module._CONN = None
        module._CHECKPOINTER = None
