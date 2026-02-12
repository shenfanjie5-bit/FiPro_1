from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient
import pytest

from app.api import routes as routes_module
from app.main import app


client = TestClient(app)


def _fake_snapshot(*, ticker: str, asof: str) -> dict:
    dt = datetime.fromisoformat(asof)
    return {'ticker': ticker, 'close': float(dt.day)}


def test_batch_backtest_runs_multiple_points_and_uses_backtest_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_modes: list[str] = []
    call_index = {'value': 0}
    actions = ['BUY', 'WATCH', 'AVOID']

    def fake_runner(request_data: dict, thread_id: str) -> dict:
        call_index['value'] += 1
        idx = call_index['value']
        seen_modes.append(str(request_data.get('run_mode', '')))
        return {
            'final_report': {
                'report_id': f'report_{idx:03d}',
                'decision': {
                    'action': actions[idx - 1],
                    'overall_score': 60 + idx,
                    'confidence': 0.5 + (idx * 0.1),
                    'summary': 'batch backtest sample',
                },
                'data_quality': {'status': 'OK'},
                'provenance': {
                    'model': {'primary': 'openclaw:main'},
                    'tool_call_stats': {
                        'tool_calls': 10 + idx,
                        'cost_usd_est': 0.01 * idx,
                        'latency_ms': 120 * idx,
                    },
                },
            },
            'persist_refs': {'skill_note_id': f'skill_{idx:03d}'},
        }

    monkeypatch.setattr(routes_module, 'run_research_workflow', fake_runner)
    monkeypatch.setattr(routes_module, 'get_market_snapshot', _fake_snapshot)
    monkeypatch.setattr(routes_module, 'get_index_market_snapshot', _fake_snapshot)

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-02',
        'end_date': '2026-02-04',
        'step_days': 1,
        'trading_days_only': True,
        'asof_time': '09:30',
        'timezone_offset': '+08:00',
        'max_runs': 10,
        'evaluation_horizon_days': 1,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert seen_modes == ['BACKTEST', 'BACKTEST', 'BACKTEST']
    assert body['summary']['total_runs'] == 3
    assert body['summary']['completed_runs'] == 3
    assert body['summary']['failed_runs'] == 0
    assert body['summary']['action_counts'] == {'BUY': 1, 'WATCH': 1, 'AVOID': 1}
    assert body['summary']['evaluated_forward_runs'] == 3
    assert body['summary']['buy_signal_count'] == 1
    assert body['summary']['buy_hit_rate'] == 1.0
    assert body['summary']['avoid_signal_count'] == 1
    assert body['summary']['avoid_hit_rate'] == 0.0
    assert body['summary']['initial_capital_cny'] == 1_000_000
    assert body['summary']['strategy_final_capital_cny'] > 1_000_000
    assert body['summary']['benchmark_final_capital_cny'] > 1_000_000
    assert 'benchmark_total_return_pct' in body['summary']
    assert 'excess_return_pct' in body['summary']
    assert body['request']['skill_pack']['skill_pack_id'] == 'cn_a_core'
    assert body['request']['skill_pack']['version'] == '0.1.0'
    assert body['equity_curve']['base_currency'] == 'CNY'
    assert body['equity_curve']['benchmark_ticker'] == '000300.SH'
    assert len(body['equity_curve']['strategy']) == 3
    assert len(body['equity_curve']['benchmark']) == 3
    assert len(body['runs']) == 3
    assert body['runs'][0]['skill_note_id']
    assert body['runs'][0]['benchmark_ticker'] == '000300.SH'
    assert body['runs'][0]['skill_pack_id'] == 'cn_a_core'
    assert body['runs'][0]['skill_pack_version'] == '0.1.0'


