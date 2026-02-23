from __future__ import annotations

from typing import Any

from app.backtest.batch import run_batch_backtest


def _base_payload() -> dict[str, Any]:
    return {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-10',
        'end_date': '2026-02-10',
        'step_days': 1,
        'trading_days_only': True,
        'asof_time': '09:30',
        'timezone_offset': '+08:00',
        'max_runs': 10,
        'evaluation_horizon_days': 1,
    }


def _snapshot_loader(ticker: str, asof: str) -> dict[str, Any]:  # noqa: ARG001
    return {'snapshot_id': 'snap_demo', 'close': 100.0}


def test_batch_backtest_marks_cancelled_when_cancel_requested_during_single_run() -> None:
    payload = _base_payload()
    cancel_state = {'requested': False}
    call_count = {'runner': 0}

    def runner(request_data: dict[str, Any], thread_id: str) -> dict[str, Any]:  # noqa: ARG001
        call_count['runner'] += 1
        # Simulate user clicking cancel while OpenClaw/main-chain call is in progress.
        cancel_state['requested'] = True
        return {
            'final_report': {
                'report_id': f'report_{call_count["runner"]}',
                'decision': {'action': 'WATCH', 'overall_score': 55, 'confidence': 0.62},
                'data_quality': {'status': 'OK'},
                'provenance': {'model': {'primary': 'openclaw:main'}, 'tool_call_stats': {}},
            },
            'persist_refs': {},
        }

    result = run_batch_backtest(
        payload,
        runner=runner,
        snapshot_loader=_snapshot_loader,
        benchmark_loader=_snapshot_loader,
        cancel_requested=lambda: bool(cancel_state['requested']),
    )

    assert call_count['runner'] == 1
    assert result['summary']['cancelled'] is True
    assert result['summary']['interrupted'] is False
    assert result['summary']['completed_runs'] == 1
    assert result['summary']['failed_runs'] == 0
    assert result['resume_state'] is None
    assert len(result['runs']) == 1
    assert result['runs'][0]['status'] == 'COMPLETED'


def test_batch_backtest_cancels_during_retry_backoff_without_marking_interrupted() -> None:
    payload = {
        **_base_payload(),
        'end_date': '2026-02-11',
        'main_chain_retry_attempts': 3,
        'main_chain_retry_backoff_ms': 1200,
    }
    cancel_state = {'requested': False}
    call_count = {'runner': 0}

    def runner(request_data: dict[str, Any], thread_id: str) -> dict[str, Any]:  # noqa: ARG001
        call_count['runner'] += 1
        cancel_state['requested'] = True
        raise RuntimeError('openclaw upstream timeout')

    result = run_batch_backtest(
        payload,
        runner=runner,
        snapshot_loader=_snapshot_loader,
        benchmark_loader=_snapshot_loader,
        cancel_requested=lambda: bool(cancel_state['requested']),
    )

    assert call_count['runner'] == 1
    assert result['summary']['cancelled'] is True
    assert result['summary']['interrupted'] is False
    assert result['summary']['completed_runs'] == 0
    assert result['summary']['failed_runs'] == 0
    assert result['summary']['processed_points'] == 0
    assert result['resume_state'] is None
    assert result['runs'] == []
