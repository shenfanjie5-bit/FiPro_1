from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid
from typing import Any


def _runtime_db_path() -> Path:
    return Path(os.getenv('WORKFLOW_RUNTIME_DB', os.getenv('WORKFLOW_CHECKPOINT_DB', 'checkpoint.db')))


def _database_url() -> str:
    return os.getenv('DATABASE_URL', '').strip()


def _use_postgres_primary() -> bool:
    return _database_url().startswith('postgresql')


def _parse_iso_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _uuid_or_namespace(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return uuid.uuid5(uuid.NAMESPACE_URL, str(value))


def _ensure_sqlite_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    rows = conn.execute(f'pragma table_info({table})').fetchall()
    existing = {str(row[1]) for row in rows}
    if column in existing:
        return
    conn.execute(f'alter table {table} add column {column} {ddl}')


def _sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_runtime_db_path(), timeout=30)
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
        'degraded integer not null default 0, '
        'attempts integer not null default 1, '
        'retry_count integer not null default 0, '
        'retry_wait_ms integer not null default 0, '
        'rate_limited_wait_ms integer not null default 0, '
        'policy_version text not null default \'tool_wrapper_m6_v1\', '
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
    conn.execute(
        'create table if not exists daily_snapshots ('
        'id text primary key, '
        'ticker text not null, '
        'asof text not null, '
        'strategy_version_id text not null, '
        'snapshot_type text not null, '
        'snapshot_json text not null, '
        'data_quality_json text not null, '
        'source text not null, '
        'source_id text not null, '
        'captured_at text not null, '
        'created_at text not null'
        ')'
    )
    conn.execute('create index if not exists idx_daily_snapshots_ticker_asof on daily_snapshots(ticker, asof desc)')
    conn.execute(
        'create table if not exists event_docs ('
        'doc_id text primary key, '
        'query text not null, '
        'title text not null, '
        'source text not null, '
        'published_at text not null, '
        'captured_at text not null, '
        'uri text, '
        'snippet text not null, '
        'checksum text not null, '
        'created_at text not null'
        ')'
    )
    conn.execute('create index if not exists idx_event_docs_query_published on event_docs(query, published_at desc)')
    _ensure_sqlite_column(conn, 'tool_traces', 'degraded', 'integer not null default 0')
    _ensure_sqlite_column(conn, 'tool_traces', 'attempts', 'integer not null default 1')
    _ensure_sqlite_column(conn, 'tool_traces', 'retry_count', 'integer not null default 0')
    _ensure_sqlite_column(conn, 'tool_traces', 'retry_wait_ms', 'integer not null default 0')
    _ensure_sqlite_column(conn, 'tool_traces', 'rate_limited_wait_ms', 'integer not null default 0')
    _ensure_sqlite_column(conn, 'tool_traces', 'policy_version', "text not null default 'tool_wrapper_m6_v1'")
    return conn


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def _extract_snapshot_ids_from_report(report_json: dict[str, Any] | None) -> list[str]:
    if not isinstance(report_json, dict):
        return []
    provenance = report_json.get('provenance', {})
    if not isinstance(provenance, dict):
        return []
    raw_ids = provenance.get('snapshot_ids', [])
    if not isinstance(raw_ids, list):
        return []
    snapshot_ids: list[str] = []
    for item in raw_ids:
        value = str(item).strip()
        if value and value not in snapshot_ids:
            snapshot_ids.append(value)
    return snapshot_ids