def test_batch_backtest_rejects_when_generated_points_exceed_max_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_module, 'run_research_workflow', lambda request_data, thread_id: {})
    monkeypatch.setattr(routes_module, 'get_market_snapshot', _fake_snapshot)
    monkeypatch.setattr(routes_module, 'get_index_market_snapshot', _fake_snapshot)

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-02',
        'end_date': '2026-02-10',
        'step_days': 1,
        'trading_days_only': True,
        'max_runs': 2,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 422
    assert 'exceeds max_runs' in str(resp.json().get('detail', '')).lower()


def test_batch_backtest_rejects_invalid_skill_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes_module, 'run_research_workflow', lambda request_data, thread_id: {})
    monkeypatch.setattr(routes_module, 'get_market_snapshot', _fake_snapshot)
    monkeypatch.setattr(routes_module, 'get_index_market_snapshot', _fake_snapshot)

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'skill_pack_id': 'cn_a_core',
        'skill_pack_version': '9.9.9',
        'start_date': '2026-02-02',
        'end_date': '2026-02-03',
        'step_days': 1,
        'trading_days_only': True,
        'max_runs': 10,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 422
    assert 'invalid skill pack configuration' in str(resp.json().get('detail', '')).lower()


def test_batch_backtest_marks_failed_items_when_runner_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    call_index = {'value': 0}

    def fake_runner(request_data: dict, thread_id: str) -> dict:
        call_index['value'] += 1
        if call_index['value'] == 2:
            raise RuntimeError('boom')
        return {
            'final_report': {
                'report_id': f'report_{call_index["value"]:03d}',
                'decision': {'action': 'WATCH', 'overall_score': 55, 'confidence': 0.4, 'summary': 'ok'},
                'data_quality': {'status': 'PARTIAL'},
                'provenance': {'model': {'primary': 'openclaw:main'}, 'tool_call_stats': {}},
            },
            'persist_refs': {},
        }

    monkeypatch.setattr(routes_module, 'run_research_workflow', fake_runner)
    monkeypatch.setattr(routes_module, 'get_market_snapshot', _fake_snapshot)
    monkeypatch.setattr(routes_module, 'get_index_market_snapshot', _fake_snapshot)

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-02',
        'end_date': '2026-02-04',
        'step_days': 1,
        'trading_days_only': True,
        'max_runs': 10,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert body['summary']['total_runs'] == 3
    assert body['summary']['completed_runs'] == 2
    assert body['summary']['failed_runs'] == 1
    assert len(body['equity_curve']['strategy']) == 2
    failed = [item for item in body['runs'] if item['status'] == 'FAILED']
    assert len(failed) == 1
    assert 'boom' in failed[0]['error']


def test_batch_backtest_skips_non_trading_days_when_tushare_rows_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    executed_days: list[str] = []

    def fake_runner(request_data: dict, thread_id: str) -> dict:
        executed_days.append(str(request_data.get('asof', ''))[:10])
        return {
            'final_report': {
                'report_id': f'report_{thread_id[-4:]}',
                'decision': {'action': 'WATCH', 'overall_score': 55, 'confidence': 0.6, 'summary': 'ok'},
                'data_quality': {'status': 'OK'},
                'provenance': {'model': {'primary': 'openclaw:main'}, 'tool_call_stats': {}},
            },
            'persist_refs': {},
        }

    def fake_market_snapshot(*, ticker: str, asof: str) -> dict:
        dt = datetime.fromisoformat(asof)
        if dt.date().isoformat() == '2026-02-03':
            return {
                'ticker': ticker,
                'close': 100.0,
                'source': 'SYNTHETIC_FALLBACK',
                'meta': {
                    'upstream_error': {
                        'code': 'DATA_UNAVAILABLE',
                        'message': 'DATA_UNAVAILABLE: daily rows empty',
                    }
                },
            }
        return {'ticker': ticker, 'close': float(100 + dt.day), 'source': 'TUSHARE_PRO'}

    def fake_index_snapshot(*, ticker: str, asof: str) -> dict:
        dt = datetime.fromisoformat(asof)
        return {'ticker': ticker, 'close': float(200 + dt.day), 'source': 'TUSHARE_PRO'}

    monkeypatch.setattr(routes_module, 'run_research_workflow', fake_runner)
    monkeypatch.setattr(routes_module, 'get_market_snapshot', fake_market_snapshot)
    monkeypatch.setattr(routes_module, 'get_index_market_snapshot', fake_index_snapshot)

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-02',
        'end_date': '2026-02-04',
        'step_days': 1,
        'trading_days_only': True,
        'max_runs': 10,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert body['request']['generated_points'] == 3
    assert body['summary']['total_runs'] == 2
    assert body['summary']['completed_runs'] == 2
    assert body['summary']['failed_runs'] == 0
    assert body['summary']['skipped_non_trading_runs'] == 1
    assert body['summary']['skipped_non_trading_dates'] == ['2026-02-03']
    assert executed_days == ['2026-02-02', '2026-02-04']
    assert all('2026-02-03' not in str(item.get('asof', '')) for item in body['runs'])


