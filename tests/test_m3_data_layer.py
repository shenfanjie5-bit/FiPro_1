from __future__ import annotations

from datetime import datetime, timezone
import uuid

import app.tools.facts as facts_module
from app.tools.rag import reset_rag_runtime_state, search_event_docs
from app.workflows.graph import run_research_workflow
from app.workflows.persistence import get_report_artifact_counts


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_market_snapshot_success_has_traceability_and_quality(monkeypatch) -> None:
    facts_module.reset_facts_runtime_state()

    def fake_live(_: str, __: str) -> dict:
        return {
            'currency': 'CNY',
            'last_price': 123.45,
            'close': 123.45,
            'returns': {'d1': 0.012, 'w1': 0.022, 'm1': 0.031},
            'volatility': {'atr_14': 1.2, 'stdev_20': 0.18},
            'volatility_20d': 0.18,
            'trend': {'ma_20': 120.1, 'ma_60': 118.4, 'regime': 'UP'},
            'liquidity': {'avg_turnover_20d': 1.4, 'spread_est': 0.002},
            'volume_ratio': 1.15,
            'captured_at': _now_iso(),
        }

    monkeypatch.setattr(facts_module, '_fetch_market_live', fake_live)
    snapshot = facts_module.get_market_snapshot('600519.SH', '2026-02-10T09:30:00+08:00')

    assert snapshot['snapshot_id'].startswith('snap_market_')
    assert snapshot['source'] == 'TUSHARE_PRO'
    assert snapshot['source_id']
    assert snapshot['checksum']
    assert snapshot['data_quality']['status'] == 'OK'
    assert snapshot['meta']['cache']['hit'] is False


def test_market_snapshot_has_upstream_params_digest(monkeypatch) -> None:
    facts_module.reset_facts_runtime_state()

    def fake_live(_: str, __: str) -> dict:
        return {
            'currency': 'CNY',
            'last_price': 110,
            'close': 110,
            'returns': {'d1': 0.01},
            'volatility': {'stdev_20': 0.16},
            'volatility_20d': 0.16,
            'trend': {'regime': 'UP'},
            'liquidity': {'avg_turnover_20d': 1.2, 'spread_est': 0.001},
            'volume_ratio': 1.1,
            '_upstream_trace': {
                'ts_code': '600519.SH',
                'endpoints': ['daily', 'daily_basic'],
                'params_digest': 'abcdef0123456789',
            },
            'captured_at': _now_iso(),
        }

    monkeypatch.setattr(facts_module, '_fetch_market_live', fake_live)
    snapshot = facts_module.get_market_snapshot('600519.SH', '2026-02-10T09:30:00+08:00')

    assert 'params_digest=' in snapshot['source_id']
    assert snapshot['meta']['upstream_trace']['params_digest'] == 'abcdef0123456789'


def test_market_snapshot_timeout_downgrades(monkeypatch) -> None:
    facts_module.reset_facts_runtime_state()

    def fake_timeout(_: str, __: str) -> dict:
        raise TimeoutError('simulated timeout')

    monkeypatch.setattr(facts_module, '_fetch_market_live', fake_timeout)
    snapshot = facts_module.get_market_snapshot('000001.SZ', '2026-02-10T09:30:00+08:00')

    assert snapshot['source'] in ('SYNTHETIC_FALLBACK', 'TUSHARE_PRO_CACHE')
    assert snapshot['data_quality']['status'] == 'DEGRADED'
    assert '__upstream__' in snapshot['data_quality']['missing_fields']
    assert 'UPSTREAM_TIMEOUT' in snapshot['data_quality']['notes']
    assert snapshot['meta']['upstream_error']['code'] == 'UPSTREAM_TIMEOUT'


def test_market_snapshot_missing_field_marks_partial(monkeypatch) -> None:
    facts_module.reset_facts_runtime_state()

    def fake_partial(_: str, __: str) -> dict:
        return {
            'currency': 'CNY',
            'last_price': 99.2,
            'close': 99.2,
            'returns': {'d1': 0.005},
            'volatility': {'stdev_20': 0.22},
            'volatility_20d': 0.22,
            'trend': {'regime': 'RANGE'},
            'liquidity': {'avg_turnover_20d': None, 'spread_est': 0.002},
            'volume_ratio': 0.98,
            'captured_at': _now_iso(),
        }

    monkeypatch.setattr(facts_module, '_fetch_market_live', fake_partial)
    snapshot = facts_module.get_market_snapshot('688001.SH', '2026-02-10T09:30:00+08:00')

    assert snapshot['data_quality']['status'] == 'PARTIAL'
    assert 'liquidity.avg_turnover_20d' in snapshot['data_quality']['missing_fields']