def _persist_sqlite_workflow_state(state: dict[str, Any], thread_id: str) -> dict[str, str]:
    report = state['report_draft']
    report_id = report['report_id']
    now = datetime.now(timezone.utc).isoformat()
    provenance = report.get('provenance', {})
    model = provenance.get('model', {})
    tool_stats = provenance.get('tool_call_stats', {})
    memory = report.get('memory_update', {})
    memory_id = str(uuid.uuid4())
    decision_log_id = str(uuid.uuid4())
    status = 'FAILED' if state.get('workflow_invalid') else 'DONE'
    snapshots_persisted = 0
    event_docs_persisted = 0

    conn = _sqlite_conn()
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
            trace_id = trace.get('trace_id') or str(uuid.uuid4())
            conn.execute(
                'insert into tool_traces('
                'id, report_id, tool_name, input_digest, latency_ms, cost_usd, error_code, ok, degraded, attempts, retry_count, retry_wait_ms, rate_limited_wait_ms, policy_version, created_at'
                ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                'on conflict(id) do update set '
                'report_id=excluded.report_id, '
                'tool_name=excluded.tool_name, '
                'input_digest=excluded.input_digest, '
                'latency_ms=excluded.latency_ms, '
                'cost_usd=excluded.cost_usd, '
                'error_code=excluded.error_code, '
                'ok=excluded.ok, '
                'degraded=excluded.degraded, '
                'attempts=excluded.attempts, '
                'retry_count=excluded.retry_count, '
                'retry_wait_ms=excluded.retry_wait_ms, '
                'rate_limited_wait_ms=excluded.rate_limited_wait_ms, '
                'policy_version=excluded.policy_version, '
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
                    int(bool(trace.get('degraded', False))),
                    int(trace.get('attempts', 1)),
                    int(trace.get('retry_count', 0)),
                    int(trace.get('retry_wait_ms', 0)),
                    int(trace.get('rate_limited_wait_ms', 0)),
                    str(trace.get('policy_version', 'tool_wrapper_m6_v1')),
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

        for snapshot in state.get('snapshots', {}).values():
            snapshot_id = snapshot.get('snapshot_id')
            if not snapshot_id:
                continue
            conn.execute(
                'insert into daily_snapshots('
                'id, ticker, asof, strategy_version_id, snapshot_type, snapshot_json, data_quality_json, source, source_id, captured_at, created_at'
                ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                'on conflict(id) do update set '
                'ticker=excluded.ticker, '
                'asof=excluded.asof, '
                'strategy_version_id=excluded.strategy_version_id, '
                'snapshot_type=excluded.snapshot_type, '
                'snapshot_json=excluded.snapshot_json, '
                'data_quality_json=excluded.data_quality_json, '
                'source=excluded.source, '
                'source_id=excluded.source_id, '
                'captured_at=excluded.captured_at, '
                'created_at=excluded.created_at',
                (
                    snapshot_id,
                    report['ticker'],
                    report['asof'],
                    report['strategy_version_id'],
                    snapshot.get('snapshot_type', 'FACTS'),
                    _to_json(snapshot),
                    _to_json(snapshot.get('data_quality', {'status': 'DEGRADED', 'missing_fields': [], 'notes': ''})),
                    str(snapshot.get('source', 'UNKNOWN')),
                    str(snapshot.get('source_id', 'UNKNOWN')),
                    str(snapshot.get('captured_at', now)),
                    now,
                ),
            )
            snapshots_persisted += 1

        for doc in state.get('event_docs', []):
            doc_id = doc.get('doc_id')
            if not doc_id:
                continue
            conn.execute(
                'insert into event_docs('
                'doc_id, query, title, source, published_at, captured_at, uri, snippet, checksum, created_at'
                ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                'on conflict(doc_id) do update set '
                'query=excluded.query, '
                'title=excluded.title, '
                'source=excluded.source, '
                'published_at=excluded.published_at, '
                'captured_at=excluded.captured_at, '
                'uri=excluded.uri, '
                'snippet=excluded.snippet, '
                'checksum=excluded.checksum, '
                'created_at=excluded.created_at',
                (
                    doc_id,
                    str(doc.get('query', '')),
                    str(doc.get('title', '')),
                    str(doc.get('source', 'UNKNOWN')),
                    str(doc.get('published_at', now)),
                    str(doc.get('captured_at', now)),
                    doc.get('uri'),
                    str(doc.get('snippet', '')),
                    str(doc.get('checksum', '')),
                    now,
                ),
            )
            event_docs_persisted += 1
        conn.commit()
    finally:
        conn.close()

    return {
        'report_id': report_id,
        'decision_log_id': decision_log_id,
        'memory_note_id': memory_id,
        'snapshots_persisted': str(snapshots_persisted),
        'event_docs_persisted': str(event_docs_persisted),
    }


def _persist_postgres_workflow_state(state: dict[str, Any], thread_id: str) -> dict[str, str]:
    from app.db.models import DecisionLog, EventDoc, MemoryNote, Report, Snapshot, ToolTrace
    from app.db.session import SessionLocal

    report = state['report_draft']
    report_id = report['report_id']
    report_db_id = _uuid_or_namespace(report_id)
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    provenance = report.get('provenance', {})
    model = provenance.get('model', {})
    tool_stats = provenance.get('tool_call_stats', {})
    memory = report.get('memory_update', {})

    decision_log_id = uuid.uuid4()
    memory_note_id = uuid.uuid4()
    snapshots_persisted = 0
    event_docs_persisted = 0

    session = SessionLocal()
    try:
        report_row = session.get(Report, report_db_id) or Report(id=report_db_id)
        report_row.ticker = report['ticker']
        report_row.asof = _parse_iso_datetime(report['asof'])
        report_row.strategy_version_id = report['strategy_version_id']
        report_row.tier = report['tier']
        report_row.run_mode = report['provenance'].get('run_mode', 'LIVE')
        report_row.schema_version = report['schema_version']
        report_row.status = 'FAILED' if state.get('workflow_invalid') else 'DONE'
        report_row.report_json = report
        report_row.created_at = now_dt
        session.add(report_row)
        session.flush()

        session.add(
            DecisionLog(
                id=decision_log_id,
                report_id=report_db_id,
                ticker=report['ticker'],
                action=report['decision']['action'],
                score=int(report['decision']['overall_score']),
                confidence=float(report['decision']['confidence']),
                snapshot_ids=list(provenance.get('snapshot_ids', [])),
                model_primary=model.get('primary', 'mock-primary'),
                model_reviewer=model.get('reviewer', 'NONE'),
                cost_usd=float(tool_stats.get('cost_usd_est', 0)),
                latency_ms=int(tool_stats.get('latency_ms', 0)),
                created_at=now_dt,
            )
        )

        for trace in state.get('tool_traces', []):
            trace_id = _uuid_or_namespace(trace.get('trace_id') or str(uuid.uuid4()))
            session.add(
                ToolTrace(
                    id=trace_id,
                    report_id=report_db_id,
                    tool_name=trace.get('tool_name', 'UNKNOWN'),
                    input_digest=trace.get('input_digest', ''),
                    latency_ms=int(trace.get('latency_ms', 0)),
                    cost_usd=float(trace.get('cost_est', 0)),
                    error_code=trace.get('error_code') or '',
                    ok=trace.get('error_code') is None,
                    degraded=bool(trace.get('degraded', False)),
                    attempts=int(trace.get('attempts', 1)),
                    retry_count=int(trace.get('retry_count', 0)),
                    retry_wait_ms=int(trace.get('retry_wait_ms', 0)),
                    rate_limited_wait_ms=int(trace.get('rate_limited_wait_ms', 0)),
                    policy_version=str(trace.get('policy_version', 'tool_wrapper_m6_v1')),
                )
            )

        session.add(
            MemoryNote(
                id=memory_note_id,
                report_id=report_db_id,
                ticker=report['ticker'],
                summary=memory.get('summary', ''),
                content=memory.get('summary', ''),
                tags=list(memory.get('tags', [])),
                importance=int(memory.get('importance', 50)),
                links=[],
                created_at=now_dt,
            )
        )

        for snapshot in state.get('snapshots', {}).values():
            snapshot_id = snapshot.get('snapshot_id')
            if not snapshot_id:
                continue
            snapshot_db_id = _uuid_or_namespace(snapshot_id)
            snapshot_row = session.get(Snapshot, snapshot_db_id) or Snapshot(id=snapshot_db_id)
            snapshot_row.ticker = report['ticker']
            snapshot_row.asof = _parse_iso_datetime(snapshot.get('asof', report['asof']))
            snapshot_row.strategy_version_id = report['strategy_version_id']
            snapshot_row.snapshot_type = snapshot.get('snapshot_type', 'FACTS')
            snapshot_row.snapshot_json = snapshot
            snapshot_row.data_quality_json = snapshot.get('data_quality', {'status': 'DEGRADED', 'missing_fields': [], 'notes': ''})
            snapshot_row.created_at = now_dt
            session.add(snapshot_row)
            snapshots_persisted += 1

        for doc in state.get('event_docs', []):
            doc_id = str(doc.get('doc_id', '')).strip()
            if not doc_id:
                continue
            published_at_raw = doc.get('published_at') or now_iso
            captured_at_raw = doc.get('captured_at') or now_iso
            event_doc = session.get(EventDoc, doc_id) or EventDoc(doc_id=doc_id)
            event_doc.query = str(doc.get('query', ''))
            event_doc.title = str(doc.get('title', ''))
            event_doc.source = str(doc.get('source', 'UNKNOWN'))
            event_doc.published_at = _parse_iso_datetime(published_at_raw)
            event_doc.captured_at = _parse_iso_datetime(captured_at_raw)
            event_doc.uri = doc.get('uri')
            event_doc.snippet = str(doc.get('snippet', ''))
            event_doc.checksum = str(doc.get('checksum', ''))
            event_doc.created_at = now_dt
            session.add(event_doc)
            event_docs_persisted += 1

        session.commit()
    finally:
        session.close()

    return {
        'report_id': report_id,
        'decision_log_id': str(decision_log_id),
        'memory_note_id': str(memory_note_id),
        'snapshots_persisted': str(snapshots_persisted),
        'event_docs_persisted': str(event_docs_persisted),
        'thread_id': thread_id,
        'persisted_at': now_iso,
    }


def persist_workflow_state(state: dict[str, Any], thread_id: str) -> dict[str, str]:
    if _use_postgres_primary():
        return _persist_postgres_workflow_state(state, thread_id)
    return _persist_sqlite_workflow_state(state, thread_id)


def _get_report_sqlite(report_id: str) -> dict[str, Any] | None:
    conn = _sqlite_conn()
    try:
        row = conn.execute('select report_json from reports where report_id = ?', (report_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return json.loads(row[0])


def _get_report_postgres(report_id: str) -> dict[str, Any] | None:
    from app.db.models import Report
    from app.db.session import SessionLocal

    report_db_id = _uuid_or_namespace(report_id)
    session = SessionLocal()
    try:
        row = session.get(Report, report_db_id)
        if row is None:
            return None
        return dict(row.report_json or {})
    finally:
        session.close()


def get_report(report_id: str) -> dict[str, Any] | None:
    if _use_postgres_primary():
        return _get_report_postgres(report_id)
    return _get_report_sqlite(report_id)


def _get_report_artifact_counts_sqlite(report_id: str) -> dict[str, int]:
    conn = _sqlite_conn()
    try:
        report_row = conn.execute('select report_json from reports where report_id = ?', (report_id,)).fetchone()
        decision_logs = conn.execute('select count(*) from decision_logs where report_id = ?', (report_id,)).fetchone()
        tool_traces = conn.execute('select count(*) from tool_traces where report_id = ?', (report_id,)).fetchone()
        memory_notes = conn.execute('select count(*) from memory_notes where report_id = ?', (report_id,)).fetchone()
        snapshot_ids: list[str] = []
        if report_row and report_row[0]:
            snapshot_ids = _extract_snapshot_ids_from_report(json.loads(report_row[0]))
        snapshots_count = 0
        if snapshot_ids:
            placeholders = ','.join('?' for _ in snapshot_ids)
            snapshots_row = conn.execute(
                f'select count(*) from daily_snapshots where id in ({placeholders})',
                tuple(snapshot_ids),
            ).fetchone()
            snapshots_count = int(snapshots_row[0] if snapshots_row else 0)
    finally:
        conn.close()
    return {
        'decision_logs': int(decision_logs[0] if decision_logs else 0),
        'tool_traces': int(tool_traces[0] if tool_traces else 0),
        'memory_notes': int(memory_notes[0] if memory_notes else 0),
        'snapshots': snapshots_count,
    }


def _get_report_artifact_counts_postgres(report_id: str) -> dict[str, int]:
    from sqlalchemy import select
    from app.db.models import DecisionLog, MemoryNote, Report, Snapshot, ToolTrace
    from app.db.session import SessionLocal

    report_db_id = _uuid_or_namespace(report_id)
    session = SessionLocal()
    try:
        report_row = session.get(Report, report_db_id)
        if report_row is None:
            return {'decision_logs': 0, 'tool_traces': 0, 'memory_notes': 0, 'snapshots': 0}

        decision_logs = session.execute(select(DecisionLog).where(DecisionLog.report_id == report_db_id)).all()
        tool_traces = session.execute(select(ToolTrace).where(ToolTrace.report_id == report_db_id)).all()
        memory_notes = session.execute(select(MemoryNote).where(MemoryNote.report_id == report_db_id)).all()
        snapshot_ids = _extract_snapshot_ids_from_report(report_row.report_json or {})
        snapshots = []
        if snapshot_ids:
            snapshot_db_ids = [_uuid_or_namespace(snapshot_id) for snapshot_id in snapshot_ids]
            snapshots = session.execute(select(Snapshot).where(Snapshot.id.in_(snapshot_db_ids))).all()
        return {
            'decision_logs': len(decision_logs),
            'tool_traces': len(tool_traces),
            'memory_notes': len(memory_notes),
            'snapshots': len(snapshots),
        }
    finally:
        session.close()


def get_report_artifact_counts(report_id: str) -> dict[str, int]:
    if _use_postgres_primary():
        return _get_report_artifact_counts_postgres(report_id)
    return _get_report_artifact_counts_sqlite(report_id)


def _get_latest_snapshot_sqlite(ticker: str, snapshot_type: str | None = None) -> dict[str, Any] | None:
    conn = _sqlite_conn()
    try:
        if snapshot_type:
            row = conn.execute(
                'select snapshot_json from daily_snapshots where ticker = ? and snapshot_type = ? order by asof desc limit 1',
                (ticker, snapshot_type),
            ).fetchone()
        else:
            row = conn.execute(
                'select snapshot_json from daily_snapshots where ticker = ? order by asof desc limit 1',
                (ticker,),
            ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return json.loads(row[0])


def _get_latest_snapshot_postgres(ticker: str, snapshot_type: str | None = None) -> dict[str, Any] | None:
    from sqlalchemy import desc, select
    from app.db.models import Snapshot
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        stmt = select(Snapshot).where(Snapshot.ticker == ticker)
        if snapshot_type:
            stmt = stmt.where(Snapshot.snapshot_type == snapshot_type)
        stmt = stmt.order_by(desc(Snapshot.asof)).limit(1)
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return dict(row.snapshot_json or {})
    finally:
        session.close()


def get_latest_snapshot(ticker: str, snapshot_type: str | None = None) -> dict[str, Any] | None:
    if _use_postgres_primary():
        return _get_latest_snapshot_postgres(ticker, snapshot_type)
    return _get_latest_snapshot_sqlite(ticker, snapshot_type)