def test_batch_backtest_uses_index_loader_for_benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    benchmark_calls: list[str] = []

    def fake_runner(request_data: dict, thread_id: str) -> dict:
        return {
            'final_report': {
                'report_id': f'report_{thread_id[-4:]}',
                'decision': {'action': 'WATCH', 'overall_score': 50, 'confidence': 0.5, 'summary': 'ok'},
                'data_quality': {'status': 'OK'},
                'provenance': {'model': {'primary': 'openclaw:main'}, 'tool_call_stats': {}},
            },
            'persist_refs': {},
        }

    def fake_market_snapshot(*, ticker: str, asof: str) -> dict:
        dt = datetime.fromisoformat(asof)
        return {'ticker': ticker, 'close': float(100 + dt.day)}

    def fake_index_snapshot(*, ticker: str, asof: str) -> dict:
        benchmark_calls.append(ticker)
        dt = datetime.fromisoformat(asof)
        return {'ticker': ticker, 'close': float(200 + dt.day)}

    monkeypatch.setattr(routes_module, 'run_research_workflow', fake_runner)
    monkeypatch.setattr(routes_module, 'get_market_snapshot', fake_market_snapshot)
    monkeypatch.setattr(routes_module, 'get_index_market_snapshot', fake_index_snapshot)

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-10',
        'end_date': '2026-02-11',
        'step_days': 1,
        'trading_days_only': True,
        'max_runs': 10,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert benchmark_calls
    assert set(benchmark_calls) == {'000300.SH'}
    assert body['runs'][0]['benchmark_price'] == 210.0
    assert body['runs'][1]['benchmark_price'] == 211.0


def test_backtest_job_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_job = {
        'job_id': 'btjob_demo_001',
        'status': 'RUNNING',
        'created_at': '2026-02-12T00:00:00+00:00',
        'updated_at': '2026-02-12T00:00:01+00:00',
        'started_at': '2026-02-12T00:00:01+00:00',
        'finished_at': '',
        'cancel_requested': False,
        'progress': {
            'generated_points': 10,
            'processed_points': 2,
            'completed_runs': 2,
            'failed_runs': 0,
            'skipped_non_trading_runs': 0,
            'current_date': '2026-02-11',
            'last_outcome': 'COMPLETED',
        },
        'result': None,
        'error': '',
    }

    monkeypatch.setattr(routes_module, 'submit_backtest_job', lambda payload, runner, snapshot_loader, benchmark_loader: fake_job)
    monkeypatch.setattr(routes_module, 'get_backtest_job', lambda job_id: fake_job if job_id == 'btjob_demo_001' else None)
    monkeypatch.setattr(routes_module, 'cancel_backtest_job', lambda job_id: {**fake_job, 'status': 'CANCELLING', 'cancel_requested': True} if job_id == 'btjob_demo_001' else None)

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-10',
        'end_date': '2026-02-11',
        'step_days': 1,
        'trading_days_only': True,
        'max_runs': 10,
    }

    create_resp = client.post('/backtests/jobs', json=payload)
    assert create_resp.status_code == 202
    assert create_resp.json()['job_id'] == 'btjob_demo_001'

    get_resp = client.get('/backtests/jobs/btjob_demo_001')
    assert get_resp.status_code == 200
    assert get_resp.json()['status'] == 'RUNNING'

    cancel_resp = client.post('/backtests/jobs/btjob_demo_001/cancel')
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()['status'] == 'CANCELLING'

    not_found_resp = client.get('/backtests/jobs/not_found')
    assert not_found_resp.status_code == 404


