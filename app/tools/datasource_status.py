from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from app.backtest import list_champion_watchdog_alerts, list_champion_watchdog_runs


DEFAULT_TUSHARE_ROOT = '/Volumes/dockcase2tb/database_all'
TUSHARE_STATUS_RELATIVE = '_meta/manifests/data_source_status/tushare.json'
TUSHARE_INCREMENTAL_REPORT_RELATIVE = '_meta/manifests/tushare_incremental_last_run.json'
TUSHARE_BULK_MANIFEST_RELATIVE = '_meta/manifests/tushare_bulk_download_manifest.json'

STATUS_COMPLETED = 'COMPLETED'
STATUS_UPDATING = 'UPDATING'
STATUS_ERROR = 'ERROR'


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _status_label(status: str) -> str:
    normalized = str(status or '').strip().upper()
    if normalized == STATUS_COMPLETED:
        return '已完成'
    if normalized == STATUS_UPDATING:
        return '更新中'
    return '异常'


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_tushare_root() -> Path:
    env_path = str(os.getenv('TUSHARE_DATA_ROOT', '')).strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path(DEFAULT_TUSHARE_ROOT).expanduser().resolve()


def _list_process_rows() -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    try:
        proc = subprocess.run(
            ['ps', '-ax', '-o', 'pid=,command='],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001
        return rows

    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        pid_text, command = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        rows.append((pid, command))
    return rows


def list_running_tushare_jobs() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for pid, command in _list_process_rows():
        mode = ''
        if 'download_tushare_bulk.py' in command:
            mode = 'FULL'
        elif 'tushare_incremental_update.py' in command:
            mode = 'INCREMENTAL'
        else:
            continue
        jobs.append(
            {
                'pid': pid,
                'mode': mode,
                'command': command,
            }
        )
    return jobs


def list_running_watchdog_jobs() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for pid, command in _list_process_rows():
        if 'champion_watchdog.py' not in command:
            continue
        jobs.append(
            {
                'pid': pid,
                'mode': 'WATCHDOG',
                'command': command,
            }
        )
    return jobs


def _infer_from_incremental_report(report_payload: dict[str, Any]) -> tuple[str, str, str]:
    if not report_payload:
        return (STATUS_ERROR, '未找到增量更新记录。', '')
    errors = report_payload.get('errors')
    error_count = len(errors) if isinstance(errors, list) else 0
    updated_at = str(report_payload.get('generated_at_utc', '')).strip()
    if error_count > 0:
        first_error = ''
        if isinstance(errors, list) and errors:
            first_error = str((errors[0] or {}).get('error', '')).strip()
        message = f'最近一次增量更新存在异常（errors={error_count}）。'
        if first_error:
            message = f'{message} 首条错误：{first_error}'
        return (STATUS_ERROR, message, updated_at)
    return (STATUS_COMPLETED, '最近一次增量更新已完成。', updated_at)


def _infer_from_bulk_manifest(payload: dict[str, Any]) -> tuple[str, str, str]:
    if not payload:
        return (STATUS_ERROR, '未找到全量下载记录。', '')
    summary = payload.get('summary', {})
    error_count = 0
    if isinstance(summary, dict):
        try:
            error_count = int(summary.get('errors', 0))
        except (TypeError, ValueError):
            error_count = 0
    updated_at = str(payload.get('generated_at_utc', '')).strip()
    if error_count > 0:
        return (STATUS_ERROR, f'最近一次全量下载存在异常（errors={error_count}）。', updated_at)
    return (STATUS_COMPLETED, '最近一次全量下载已完成。', updated_at)


def build_tushare_status_item() -> dict[str, Any]:
    root = _resolve_tushare_root()
    status_file = root / TUSHARE_STATUS_RELATIVE
    incremental_report_file = root / TUSHARE_INCREMENTAL_REPORT_RELATIVE
    bulk_manifest_file = root / TUSHARE_BULK_MANIFEST_RELATIVE

    running_jobs = list_running_tushare_jobs()
    status_payload = _safe_read_json(status_file)

    status = str(status_payload.get('status', '')).strip().upper()
    message = str(status_payload.get('message', '')).strip()
    updated_at = str(status_payload.get('updated_at_utc', '')).strip()
    last_success_at = str(status_payload.get('last_success_at_utc', '')).strip()
    last_error_at = str(status_payload.get('last_error_at_utc', '')).strip()

    if running_jobs:
        status = STATUS_UPDATING
        message = f'检测到 {len(running_jobs)} 个 Tushare 更新任务正在运行。'
        updated_at = _now_utc_iso()
    else:
        if status not in {STATUS_COMPLETED, STATUS_UPDATING, STATUS_ERROR}:
            incremental_report = _safe_read_json(incremental_report_file)
            if incremental_report:
                status, message, inferred_updated = _infer_from_incremental_report(incremental_report)
                if inferred_updated:
                    updated_at = inferred_updated
                    if status == STATUS_COMPLETED and not last_success_at:
                        last_success_at = inferred_updated
                    if status == STATUS_ERROR and not last_error_at:
                        last_error_at = inferred_updated
            else:
                bulk_manifest = _safe_read_json(bulk_manifest_file)
                status, message, inferred_updated = _infer_from_bulk_manifest(bulk_manifest)
                if inferred_updated:
                    updated_at = inferred_updated
                    if status == STATUS_COMPLETED and not last_success_at:
                        last_success_at = inferred_updated
                    if status == STATUS_ERROR and not last_error_at:
                        last_error_at = inferred_updated

    if status == STATUS_COMPLETED and not last_success_at:
        last_success_at = updated_at
    if status == STATUS_ERROR and not last_error_at:
        last_error_at = updated_at
    if not message:
        message = '状态未知，请检查运行日志。'

    return {
        'source_id': 'TUSHARE',
        'name': 'TUSHARE数据',
        'status': status if status in {STATUS_COMPLETED, STATUS_UPDATING, STATUS_ERROR} else STATUS_ERROR,
        'label': _status_label(status),
        'message': message,
        'updated_at_utc': updated_at,
        'last_success_at_utc': last_success_at,
        'last_error_at_utc': last_error_at,
        'running_jobs': running_jobs,
        'meta': {
            'root': str(root),
            'status_file': str(status_file),
            'incremental_report_file': str(incremental_report_file),
            'bulk_manifest_file': str(bulk_manifest_file),
        },
    }


def build_champion_watchdog_status_item() -> dict[str, Any]:
    running_jobs = list_running_watchdog_jobs()
    updated_at = ''
    last_success_at = ''
    last_error_at = ''

    try:
        latest_runs = list_champion_watchdog_runs(limit=1, offset=0)
        latest_items = latest_runs.get('items', []) if isinstance(latest_runs, dict) else []
        latest = latest_items[0] if isinstance(latest_items, list) and latest_items else {}
        latest_overall_status = str(latest.get('overall_status', '')).strip().upper()
        latest_run_id = str(latest.get('run_id', '')).strip()
        updated_at = str(latest.get('generated_at', '')).strip()

        open_alerts_payload = list_champion_watchdog_alerts(limit=1000, offset=0, status='OPEN')
        open_summary = open_alerts_payload.get('summary', {}) if isinstance(open_alerts_payload, dict) else {}
        open_count = int(open_summary.get('open_count', 0)) if isinstance(open_summary, dict) else 0
        critical_open = int(open_summary.get('critical_open_count', 0)) if isinstance(open_summary, dict) else 0
        warning_open = int(open_summary.get('warning_open_count', 0)) if isinstance(open_summary, dict) else 0

        if running_jobs:
            status = STATUS_UPDATING
            message = f'检测到 {len(running_jobs)} 个 Champion Watchdog 任务正在运行。'
        elif critical_open > 0:
            status = STATUS_ERROR
            message = f'存在 {critical_open} 条未关闭的严重告警。'
            last_error_at = updated_at
        elif warning_open > 0:
            status = STATUS_UPDATING
            message = f'存在 {warning_open} 条未关闭的告警。'
        elif latest_run_id:
            status = STATUS_COMPLETED
            message = '最近一次 Watchdog 运行完成，无未处理告警。'
            last_success_at = updated_at
        else:
            status = STATUS_COMPLETED
            message = '尚无 Watchdog 运行记录。'

        if latest_overall_status == 'CRITICAL' and status == STATUS_COMPLETED and open_count == 0:
            message = '最近一次 Watchdog 为严重状态，但告警已处理。'

        return {
            'source_id': 'CHAMPION_WATCHDOG',
            'name': 'Champion监控',
            'status': status,
            'label': _status_label(status),
            'message': message,
            'updated_at_utc': updated_at,
            'last_success_at_utc': last_success_at,
            'last_error_at_utc': last_error_at,
            'running_jobs': running_jobs,
            'meta': {
                'latest_run_id': latest_run_id,
                'latest_overall_status': latest_overall_status,
                'open_alert_count': open_count,
                'critical_open_alert_count': critical_open,
                'warning_open_alert_count': warning_open,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            'source_id': 'CHAMPION_WATCHDOG',
            'name': 'Champion监控',
            'status': STATUS_ERROR,
            'label': _status_label(STATUS_ERROR),
            'message': f'读取 Watchdog 状态失败：{exc}',
            'updated_at_utc': _now_utc_iso(),
            'last_success_at_utc': '',
            'last_error_at_utc': _now_utc_iso(),
            'running_jobs': running_jobs,
            'meta': {
                'open_alert_count': 0,
                'critical_open_alert_count': 0,
                'warning_open_alert_count': 0,
            },
        }


def get_data_sources_status_snapshot() -> dict[str, Any]:
    sources = [
        build_tushare_status_item(),
        build_champion_watchdog_status_item(),
    ]
    has_error = any(item.get('status') == STATUS_ERROR for item in sources)
    has_updating = any(item.get('status') == STATUS_UPDATING for item in sources)
    if has_error:
        overall = STATUS_ERROR
    elif has_updating:
        overall = STATUS_UPDATING
    else:
        overall = STATUS_COMPLETED
    return {
        'generated_at_utc': _now_utc_iso(),
        'overall_status': overall,
        'overall_label': _status_label(overall),
        'sources': sources,
    }
