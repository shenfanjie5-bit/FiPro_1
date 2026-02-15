from __future__ import annotations

from concurrent.futures import Future
import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.backtest import jobs as jobs_module


class _DummyExecutor:
    def submit(self, fn, *args, **kwargs):  # noqa: ANN001
        _ = fn
        _ = args
        _ = kwargs
        fut: Future[Any] = Future()
        fut.set_result(None)
        return fut


def _reload_jobs_module(monkeypatch: pytest.MonkeyPatch, state_file: Path):
    monkeypatch.setenv('BACKTEST_JOBS_STATE_FILE', str(state_file))
    try:
        jobs_module._EXECUTOR.shutdown(wait=False, cancel_futures=True)
    except Exception:  # noqa: BLE001
        pass
    reloaded = importlib.reload(jobs_module)
    return reloaded


def test_submit_backtest_job_persists_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / 'backtest_jobs_state.json'
    jobs = _reload_jobs_module(monkeypatch, state_file)
    monkeypatch.setattr(jobs, '_EXECUTOR', _DummyExecutor())

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
    }

    created = jobs.submit_backtest_job(
        payload,
        runner=lambda request_data, thread_id: {},  # noqa: ARG005
        snapshot_loader=lambda ticker, asof: {},  # noqa: ARG005
        benchmark_loader=None,
    )
    assert created['status'] == 'PENDING'
    assert state_file.exists()

    stored = json.loads(state_file.read_text(encoding='utf-8'))
    assert stored['total_jobs'] == 1
    assert isinstance(stored['items'], list)
    assert stored['items'][0]['job_id'] == created['job_id']
    assert stored['items'][0]['status'] == 'PENDING'


def test_restore_running_job_marks_interrupted_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / 'backtest_jobs_state.json'
    payload = {
        'generated_at': '2026-02-15T00:00:00+00:00',
        'total_jobs': 1,
        'items': [
            {
                'job_id': 'btjob_restore_001',
                'payload': {'ticker': '600519.SH'},
                'status': 'RUNNING',
                'created_at': '2026-02-15T00:00:00+00:00',
                'updated_at': '2026-02-15T00:01:00+00:00',
                'started_at': '2026-02-15T00:00:10+00:00',
                'finished_at': '',
                'cancel_requested': False,
                'progress': {
                    'generated_points': 10,
                    'processed_points': 2,
                    'completed_runs': 2,
                    'failed_runs': 0,
                    'skipped_non_trading_runs': 0,
                    'current_date': '2026-02-15',
                    'last_outcome': '',
                },
                'result': None,
                'error': '',
            }
        ],
    }
    state_file.write_text(json.dumps(payload, ensure_ascii=True), encoding='utf-8')

    jobs = _reload_jobs_module(monkeypatch, state_file)
    restored = jobs.get_backtest_job('btjob_restore_001')
    assert restored is not None
    assert restored['status'] == 'FAILED'
    assert restored['progress']['last_outcome'] == 'FAILED'
    assert 'interrupted by process restart' in restored['error']
