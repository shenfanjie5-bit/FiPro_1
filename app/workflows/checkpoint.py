from __future__ import annotations

import json
import sqlite3
from pathlib import Path


DB_PATH = Path('checkpoint.db')


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'create table if not exists workflow_checkpoints ('
        'thread_id text not null, '
        'step text not null, '
        'state_json text not null, '
        'created_at datetime default current_timestamp'
        ')'
    )
    return conn


def save_checkpoint(thread_id: str, step: str, state: dict) -> None:
    conn = _conn()
    try:
        conn.execute(
            'insert into workflow_checkpoints(thread_id, step, state_json) values (?, ?, ?)',
            (thread_id, step, json.dumps(state, ensure_ascii=True, default=str)),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_checkpoint(thread_id: str) -> dict | None:
    conn = _conn()
    try:
        row = conn.execute(
            'select state_json from workflow_checkpoints where thread_id = ? order by rowid desc limit 1',
            (thread_id,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row[0])
    finally:
        conn.close()
