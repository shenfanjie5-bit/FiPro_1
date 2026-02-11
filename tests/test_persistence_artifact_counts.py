from __future__ import annotations

import importlib
import json


def test_sqlite_artifact_counts_use_report_snapshot_ids(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / 'runtime.db'
    monkeypatch.setenv('WORKFLOW_RUNTIME_DB', str(db_path))
    monkeypatch.setenv('WORKFLOW_CHECKPOINT_DB', str(db_path))
    monkeypatch.setenv('DATABASE_URL', 'sqlite+pysqlite:///:memory:')

    persistence = importlib.import_module('app.workflows.persistence')
    conn = persistence._sqlite_conn()
    try:
        report_id = 'report_test_001'
        report_json = {
            'report_id': report_id,
            'provenance': {'snapshot_ids': ['snap_a', 'snap_b']},
        }
        conn.execute(
            'insert into reports('
            'report_id, thread_id, ticker, asof, strategy_version_id, tier, run_mode, schema_version, status, report_json, created_at'
            ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                report_id,
                'thread_x',
                '600519.SH',
                '2026-02-10T01:30:00+00:00',
                'stg_v1',
                'TIER1',
                'LIVE',
                '0.1',
                'DONE',
                json.dumps(report_json, ensure_ascii=True, sort_keys=True),
                '2026-02-10T01:31:00+00:00',
            ),
        )
        conn.execute(
            'insert into decision_logs('
            'id, report_id, ticker, action, score, confidence, snapshot_ids_json, model_primary, model_reviewer, cost_usd, latency_ms, created_at'
            ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                'dlog_1',
                report_id,
                '600519.SH',
                'WATCH',
                55,
                0.5,
                '[]',
                'mock',
                'NONE',
                0.0,
                1,
                '2026-02-10T01:31:00+00:00',
            ),
        )
        conn.execute(
            'insert into tool_traces('
            'id, report_id, tool_name, input_digest, latency_ms, cost_usd, error_code, ok, created_at'
            ') values (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                'trace_1',
                report_id,
                'test_tool',
                'abc',
                1,
                0.0,
                '',
                1,
                '2026-02-10T01:31:00+00:00',
            ),
        )
        conn.execute(
            'insert into memory_notes('
            'id, report_id, ticker, summary, tags_json, importance, created_at'
            ') values (?, ?, ?, ?, ?, ?, ?)',
            (
                'mem_1',
                report_id,
                '600519.SH',
                'summary',
                '[]',
                50,
                '2026-02-10T01:31:00+00:00',
            ),
        )
        for snapshot_id in ('snap_a', 'snap_b', 'snap_extra'):
            conn.execute(
                'insert into daily_snapshots('
                'id, ticker, asof, strategy_version_id, snapshot_type, snapshot_json, data_quality_json, source, source_id, captured_at, created_at'
                ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    snapshot_id,
                    '600519.SH',
                    '2026-02-10T01:30:00+00:00',
                    'stg_v1',
                    'MARKET',
                    '{}',
                    '{}',
                    'TEST',
                    'test',
                    '2026-02-10T01:30:00+00:00',
                    '2026-02-10T01:31:00+00:00',
                ),
            )
        conn.commit()
    finally:
        conn.close()

    counts = persistence.get_report_artifact_counts(report_id)
    assert counts['decision_logs'] == 1
    assert counts['tool_traces'] == 1
    assert counts['memory_notes'] == 1
    assert counts['snapshots'] == 2
