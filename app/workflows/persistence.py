from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid
from typing import Any


DB_PATH = Path(os.getenv('WORKFLOW_RUNTIME_DB', os.getenv('WORKFLOW_CHECKPOINT_DB', 'checkpoint.db')))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute(
        'create table if not exists reports ('
        'report_id text primary key, '
        'thread_id text not null, '
        'ticker text not null, '
        'asof text not null, '
        'strategy_version_id text not null, '
        'tier text not null, '
        'run_mode text not null, '
        'schema_version text not null, '
        'status text not null, '
        'report_json text not null, '
        'created_at text not null'
        ')'
    )
    conn.execute(
        'create table if not exists decision_logs ('
        'id text primary key, '
        'report_id text not null, '
        'ticker text not null, '
        'action text not null, '
        'score integer not null, '
        'confidence real not null, '
        'snapshot_ids_json text not null, '
        'model_primary text not null, '
        'model_reviewer text not null, '
        'cost_usd real not null, '
        'latency_ms integer not null, '
        'created_at text not null'
        ')'
    )
    conn.execute(
        'create table if not exists tool_traces ('
        'id text primary key, '
        'report_id text not null, '
        'tool_name text not null, '
        'input_digest text not null, '
        'latency_ms integer not null, '
        'cost_usd real not null, '
        'error_code text, '
        'ok integer not null, '
        'created_at text not null'
        ')'
    )
    conn.execute(
        'create table if not exists memory_notes ('
        'id text primary key, '
        'report_id text not null, '
        'ticker text not null, '
        'summary text not null, '
        'tags_json text not null, '
        'importance integer not null, '
        'created_at text not null'
        ')'
    )
    return conn


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def persist_workflow_state(state: dict[str, Any], thread_id: str) -> dict[str, str]:
    report = state['report_draft']
    report_id = report['report_id']
    now = datetime.now(timezone.utc).isoformat()
    provenance = report.get('provenance', {})
    model = provenance.get('model', {})
    tool_stats = provenance.get('tool_call_stats', {})
    memory = report.get('memory_update', {})
    memory_id = f"mem_{uuid.uuid4().hex[:12]}"
    decision_log_id = f"dlog_{uuid.uuid4().hex[:12]}"
    status = 'INVALID' if state.get('workflow_invalid') else 'DONE'

    conn = _conn()
    try:
        conn.execute(
            'insert into reports('
            'report_id, thread_id, ticker, asof, strategy_version_id, tier, run_mode, schema_version, status, report_json, created_at'
            ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'on conflict(report_id) do update set '
            'thread_id=excluded.thread_id, '
            'ticker=excluded.ticker, '
            'asof=excluded.asof, '
            'strategy_version_id=excluded.strategy_version_id, '
            'tier=excluded.tier, '
            'run_mode=excluded.run_mode, '
            'schema_version=excluded.schema_version, '
            'status=excluded.status, '
            'report_json=excluded.report_json, '
            'created_at=excluded.created_at',
            (
                report_id,
                thread_id,
                report['ticker'],
                report['asof'],
                report['strategy_version_id'],
                report['tier'],
                report['provenance'].get('run_mode', 'LIVE'),
                report['schema_version'],
                status,
                _to_json(report),
                now,
            ),
        )

        conn.execute(
            'insert into decision_logs('
            'id, report_id, ticker, action, score, confidence, snapshot_ids_json, model_primary, model_reviewer, cost_usd, latency_ms, created_at'
            ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'on conflict(id) do update set '
            'report_id=excluded.report_id, '
            'ticker=excluded.ticker, '
            'action=excluded.action, '
            'score=excluded.score, '
            'confidence=excluded.confidence, '
            'snapshot_ids_json=excluded.snapshot_ids_json, '
            'model_primary=excluded.model_primary, '
            'model_reviewer=excluded.model_reviewer, '
            'cost_usd=excluded.cost_usd, '
            'latency_ms=excluded.latency_ms, '
            'created_at=excluded.created_at',
            (
                decision_log_id,
                report_id,
                report['ticker'],
                report['decision']['action'],
                int(report['decision']['overall_score']),
                float(report['decision']['confidence']),
                _to_json(provenance.get('snapshot_ids', [])),
                model.get('primary', 'mock-primary'),
                model.get('reviewer', 'NONE'),
                float(tool_stats.get('cost_usd_est', 0)),
                int(tool_stats.get('latency_ms', 0)),
                now,
            ),
        )

        for trace in state.get('tool_traces', []):
            trace_id = trace.get('trace_id') or f"trace_{uuid.uuid4().hex[:12]}"
            conn.execute(
                'insert into tool_traces('
                'id, report_id, tool_name, input_digest, latency_ms, cost_usd, error_code, ok, created_at'
                ') values (?, ?, ?, ?, ?, ?, ?, ?, ?) '
                'on conflict(id) do update set '
                'report_id=excluded.report_id, '
                'tool_name=excluded.tool_name, '
                'input_digest=excluded.input_digest, '
                'latency_ms=excluded.latency_ms, '
                'cost_usd=excluded.cost_usd, '
                'error_code=excluded.error_code, '
                'ok=excluded.ok, '
                'created_at=excluded.created_at',
                (
                    trace_id,
                    report_id,
                    trace.get('tool_name', 'UNKNOWN'),
                    trace.get('input_digest', ''),
                    int(trace.get('latency_ms', 0)),
                    float(trace.get('cost_est', 0)),
                    trace.get('error_code'),
                    int(trace.get('error_code') is None),
                    now,
                ),
            )

        conn.execute(
            'insert into memory_notes('
            'id, report_id, ticker, summary, tags_json, importance, created_at'
            ') values (?, ?, ?, ?, ?, ?, ?) '
            'on conflict(id) do update set '
            'report_id=excluded.report_id, '
            'ticker=excluded.ticker, '
            'summary=excluded.summary, '
            'tags_json=excluded.tags_json, '
            'importance=excluded.importance, '
            'created_at=excluded.created_at',
            (
                memory_id,
                report_id,
                report['ticker'],
                memory.get('summary', ''),
                _to_json(memory.get('tags', [])),
                int(memory.get('importance', 50)),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        'report_id': report_id,
        'decision_log_id': decision_log_id,
        'memory_note_id': memory_id,
    }


def get_report(report_id: str) -> dict[str, Any] | None:
    conn = _conn()
    try:
        row = conn.execute('select report_json from reports where report_id = ?', (report_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return json.loads(row[0])


def get_report_artifact_counts(report_id: str) -> dict[str, int]:
    conn = _conn()
    try:
        decision_logs = conn.execute('select count(*) from decision_logs where report_id = ?', (report_id,)).fetchone()
        tool_traces = conn.execute('select count(*) from tool_traces where report_id = ?', (report_id,)).fetchone()
        memory_notes = conn.execute('select count(*) from memory_notes where report_id = ?', (report_id,)).fetchone()
    finally:
        conn.close()
    return {
        'decision_logs': int(decision_logs[0] if decision_logs else 0),
        'tool_traces': int(tool_traces[0] if tool_traces else 0),
        'memory_notes': int(memory_notes[0] if memory_notes else 0),
    }
