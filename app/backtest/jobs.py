from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any
import time
import uuid

from app.backtest.batch import Runner, SnapshotLoader, run_batch_backtest


FINAL_JOB_STATUSES = {'COMPLETED', 'FAILED', 'CANCELLED'}
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATE_FILE_RELATIVE = '.run/backtest_jobs_state.json'
_PERSIST_MIN_INTERVAL_SECONDS = 0.8


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
_LAST_PERSIST_TS = 0.0


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


def _state_file_path() -> Path:
    override = str(os.getenv('BACKTEST_JOBS_STATE_FILE', '')).strip()
    if override:
        return Path(override).expanduser().resolve()
    return (_PROJECT_ROOT / _STATE_FILE_RELATIVE).resolve()


def _serialize_job(job: BacktestJob) -> dict[str, Any]:
    return {
        'job_id': job.job_id,
        'payload': dict(job.payload),
        'status': str(job.status),
        'created_at': str(job.created_at),
        'updated_at': str(job.updated_at),
        'started_at': str(job.started_at),
        'finished_at': str(job.finished_at),
        'cancel_requested': bool(job.cancel_requested),
        'progress': dict(job.progress),
        'result': job.result,
        'error': str(job.error),
    }


def _deserialize_job(payload: dict[str, Any]) -> BacktestJob | None:
    try:
        job_id = str(payload.get('job_id', '')).strip()
        if not job_id:
            return None
        return BacktestJob(
            job_id=job_id,
            payload=dict(payload.get('payload', {})) if isinstance(payload.get('payload', {}), dict) else {},
            status=str(payload.get('status', 'FAILED')).strip().upper() or 'FAILED',
            created_at=str(payload.get('created_at', '')).strip() or _now_iso(),
            updated_at=str(payload.get('updated_at', '')).strip() or _now_iso(),
            started_at=str(payload.get('started_at', '')).strip(),
            finished_at=str(payload.get('finished_at', '')).strip(),
            cancel_requested=bool(payload.get('cancel_requested', False)),
            progress=dict(payload.get('progress', {})) if isinstance(payload.get('progress', {}), dict) else _new_progress(),
            result=payload.get('result') if isinstance(payload.get('result'), dict) else None,
            error=str(payload.get('error', '')).strip(),
        )
    except Exception:  # noqa: BLE001
        return None


def _normalize_restored_job(job: BacktestJob) -> BacktestJob:
    if job.status in {'PENDING', 'RUNNING', 'CANCELLING'}:
        job.status = 'FAILED'
        job.cancel_requested = False
        job.finished_at = _now_iso()
        job.updated_at = job.finished_at
        if not job.error:
            job.error = 'job interrupted by process restart'
        progress = dict(job.progress)
        progress['last_outcome'] = 'FAILED'
        job.progress = progress
    return job


def _persist_jobs_locked(*, force: bool = False) -> None:
    global _LAST_PERSIST_TS
    now = time.monotonic()
    if not force and (now - _LAST_PERSIST_TS) < _PERSIST_MIN_INTERVAL_SECONDS:
        return
    state_path = _state_file_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = [_serialize_job(job) for job in _JOBS.values()]
    payload = {
        'generated_at': _now_iso(),
        'total_jobs': len(serialized),
        'items': serialized,
    }
    tmp_path = state_path.with_suffix(f'{state_path.suffix}.tmp')
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True, separators=(',', ':'), default=str), encoding='utf-8')
    tmp_path.replace(state_path)
    _LAST_PERSIST_TS = now


def _load_jobs_from_disk() -> None:
    state_path = _state_file_path()
    if not state_path.exists() or not state_path.is_file():
        return
    try:
        raw = json.loads(state_path.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return
    items = raw.get('items', []) if isinstance(raw, dict) else []
    if not isinstance(items, list):
        return
    loaded: dict[str, BacktestJob] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        job = _deserialize_job(item)
        if job is None:
            continue
        loaded[job.job_id] = _normalize_restored_job(job)
    if not loaded:
        return
    with _JOBS_LOCK:
        _JOBS.clear()
        _JOBS.update(loaded)
        _cleanup_finished_jobs_locked()


def _resume_state_from_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    summary = result.get('summary')
    if not isinstance(summary, dict) or not bool(summary.get('resumable')):
        return None
    resume_state = result.get('resume_state')
    if not isinstance(resume_state, dict):
        return None
    return resume_state


def _job_view(job: BacktestJob) -> dict[str, Any]:
    resumable = _resume_state_from_result(job.result) is not None
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
        'resumable': resumable,
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
        _persist_jobs_locked(force=True)
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
        _persist_jobs_locked(force=True)
        return _job_view(job)


def resume_backtest_job(
    job_id: str,
    *,
    runner: Runner,
    snapshot_loader: SnapshotLoader,
    benchmark_loader: SnapshotLoader | None = None,
) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        if job.status != 'FAILED':
            raise ValueError('only failed jobs can be resumed')
        resume_state = _resume_state_from_result(job.result)
        if resume_state is None:
            raise ValueError('job has no resumable checkpoint')
        next_index = int(resume_state.get('next_index', 0))
        if next_index <= 0:
            raise ValueError('resume checkpoint is invalid')
        payload = dict(job.payload)
        payload['_resume_state'] = deepcopy(resume_state)

    resumed = submit_backtest_job(
        payload,
        runner=runner,
        snapshot_loader=snapshot_loader,
        benchmark_loader=benchmark_loader,
    )
    resumed['resumed_from_job_id'] = job_id
    return resumed


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
        _persist_jobs_locked(force=True)

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
            _persist_jobs_locked(force=False)

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
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            cancel_requested_now = bool(current.cancel_requested) if current is not None else False
        cancelled = bool(summary.get('cancelled')) or cancel_requested_now
        interrupted = bool(summary.get('interrupted'))
        interruption_reason = str(summary.get('interruption_reason', '')).strip()
        with _JOBS_LOCK:
            current = _JOBS.get(job_id)
            if current is None:
                return
            current.result = result
            current.finished_at = _now_iso()
            current.updated_at = current.finished_at
            if cancelled:
                current.status = 'CANCELLED'
                current.error = ''
                last_outcome = 'CANCELLED'
            elif interrupted:
                current.status = 'FAILED'
                current.error = interruption_reason or 'main chain unavailable after retries'
                last_outcome = 'FAILED'
            else:
                current.status = 'COMPLETED'
                current.error = ''
                last_outcome = 'COMPLETED'
            current.progress = {
                'generated_points': int(result.get('request', {}).get('generated_points', 0)),
                'processed_points': int(summary.get('processed_points', 0)),
                'completed_runs': int(summary.get('completed_runs', 0)),
                'failed_runs': int(summary.get('failed_runs', 0)),
                'skipped_non_trading_runs': int(summary.get('skipped_non_trading_runs', 0)),
                'current_date': '',
                'last_outcome': last_outcome,
            }
            _cleanup_finished_jobs_locked()
            _persist_jobs_locked(force=True)
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
            _persist_jobs_locked(force=True)


_load_jobs_from_disk()
