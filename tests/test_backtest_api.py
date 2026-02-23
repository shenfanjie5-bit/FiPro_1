from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient
import pytest

import app.backtest.batch as batch_module
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
    assert 'strategy_gross_total_return_pct' in body['summary']
    assert body['summary']['strategy_gross_final_capital_cny'] >= body['summary']['strategy_final_capital_cny']
    assert body['summary']['total_trade_cost_cny'] >= 0
    assert 'transaction_cost_model' in body['request']
    assert 'transaction_cost_model' in body['equity_curve']
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


def test_batch_backtest_prefers_model_defined_horizon_days(monkeypatch: pytest.MonkeyPatch) -> None:
    call_index = {'value': 0}
    horizon_days = [1, 7, 30]

    def fake_runner(request_data: dict, thread_id: str) -> dict:
        _ = request_data
        _ = thread_id
        call_index['value'] += 1
        idx = call_index['value'] - 1
        return {
            'final_report': {
                'report_id': f'report_horizon_{idx:03d}',
                'decision': {
                    'action': 'WATCH',
                    'overall_score': 66,
                    'confidence': 0.66,
                    'summary': 'model proposes horizon',
                    'evaluation_horizon_days': horizon_days[idx],
                },
                'data_quality': {'status': 'OK'},
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
        'evaluation_horizon_days': 5,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert [item['evaluation_horizon_days_used'] for item in body['runs']] == horizon_days
    assert all(item['evaluation_horizon_source'] == 'decision.evaluation_horizon_days' for item in body['runs'])
    assert body['request']['evaluation_horizon_days_default'] == 5
    assert body['request']['evaluation_horizon_mode'] == 'model_first'
    assert body['summary']['min_evaluation_horizon_days'] == 1
    assert body['summary']['max_evaluation_horizon_days'] == 30
    assert body['summary']['evaluation_horizon_source_counts']['decision.evaluation_horizon_days'] == 3


def test_batch_backtest_maps_time_horizon_to_days(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_runner(request_data: dict, thread_id: str) -> dict:
        _ = request_data
        _ = thread_id
        return {
            'final_report': {
                'report_id': 'report_time_horizon_001',
                'decision': {
                    'action': 'WATCH',
                    'overall_score': 60,
                    'confidence': 0.5,
                    'summary': 'use long term horizon',
                    'time_horizon': 'LONG_TERM',
                },
                'data_quality': {'status': 'OK'},
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
        'end_date': '2026-02-02',
        'step_days': 1,
        'trading_days_only': True,
        'max_runs': 10,
        'evaluation_horizon_days': 5,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body['runs'][0]['evaluation_horizon_days_used'] == 60
    assert body['runs'][0]['evaluation_horizon_source'] == 'decision.time_horizon'


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


def test_batch_backtest_interrupts_and_returns_resume_checkpoint_when_main_chain_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_runner(request_data: dict, thread_id: str) -> dict:
        if str(request_data.get('asof', '')).startswith('2026-02-03'):
            raise RuntimeError('boom')
        return {
            'final_report': {
                'report_id': f'report_{thread_id[-4:]}',
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

    assert body['summary']['interrupted'] is True
    assert body['summary']['resumable'] is True
    assert 'boom' in body['summary']['interruption_reason']
    assert body['summary']['total_runs'] == 2
    assert body['summary']['completed_runs'] == 1
    assert body['summary']['failed_runs'] == 1
    assert body['summary']['processed_points'] == 1
    assert body['summary']['remaining_points'] == 2
    assert len(body['equity_curve']['strategy']) == 1
    assert len(body['runs']) == 1
    assert body['runs'][0]['status'] == 'COMPLETED'
    assert isinstance(body['resume_state'], dict)
    assert body['resume_state']['next_index'] == 1


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


def test_batch_backtest_resolves_champion_skill_pack_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_versions: list[str] = []

    def fake_runner(request_data: dict, thread_id: str) -> dict:
        _ = thread_id
        seen_versions.append(str(request_data.get('skill_pack_version', '')))
        return {
            'final_report': {
                'report_id': 'report_alias_001',
                'decision': {'action': 'WATCH', 'overall_score': 50, 'confidence': 0.5, 'summary': 'ok'},
                'data_quality': {'status': 'OK'},
                'provenance': {'model': {'primary': 'openclaw:main'}, 'tool_call_stats': {}},
            },
            'persist_refs': {},
        }

    monkeypatch.setattr(routes_module, 'run_research_workflow', fake_runner)
    monkeypatch.setattr(routes_module, 'get_market_snapshot', _fake_snapshot)
    monkeypatch.setattr(routes_module, 'get_index_market_snapshot', _fake_snapshot)
    monkeypatch.setattr(batch_module, 'resolve_champion_version', lambda skill_pack_id: '0.9.0')  # noqa: ARG005
    monkeypatch.setattr(
        batch_module,
        'load_skill_pack',
        lambda skill_pack_id, version: {  # noqa: ARG005
            'summary': {
                'skill_pack_id': skill_pack_id,
                'version': version,
                'market': 'CN_A',
                'status': 'champion',
                'factor_count': 1,
                'enabled_factor_count': 1,
                'zero_weight_factor_count': 0,
                'llm_mapping_count': 1,
            }
        },
    )

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'skill_pack_id': 'cn_a_core',
        'skill_pack_version': 'champion',
        'start_date': '2026-02-10',
        'end_date': '2026-02-11',
        'step_days': 1,
        'trading_days_only': True,
        'max_runs': 10,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body['request']['skill_pack']['version'] == '0.9.0'
    assert body['request']['skill_pack']['version_source'] == 'champion'
    assert seen_versions == ['0.9.0', '0.9.0']


def test_portfolio_backtest_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes_module,
        'run_portfolio_backtest',
        lambda payload, runner, snapshot_loader, benchmark_loader: {  # noqa: ARG005
            'portfolio_id': 'portfolio_bt_demo',
            'request': payload,
            'summary': {
                'component_count': 2,
                'strategy_total_return_pct': 2.5,
                'benchmark_total_return_pct': 1.1,
                'excess_return_pct': 1.4,
            },
            'equity_curve': {'strategy': [], 'benchmark': [], 'benchmark_ticker': '000300.SH', 'base_currency': 'CNY'},
            'components': [],
        },
    )

    payload = {
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-10',
        'end_date': '2026-02-11',
        'step_days': 1,
        'trading_days_only': True,
        'asof_time': '09:30',
        'timezone_offset': '+08:00',
        'max_runs': 10,
        'evaluation_horizon_days': 1,
        'initial_capital_cny': 1_000_000,
        'portfolio': [
            {'ticker': '600519.SH', 'weight': 0.6},
            {'ticker': '000001.SZ', 'weight': 0.4},
        ],
    }
    resp = client.post('/backtests/portfolio/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body['portfolio_id'] == 'portfolio_bt_demo'
    assert body['summary']['component_count'] == 2
    assert body['summary']['excess_return_pct'] == 1.4


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
    monkeypatch.setattr(
        routes_module,
        'resume_backtest_job',
        lambda job_id, runner, snapshot_loader, benchmark_loader: {**fake_job, 'job_id': 'btjob_demo_002', 'status': 'PENDING'} if job_id == 'btjob_demo_001' else None,
    )

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

    resume_resp = client.post('/backtests/jobs/btjob_demo_001/resume')
    assert resume_resp.status_code == 202
    assert resume_resp.json()['job_id'] == 'btjob_demo_002'

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
        'anti_overfit_evidence': {
            'train_window': {'start_date': '2018-01-01', 'end_date': '2023-12-31'},
            'validation_window': {'start_date': '2024-01-01', 'end_date': '2025-12-31'},
            'sensitivity': {'scenario_count': 8, 'pass_rate': 0.8, 'min_pass_rate': 0.7},
            'param_change_count': 2,
        },
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
    assert body['evaluation']['anti_overfit_evidence']['param_change_count'] == 2
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


def test_skill_pack_candidate_generate_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes_module,
        'generate_skill_pack_candidates',
        lambda **kwargs: {  # noqa: ARG005
            'skill_pack_id': 'cn_a_core',
            'base_version': '0.1.0',
            'calibration_version': '0.1.0',
            'profile_id': 'cn_a_core_calibration_v0_1_0',
            'generated_count': 2,
            'items': [
                {'version': '0.1.1', 'param_id': 'factor.weight.price.momentum_20d'},
                {'version': '0.1.2', 'param_id': 'factor.weight.flow.moneyflow_5d'},
            ],
            'dry_run': False,
        },
    )

    payload = {
        'skill_pack_id': 'cn_a_core',
        'base_version': '0.1.0',
        'calibration_version': '0.1.0',
        'max_candidates': 2,
        'author': 'qa',
        'param_ids': [],
        'dry_run': False,
    }
    resp = client.post('/skill-packs/candidates/generate', json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body['skill_pack_id'] == 'cn_a_core'
    assert body['generated_count'] == 2
    assert body['items'][0]['version'] == '0.1.1'


def test_skill_pack_llm_proposal_run_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes_module,
        'run_llm_skill_pack_proposal_cycle',
        lambda **kwargs: {  # noqa: ARG005
            'run_id': 'run_demo_001',
            'skill_pack_id': 'cn_a_core',
            'base_version': '0.1.0',
            'candidate_generation': {'generated_count': 2},
            'selected_candidate': {'candidate_version': '0.1.2'},
            'execution': None,
        },
    )

    payload = {
        'skill_pack_id': 'cn_a_core',
        'base_version': 'champion',
        'proposal_count': 2,
        'author': 'qa',
        'execute': False,
        'manual_approved': False,
        'anti_overfit_evidence': {},
        'dry_run': False,
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
    resp = client.post('/skill-packs/proposals/llm-run', json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body['run_id'] == 'run_demo_001'
    assert body['selected_candidate']['candidate_version'] == '0.1.2'


def test_skill_pack_llm_proposal_run_query_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_list_llm_proposal_runs(**kwargs):  # noqa: ANN003
        if kwargs.get('generated_after') == 'invalid-datetime':
            raise ValueError('generated_after must be valid ISO datetime')
        captured.update(kwargs)
        return {
            'total': 1,
            'limit': kwargs.get('limit', 50),
            'offset': kwargs.get('offset', 0),
            'summary': {
                'executed_runs': 0,
                'dry_run_runs': 0,
                'selected_decision_counts': {'BLOCK': 1},
                'avg_selected_excess_return_delta_pct': -0.2,
                'avg_selected_segment_win_rate': 0.42,
            },
            'items': [
                {
                    'run_id': 'run_demo_001',
                    'generated_at': '2026-02-14T00:00:00+00:00',
                    'skill_pack_id': 'cn_a_core',
                    'base_version': '0.1.0',
                    'proposal_count': 2,
                    'dry_run': False,
                    'selected_candidate_version': '0.1.2',
                    'selected_decision': 'BLOCK',
                    'selected_excess_return_delta_pct': -0.2,
                    'selected_segment_win_rate': 0.42,
                    'executed': False,
                }
            ],
        }

    monkeypatch.setattr(
        routes_module,
        'list_llm_proposal_runs',
        fake_list_llm_proposal_runs,
    )
    monkeypatch.setattr(
        routes_module,
        'get_llm_proposal_run',
        lambda run_id: {'run_id': run_id, 'skill_pack_id': 'cn_a_core'} if run_id == 'run_demo_001' else None,
    )

    list_resp = client.get(
        '/skill-packs/proposals/runs'
        '?skill_pack_id=cn_a_core&executed=false&dry_run=false'
        '&selected_decision=BLOCK&generated_after=2026-02-13T00:00:00Z'
        '&generated_before=2026-02-15T00:00:00Z'
    )
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body['total'] == 1
    assert list_body['items'][0]['run_id'] == 'run_demo_001'
    assert list_body['summary']['selected_decision_counts']['BLOCK'] == 1
    assert captured['skill_pack_id'] == 'cn_a_core'
    assert captured['executed'] is False
    assert captured['dry_run'] is False
    assert captured['selected_decision'] == 'BLOCK'

    invalid_time = client.get('/skill-packs/proposals/runs?generated_after=invalid-datetime')
    assert invalid_time.status_code == 422

    get_resp = client.get('/skill-packs/proposals/runs/run_demo_001')
    assert get_resp.status_code == 200
    assert get_resp.json()['skill_pack_id'] == 'cn_a_core'

    not_found = client.get('/skill-packs/proposals/runs/missing')
    assert not_found.status_code == 404


def test_skill_pack_champion_health_check_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes_module,
        'run_champion_health_check',
        lambda **kwargs: {  # noqa: ARG005
            'run_id': 'hc_demo_001',
            'skill_pack_id': 'cn_a_core',
            'champion_version': '0.1.4',
            'baseline_version': '0.1.3',
            'health_status': 'PASS',
            'evaluation': {'decision': 'ALLOW'},
            'rollback_execution': None,
        },
    )
    monkeypatch.setattr(
        routes_module,
        'list_champion_health_checks',
        lambda limit, offset: {  # noqa: ARG005
            'total': 1,
            'limit': limit,
            'offset': offset,
            'items': [
                {
                    'run_id': 'hc_demo_001',
                    'skill_pack_id': 'cn_a_core',
                    'health_status': 'PASS',
                }
            ],
        },
    )
    monkeypatch.setattr(
        routes_module,
        'get_champion_health_check',
        lambda run_id: {'run_id': run_id, 'health_status': 'PASS'} if run_id == 'hc_demo_001' else None,
    )

    payload = {
        'skill_pack_id': 'cn_a_core',
        'champion_version': '0.1.4',
        'baseline_version': '0.1.3',
        'auto_rollback': False,
        'rollback_dry_run': True,
        'rollback_reason': 'monitoring_gate_block',
        'operator': 'monitor_engine',
        'manual_approved': True,
        'anti_overfit_evidence': {},
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

    run_resp = client.post('/skill-packs/champion/health-check', json=payload)
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    assert run_body['run_id'] == 'hc_demo_001'
    assert run_body['health_status'] == 'PASS'

    list_resp = client.get('/skill-packs/champion/health-checks')
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body['total'] == 1
    assert list_body['items'][0]['run_id'] == 'hc_demo_001'

    get_resp = client.get('/skill-packs/champion/health-checks/hc_demo_001')
    assert get_resp.status_code == 200
    assert get_resp.json()['health_status'] == 'PASS'

    not_found = client.get('/skill-packs/champion/health-checks/missing')
    assert not_found.status_code == 404


def test_skill_pack_release_event_query_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes_module,
        'list_release_events',
        lambda limit, offset: {  # noqa: ARG005
            'total': 1,
            'limit': limit,
            'offset': offset,
            'items': [
                {
                    'event_id': 'release_demo_001',
                    'skill_pack_id': 'cn_a_core',
                    'target_version': '0.1.4',
                    'switch_mode': 'promotion',
                    'executed': True,
                }
            ],
        },
    )
    monkeypatch.setattr(
        routes_module,
        'get_release_event',
        lambda event_id: {'event_id': event_id, 'executed': True} if event_id == 'release_demo_001' else None,
    )

    list_resp = client.get('/skill-packs/releases')
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body['total'] == 1
    assert list_body['items'][0]['event_id'] == 'release_demo_001'

    get_resp = client.get('/skill-packs/releases/release_demo_001')
    assert get_resp.status_code == 200
    assert get_resp.json()['executed'] is True

    not_found = client.get('/skill-packs/releases/missing')
    assert not_found.status_code == 404


def test_skill_pack_champion_watchdog_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run_champion_watchdog(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {
            'run_id': 'wd_demo_001',
            'overall_status': 'WARN',
            'alert_count': 1,
            'summary': {'latest_health_status': 'FAIL'},
            'rollback_recommendation': {'should_rollback': True, 'target_version': '0.1.3'},
            'rollback_execution': {'executed': False, 'dry_run': True},
        }

    monkeypatch.setattr(
        routes_module,
        'run_champion_watchdog',
        fake_run_champion_watchdog,
    )
    monkeypatch.setattr(
        routes_module,
        'list_champion_watchdog_runs',
        lambda limit, offset: {  # noqa: ARG005
            'total': 1,
            'limit': limit,
            'offset': offset,
            'items': [
                {
                    'run_id': 'wd_demo_001',
                    'overall_status': 'WARN',
                    'alert_count': 1,
                    'should_rollback': True,
                    'rollback_target_version': '0.1.3',
                }
            ],
        },
    )
    monkeypatch.setattr(
        routes_module,
        'get_champion_watchdog_run',
        lambda run_id: {'run_id': run_id, 'overall_status': 'WARN'} if run_id == 'wd_demo_001' else None,
    )

    payload = {
        'run_health_check': False,
        'health_check': {},
        'lookback_runs': 20,
        'consecutive_fail_critical': 2,
        'fail_rate_warn': 0.25,
        'fail_rate_critical': 0.5,
        'rollback_storm_critical': 2,
        'execute_rollback_on_recommendation': True,
        'rollback_dry_run': True,
        'rollback_reason': 'watchdog_recommendation',
        'rollback_operator': 'watchdog_engine',
    }

    run_resp = client.post('/skill-packs/champion/watchdog/run', json=payload)
    assert run_resp.status_code == 200
    assert run_resp.json()['run_id'] == 'wd_demo_001'
    assert captured['execute_rollback_on_recommendation'] is True
    assert captured['rollback_dry_run'] is True
    assert captured['rollback_reason'] == 'watchdog_recommendation'
    assert captured['rollback_operator'] == 'watchdog_engine'

    list_resp = client.get('/skill-packs/champion/watchdog/runs')
    assert list_resp.status_code == 200
    assert list_resp.json()['items'][0]['run_id'] == 'wd_demo_001'

    get_resp = client.get('/skill-packs/champion/watchdog/runs/wd_demo_001')
    assert get_resp.status_code == 200
    assert get_resp.json()['overall_status'] == 'WARN'

    not_found = client.get('/skill-packs/champion/watchdog/runs/missing')
    assert not_found.status_code == 404


def test_skill_pack_champion_switch_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes_module,
        'switch_skill_pack_champion',
        lambda **kwargs: {  # noqa: ARG005
            'executed': True,
            'dry_run': False,
            'skill_pack_id': 'cn_a_core',
            'target_version': '0.0.1',
            'champion_version_before': '0.1.0',
            'champion_version_after': '0.0.1',
            'archived_previous_champion': True,
            'reason': 'rollback_after_regression',
            'operator': 'qa_user',
            'switch_mode': 'manual',
        },
    )

    payload = {
        'target_version': '0.0.1',
        'reason': 'rollback_after_regression',
        'operator': 'qa_user',
        'dry_run': False,
    }
    resp = client.post('/skill-packs/cn_a_core/champion/switch', json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body['executed'] is True
    assert body['champion_version_before'] == '0.1.0'
    assert body['champion_version_after'] == '0.0.1'


def test_batch_backtest_propagates_ta_hybrid_params_and_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_request: dict = {}

    def fake_runner(request_data: dict, thread_id: str) -> dict:
        _ = thread_id
        captured_request.update(request_data)
        return {
            'final_report': {
                'report_id': 'report_ta_001',
                'decision': {
                    'action': 'WATCH',
                    'overall_score': 64,
                    'confidence': 0.61,
                    'summary': 'ta_hybrid analyzed',
                },
                'data_quality': {'status': 'OK'},
                'provenance': {
                    'model': {'primary': 'openclaw:main'},
                    'tool_call_stats': {'tool_calls': 11, 'cost_usd_est': 0.02, 'latency_ms': 220},
                    'ta_hybrid': {
                        'mode': 'ANALYZE_ONLY',
                        'status': 'ANALYZED',
                        'applied': False,
                        'directional_bias': 0.25,
                        'conviction': 0.58,
                        'disagreement': 0.12,
                        'llm_calls_used': 0,
                    },
                },
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
        'analysis_mode': 'TA_HYBRID',
        'ta_hybrid_mode': 'ANALYZE_ONLY',
        'ta_research_rounds': 1,
        'ta_risk_rounds': 1,
        'ta_llm_call_cap': 6,
        'ta_require_evidence_refs': True,
        'start_date': '2026-02-02',
        'end_date': '2026-02-02',
        'step_days': 1,
        'trading_days_only': True,
        'max_runs': 10,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert captured_request['analysis_mode'] == 'TA_HYBRID'
    assert captured_request['ta_hybrid_mode'] == 'ANALYZE_ONLY'
    assert captured_request['ta_research_rounds'] == 1
    assert captured_request['ta_risk_rounds'] == 1
    assert captured_request['ta_llm_call_cap'] == 6
    assert captured_request['ta_require_evidence_refs'] is True
    assert body['runs'][0]['ta_hybrid_mode'] == 'ANALYZE_ONLY'
    assert body['runs'][0]['ta_hybrid_status'] == 'ANALYZED'
    assert body['runs'][0]['ta_hybrid_applied'] is False
    assert body['summary']['ta_hybrid_applied_runs'] == 0
    assert 'avg_ta_directional_bias' in body['summary']


def test_batch_backtest_counts_ta_hybrid_applied_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_runner(request_data: dict, thread_id: str) -> dict:
        _ = request_data
        _ = thread_id
        return {
            'final_report': {
                'report_id': 'report_ta_blend_001',
                'decision': {
                    'action': 'WATCH',
                    'overall_score': 67,
                    'confidence': 0.6,
                    'summary': 'ta_hybrid blended',
                },
                'data_quality': {'status': 'OK'},
                'provenance': {
                    'model': {'primary': 'openclaw:main'},
                    'tool_call_stats': {'tool_calls': 12, 'cost_usd_est': 0.02, 'latency_ms': 210},
                    'ta_hybrid': {
                        'mode': 'BLEND',
                        'status': 'BLENDED',
                        'applied': True,
                        'directional_bias': 0.31,
                        'conviction': 0.66,
                        'disagreement': 0.09,
                        'llm_calls_used': 0,
                    },
                },
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
        'analysis_mode': 'TA_HYBRID',
        'ta_hybrid_mode': 'BLEND',
        'start_date': '2026-02-02',
        'end_date': '2026-02-02',
        'step_days': 1,
        'trading_days_only': True,
        'max_runs': 10,
    }
    resp = client.post('/backtests/run', json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert body['runs'][0]['ta_hybrid_mode'] == 'BLEND'
    assert body['runs'][0]['ta_hybrid_status'] == 'BLENDED'
    assert body['runs'][0]['ta_hybrid_applied'] is True
    assert body['summary']['ta_hybrid_applied_runs'] == 1
    assert body['summary']['ta_hybrid_applied_rate'] == 1.0