def test_market_snapshot_outlier_marks_degraded(monkeypatch) -> None:
    facts_module.reset_facts_runtime_state()

    def fake_outlier(_: str, __: str) -> dict:
        return {
            'currency': 'CNY',
            'last_price': -10.0,
            'close': -10.0,
            'returns': {'d1': 0.01},
            'volatility': {'stdev_20': 0.2},
            'volatility_20d': 0.2,
            'trend': {'regime': 'DOWN'},
            'liquidity': {'avg_turnover_20d': 1.2, 'spread_est': 0.002},
            'volume_ratio': 1.1,
            'captured_at': _now_iso(),
        }

    monkeypatch.setattr(facts_module, '_fetch_market_live', fake_outlier)
    snapshot = facts_module.get_market_snapshot('688002.SH', '2026-02-10T09:30:00+08:00')

    assert snapshot['data_quality']['status'] == 'DEGRADED'
    assert 'last_price' in snapshot['meta']['quality_metrics']['outlier_fields']


def test_market_snapshot_cache_hit(monkeypatch) -> None:
    facts_module.reset_facts_runtime_state()
    calls = {'count': 0}

    def fake_live(_: str, __: str) -> dict:
        calls['count'] += 1
        return {
            'currency': 'CNY',
            'last_price': 88.8,
            'close': 88.8,
            'returns': {'d1': 0.0},
            'volatility': {'stdev_20': 0.18},
            'volatility_20d': 0.18,
            'trend': {'regime': 'RANGE'},
            'liquidity': {'avg_turnover_20d': 1.1, 'spread_est': 0.001},
            'volume_ratio': 1.0,
            'captured_at': _now_iso(),
        }

    monkeypatch.setattr(facts_module, '_fetch_market_live', fake_live)
    first = facts_module.get_market_snapshot('600000.SH', '2026-02-10T09:30:00+08:00')
    second = facts_module.get_market_snapshot('600000.SH', '2026-02-10T09:30:00+08:00')

    assert calls['count'] == 1
    assert first['snapshot_id'] == second['snapshot_id']
    assert first['meta']['cache']['hit'] is False
    assert second['meta']['cache']['hit'] is True


def test_market_snapshot_id_is_deterministic_with_same_input(monkeypatch) -> None:
    facts_module.reset_facts_runtime_state()

    def fake_live(_: str, __: str) -> dict:
        return {
            'currency': 'CNY',
            'last_price': 100.0,
            'close': 100.0,
            'returns': {'d1': 0.01, 'w1': 0.02, 'm1': 0.03},
            'volatility': {'atr_14': 0.1, 'stdev_20': 0.2},
            'volatility_20d': 0.2,
            'trend': {'ma_20': 98, 'ma_60': 95, 'regime': 'UP'},
            'liquidity': {'avg_turnover_20d': 1.3, 'spread_est': 0.0015},
            'volume_ratio': 1.2,
            'captured_at': '2026-02-10T00:00:00+00:00',
        }

    monkeypatch.setattr(facts_module, '_fetch_market_live', fake_live)
    snapshot_a = facts_module.get_market_snapshot('600519.SH', '2026-02-10T09:30:00+08:00')

    facts_module.reset_facts_runtime_state()
    monkeypatch.setattr(facts_module, '_fetch_market_live', fake_live)
    snapshot_b = facts_module.get_market_snapshot('600519.SH', '2026-02-10T09:30:00+08:00')

    assert snapshot_a['snapshot_id'] == snapshot_b['snapshot_id']


def test_event_docs_adapter_ingest_and_cache() -> None:
    reset_rag_runtime_state()
    asof_range = {'start': '2026-02-01T00:00:00+00:00', 'end': '2026-02-10T00:00:00+00:00'}
    first = search_event_docs(query='茅台 供需', asof_range=asof_range, top_k=3)
    second = search_event_docs(query='茅台 供需', asof_range=asof_range, top_k=3)

    assert len(first['docs']) >= 1
    assert first['meta']['cache']['hit'] is False
    assert second['meta']['cache']['hit'] is True
    first_doc = first['docs'][0]
    assert first_doc['doc_id']
    assert first_doc['source']
    assert first_doc['checksum']
    assert first_doc['captured_at']


def test_tier1_workflow_includes_docs_and_snapshot_artifacts() -> None:
    facts_module.reset_facts_runtime_state()
    reset_rag_runtime_state()
    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER1',
        'run_mode': 'LIVE',
    }
    thread_id = f"thread_m3_{uuid.uuid4().hex[:8]}"
    result = run_research_workflow(request_data=payload, thread_id=thread_id)
    final_report = result['final_report']

    assert final_report['provenance']['snapshot_ids']
    assert any(ref.get('source', '').startswith('event_docs.') for ref in final_report['evidence_refs'])

    artifacts = get_report_artifact_counts(final_report['report_id'])
    assert artifacts['snapshots'] >= 3
