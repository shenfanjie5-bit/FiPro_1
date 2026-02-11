from pathlib import Path


SQL_PATH = Path('sql/001_init.sql')


def _sql_text() -> str:
    return SQL_PATH.read_text(encoding='utf-8').lower()


def _table_block(sql: str, table_name: str) -> str:
    marker = f'create table if not exists {table_name} ('
    start = sql.find(marker)
    assert start != -1, f'table not found: {table_name}'
    end = sql.find(');', start)
    assert end != -1, f'table block not closed: {table_name}'
    return sql[start:end]


def test_strategy_versions_has_weights_hash() -> None:
    sql = _sql_text()
    block = _table_block(sql, 'strategy_versions')
    assert 'weights_hash text not null' in block


def test_daily_snapshots_has_type_and_data_quality() -> None:
    sql = _sql_text()
    block = _table_block(sql, 'daily_snapshots')
    assert 'snapshot_type text not null' in block
    assert 'data_quality_json jsonb not null' in block


def test_reports_has_tier_and_run_mode() -> None:
    sql = _sql_text()
    block = _table_block(sql, 'reports')
    assert 'tier text not null' in block
    assert 'run_mode text not null' in block


def test_decision_logs_has_replay_and_cost_fields() -> None:
    sql = _sql_text()
    block = _table_block(sql, 'decision_logs')
    assert 'snapshot_ids text[] not null' in block
    assert 'model_primary text not null' in block
    assert 'model_reviewer text not null' in block
    assert 'cost_usd numeric(10,6) not null' in block
    assert 'latency_ms int not null' in block


def test_watchlist_has_status() -> None:
    sql = _sql_text()
    block = _table_block(sql, 'watchlist')
    assert 'status text not null' in block


def test_tool_traces_has_m6_retry_and_audit_fields() -> None:
    sql = _sql_text()
    block = _table_block(sql, 'tool_traces')
    assert 'degraded boolean not null' in block
    assert 'attempts int not null' in block
    assert 'retry_count int not null' in block
    assert 'retry_wait_ms int not null' in block
    assert 'rate_limited_wait_ms int not null' in block
    assert 'policy_version text not null' in block
