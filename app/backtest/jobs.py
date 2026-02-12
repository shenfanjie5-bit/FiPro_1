from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any
import uuid

from app.backtest.batch import Runner, SnapshotLoader, run_batch_backtest


FINAL_JOB_STATUSES = {'COMPLETED', 'FAILED', 'CANCELLED'}


@dataclass
class BacktestJob:
    job_id: str
    payload: dict[str, Any]
    status: str
    created_at: str
    updated_at: str
    started_at: str = ''
    finished_at: str = ''
    cancel_requested: bool = False
    progress: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str = ''


_JOBS_LOCK = Lock()
_JOBS: dict[str, BacktestJob] = {}
_FUTURES: dict[str, Future[Any]] = {}
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='fipro-backtest')
_MAX_FINISHED_JOBS = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_progress() -> dict[str, Any]:
    return {
        'generated_points': 0,
        'processed_points': 0,
        'completed_runs': 0,
        'failed_runs': 0,
        'skipped_non_trading_runs': 0,
        'current_date': '',
        'last_outcome': '',
    }


def _job_view(job: BacktestJob) -> dict[str, Any]:
    return {
        'job_id': job.job_id,
        'status': job.status,
        'created_at': job.created_at,
        'updated_at': job.updated_at,
        'started_at': job.started_at,
        'finished_at': job.finished_at,
        'cancel_requested': job.cancel_requested,
        'progress': dict(job.progress),
        'result': job.result,
        'error': job.error,
    }


def _cleanup_finished_jobs_locked() -> None:
    finished = [job for job in _JOBS.values() if job.status in FINAL_JOB_STATUSES]
    if len(finished) <= _MAX_FINISHED_JOBS:
        return
    finished.sort(key=lambda item: item.updated_at)
    remove_count = len(finished) - _MAX_FINISHED_JOBS
    for job in finished[:remove_count]:
        _JOBS.pop(job.job_id, None)
        _FUTURES.pop(job.job_id, None)


def submit_backtest_job(
    payload: dict[str, Any],
    *,
    runner: Runner,
    snapshot_loader: SnapshotLoader,
    benchmark_loader: SnapshotLoader | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    job = BacktestJob(
        job_id=f'btjob_{uuid.uuid4().hex[:12]}',
        payload=dict(payload),
        status='PENDING',
        created_at=now,
        updated_at=now,
        progress=_new_progress(),
    )
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
        future = _EXECUTOR.submit(
            _run_job,
            job.job_id,
            runner,
            snapshot_loader,
            benchmark_loader,
        )
        _FUTURES[job.job_id] = future
        _cleanup_finished_jobs_locked()
        return _job_view(job)


def get_backtest_job(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        return _job_view(job)


def cancel_backtest_job(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        if job.status in FINAL_JOB_STATUSES:
            return _job_view(job)
        job.cancel_requested = True
        if job.status in {'PENDING', 'RUNNING'}:
            job.status = 'CANCELLING'
        job.updated_at = _now_iso()
        return _job_view(job)


def _run_job(
    job_id: str,
    runner: Runner,
    snapshot_loader: SnapshotLoader,
    benchmark_loader: SnapshotLoader | None,
) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.started_at = _now_iso()
        job.updated_at = job.started_at
        if job.status != 'CANCELLING':
            job.status = 'RUNNING'

    def _cancel_checker() -> bool:
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            if current is None:
                return True
            return bool(current.cancel_requested)

    def _progress_callback(update: dict[str, Any]) -> None:
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            if current is None:
                return
            if current.status not in {'CANCELLING'}:
                current.status = 'RUNNING'
            progress = dict(current.progress)
            for key in (
                'generated_points',
                'processed_points',
                'completed_runs',
                'failed_runs',
                'skipped_non_trading_runs',
                'current_date',
                'last_outcome',
            ):
                if key in update:
                    progress[key] = update[key]
            current.progress = progress
            current.updated_at = _now_iso()

    try:
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            if current is None:
                return
            job_payload = dict(current.payload)

        result = run_batch_backtest(
            job_payload,
            runner=runner,
            snapshot_loader=snapshot_loader,
            benchmark_loader=benchmark_loader,
            progress_callback=_progress_callback,
            cancel_requested=_cancel_checker,
        )
        summary = result.get('summary', {}) if isinstance(result, dict) else {}
        cancelled = bool(summary.get('cancelled'))
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            if current is None:
                return
            current.result = result
            current.finished_at = _now_iso()
            current.updated_at = current.finished_at
            current.status = 'CANCELLED' if cancelled else 'COMPLETED'
            current.error = ''
            current.progress = {
                'generated_points': int(result.get('request', {}).get('generated_points', 0)),
                'processed_points': int(summary.get('processed_points', 0)),
                'completed_runs': int(summary.get('completed_runs', 0)),
                'failed_runs': int(summary.get('failed_runs', 0)),
                'skipped_non_trading_runs': int(summary.get('skipped_non_trading_runs', 0)),
                'current_date': '',
                'last_outcome': 'CANCELLED' if cancelled else 'COMPLETED',
            }
            _cleanup_finished_jobs_locked()
    except Exception as exc:  # noqa: BLE001
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            if current is None:
                return
            current.status = 'FAILED'
            current.error = str(exc)
            current.finished_at = _now_iso()
            current.updated_at = current.finished_at
            current.progress['last_outcome'] = 'FAILED'
            _cleanup_finished_jobs_locked()