def test_skill_pack_promotion_run_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate_result = {
        'batch_id': 'bt_candidate',
        'request': {'skill_pack_version': '0.1.0'},
        'summary': {'strategy_total_return_pct': 12.0, 'excess_return_pct': 1.4},
    }
    champion_result = {
        'batch_id': 'bt_champion',
        'request': {'skill_pack_version': '0.0.1'},
        'summary': {'strategy_total_return_pct': 9.0, 'excess_return_pct': 0.4},
    }

    def fake_run_batch_backtest(payload: dict, runner, snapshot_loader, benchmark_loader):  # noqa: ANN001
        _ = runner
        _ = snapshot_loader
        _ = benchmark_loader
        if payload.get('skill_pack_version') == '0.1.0':
            return candidate_result
        return champion_result

    monkeypatch.setattr(routes_module, 'run_batch_backtest', fake_run_batch_backtest)
    monkeypatch.setattr(routes_module, 'resolve_champion_version', lambda skill_pack_id: '0.0.1')  # noqa: ARG005
    monkeypatch.setattr(routes_module, 'load_promotion_gate', lambda skill_pack_id, version: {'promotion_rule': {'all_of': []}})  # noqa: ARG005
    monkeypatch.setattr(
        routes_module,
        'evaluate_skill_pack_promotion',
        lambda **kwargs: {'decision': 'ALLOW', 'failed_checks': [], 'checks': [], **kwargs},  # noqa: ARG005
    )
    monkeypatch.setattr(
        routes_module,
        'execute_skill_pack_promotion',
        lambda **kwargs: {'executed': True, 'dry_run': kwargs.get('dry_run', False)},  # noqa: ARG005
    )

    payload = {
        'skill_pack_id': 'cn_a_core',
        'candidate_version': '0.1.0',
        'execute': True,
        'dry_run': False,
        'manual_approved': True,
        'backtest': {
            'ticker': '600519.SH',
            'market': 'CN_A',
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER0',
            'start_date': '2026-02-10',
            'end_date': '2026-02-11',
            'step_days': 1,
            'trading_days_only': True,
            'max_runs': 10,
        },
    }

    resp = client.post('/skill-packs/promotions/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body['skill_pack_id'] == 'cn_a_core'
    assert body['candidate_version'] == '0.1.0'
    assert body['champion_version'] == '0.0.1'
    assert body['evaluation']['decision'] == 'ALLOW'
    assert body['execution']['executed'] is True
    assert body['candidate_backtest']['batch_id'] == 'bt_candidate'
    assert body['champion_backtest']['batch_id'] == 'bt_champion'


def test_skill_pack_versions_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes_module,
        'list_skill_pack_versions',
        lambda skill_pack_id: [  # noqa: ARG005
            {'version': '0.1.0', 'status': 'candidate'},
            {'version': '0.0.1', 'status': 'champion'},
        ],
    )
    monkeypatch.setattr(routes_module, 'resolve_champion_version', lambda skill_pack_id: '0.0.1')  # noqa: ARG005

    resp = client.get('/skill-packs/cn_a_core/versions')
    assert resp.status_code == 200
    body = resp.json()
    assert body['skill_pack_id'] == 'cn_a_core'
    assert body['champion_version'] == '0.0.1'
    assert len(body['items']) == 2
