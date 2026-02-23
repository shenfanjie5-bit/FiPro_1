from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Callable
import uuid

from app.backtest.champion_monitor import (
    list_champion_health_checks,
    run_champion_health_check,
)
from app.backtest.promotion import switch_skill_pack_champion
from app.backtest.release_events import list_release_events


Runner = Callable[[dict[str, Any], str], dict[str, Any]]
SnapshotLoader = Callable[[str, str], dict[str, Any]]

_MAX_STORED_WATCHDOG_RUNS = 1000
_MAX_STORED_WATCHDOG_TICKETS = 2000

_ALERT_STATUS_OPEN = 'OPEN'
_ALERT_STATUS_ACKED = 'ACKED'
_ALERT_STATUS_CLOSED = 'CLOSED'
_ALERT_STATUS_SET = {_ALERT_STATUS_OPEN, _ALERT_STATUS_ACKED, _ALERT_STATUS_CLOSED}

_TICKET_STATUS_OPEN = 'OPEN'

_SEVERITY_RANK = {
    'critical': 3,
    'warning': 2,
    'info': 1,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_text(value: Any) -> str:
    return str(value or '').strip()


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _watchdog_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / '.run' / 'champion_watchdog_runs'
    return Path(root_dir) / '.run' / 'champion_watchdog_runs'


def _watchdog_alert_state_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / '.run' / 'champion_watchdog_alert_states'
    return Path(root_dir) / '.run' / 'champion_watchdog_alert_states'


def _watchdog_ticket_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / '.run' / 'champion_watchdog_tickets'
    return Path(root_dir) / '.run' / 'champion_watchdog_tickets'


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=False) + '\n', encoding='utf-8')


def _prune_old_json_files(root: Path, *, max_items: int) -> None:
    try:
        files = [item for item in root.glob('*.json') if item.is_file()]
    except FileNotFoundError:
        return
    if len(files) <= max_items:
        return
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in files[max_items:]:
        try:
            stale.unlink()
        except FileNotFoundError:
            continue


def _normalize_alert_status(value: Any) -> str:
    normalized = _safe_text(value).upper()
    if normalized in _ALERT_STATUS_SET:
        return normalized
    return _ALERT_STATUS_OPEN


def _normalize_severity(value: Any) -> str:
    normalized = _safe_text(value).lower()
    if normalized in {'critical', 'warning', 'info'}:
        return normalized
    return 'warning'


def _slug(value: str) -> str:
    chunks: list[str] = []
    for char in value.strip().lower():
        if char.isalnum():
            chunks.append(char)
        elif chunks and chunks[-1] != '_':
            chunks.append('_')
    result = ''.join(chunks).strip('_')
    return result or 'alert'


def _build_alert_id(run_id: str, index: int, code: str) -> str:
    return f"{_slug(run_id)}_{index + 1:02d}_{_slug(code)[:48]}"


def _load_watchdog_run_payloads(*, root_dir: str | Path | None = None) -> list[dict[str, Any]]:
    root = _watchdog_root(root_dir)
    if not root.exists() or not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in root.glob('*.json'):
        payload = _read_json(path)
        if payload is None:
            continue
        if not _safe_text(payload.get('run_id')):
            payload = dict(payload)
            payload['run_id'] = path.stem
        rows.append(payload)
    rows.sort(key=lambda item: (_safe_text(item.get('generated_at')), _safe_text(item.get('run_id'))), reverse=True)
    return rows


def _load_watchdog_alert_state(alert_id: str, *, root_dir: str | Path | None = None) -> dict[str, Any]:
    normalized = _safe_text(alert_id)
    if not normalized:
        return {}
    payload = _read_json(_watchdog_alert_state_root(root_dir) / f'{normalized}.json')
    if payload is None:
        return {}
    payload = dict(payload)
    payload['alert_id'] = normalized
    payload['status'] = _normalize_alert_status(payload.get('status'))
    return payload


def _save_watchdog_alert_state(
    *,
    alert_id: str,
    status: str,
    operator: str,
    note: str,
    action: str,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_id = _safe_text(alert_id)
    if not normalized_id:
        raise ValueError('alert_id is required')

    normalized_status = _normalize_alert_status(status)
    now = _now_iso()
    root = _watchdog_alert_state_root(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{normalized_id}.json'

    payload = _read_json(path) or {}
    payload = dict(payload)
    payload['alert_id'] = normalized_id
    payload['status'] = normalized_status
    payload['updated_at'] = now

    normalized_operator = _safe_text(operator)
    normalized_note = _safe_text(note)
    action_upper = _safe_text(action).upper()

    if action_upper == 'ACK':
        payload['acknowledged_at'] = now
        payload['acknowledged_by'] = normalized_operator
        if normalized_note:
            payload['ack_note'] = normalized_note
    elif action_upper == 'CLOSE':
        payload['closed_at'] = now
        payload['closed_by'] = normalized_operator
        if normalized_note:
            payload['close_note'] = normalized_note

    _write_json(path, payload)
    return payload


def _severity_rank(value: Any) -> int:
    return _SEVERITY_RANK.get(_normalize_severity(value), 0)


def _highest_alert_severity(alerts: list[dict[str, Any]]) -> str:
    if not alerts:
        return ''
    best = max((_normalize_severity(item.get('severity')) for item in alerts), key=lambda item: _SEVERITY_RANK.get(item, 0))
    return best


def _enrich_watchdog_alerts(
    *,
    run_id: str,
    generated_at: str,
    alerts: list[dict[str, Any]],
    root_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for index, raw in enumerate(alerts):
        if not isinstance(raw, dict):
            continue
        code = _safe_text(raw.get('code')) or f'WATCHDOG_ALERT_{index + 1}'
        alert_id = _safe_text(raw.get('alert_id')) or _build_alert_id(run_id, index, code)
        state = _load_watchdog_alert_state(alert_id, root_dir=root_dir)
        status = _normalize_alert_status(state.get('status') or raw.get('status'))
        enriched.append(
            {
                'alert_id': alert_id,
                'run_id': run_id,
                'generated_at': generated_at,
                'severity': _normalize_severity(raw.get('severity')),
                'code': code,
                'message': _safe_text(raw.get('message')),
                'status': status,
                'acknowledged_at': _safe_text(state.get('acknowledged_at')),
                'acknowledged_by': _safe_text(state.get('acknowledged_by')),
                'ack_note': _safe_text(state.get('ack_note')),
                'closed_at': _safe_text(state.get('closed_at')),
                'closed_by': _safe_text(state.get('closed_by')),
                'close_note': _safe_text(state.get('close_note')),
                'updated_at': _safe_text(state.get('updated_at')),
            }
        )
    return enriched


def _build_alert_state_summary(alerts: list[dict[str, Any]]) -> dict[str, int]:
    open_count = sum(1 for item in alerts if _normalize_alert_status(item.get('status')) == _ALERT_STATUS_OPEN)
    acked_count = sum(1 for item in alerts if _normalize_alert_status(item.get('status')) == _ALERT_STATUS_ACKED)
    closed_count = sum(1 for item in alerts if _normalize_alert_status(item.get('status')) == _ALERT_STATUS_CLOSED)
    critical_open_count = sum(
        1
        for item in alerts
        if _normalize_alert_status(item.get('status')) == _ALERT_STATUS_OPEN and _normalize_severity(item.get('severity')) == 'critical'
    )
    warning_open_count = sum(
        1
        for item in alerts
        if _normalize_alert_status(item.get('status')) == _ALERT_STATUS_OPEN and _normalize_severity(item.get('severity')) == 'warning'
    )
    return {
        'open_count': open_count,
        'acked_count': acked_count,
        'closed_count': closed_count,
        'critical_open_count': critical_open_count,
        'warning_open_count': warning_open_count,
    }


def _persist_watchdog_run(payload: dict[str, Any], *, root_dir: str | Path | None = None) -> None:
    run_id = _safe_text(payload.get('run_id'))
    if not run_id:
        return
    root = _watchdog_root(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    to_save = dict(payload)
    to_save['persisted_at'] = _now_iso()
    _write_json(root / f'{run_id}.json', to_save)
    _prune_old_json_files(root, max_items=_MAX_STORED_WATCHDOG_RUNS)


def _persist_watchdog_ticket(payload: dict[str, Any], *, root_dir: str | Path | None = None) -> None:
    ticket_id = _safe_text(payload.get('ticket_id'))
    if not ticket_id:
        return
    root = _watchdog_ticket_root(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    to_save = dict(payload)
    to_save['persisted_at'] = _now_iso()
    _write_json(root / f'{ticket_id}.json', to_save)
    _prune_old_json_files(root, max_items=_MAX_STORED_WATCHDOG_TICKETS)


def _find_latest_ticket_for_run(run_id: str, *, root_dir: str | Path | None = None) -> dict[str, Any] | None:
    normalized = _safe_text(run_id)
    if not normalized:
        return None
    root = _watchdog_ticket_root(root_dir)
    if not root.exists() or not root.is_dir():
        return None

    latest: dict[str, Any] | None = None
    for path in root.glob('*.json'):
        payload = _read_json(path)
        if payload is None:
            continue
        if _safe_text(payload.get('run_id')) != normalized:
            continue
        if latest is None:
            latest = payload
            continue
        if _safe_text(payload.get('created_at')) > _safe_text(latest.get('created_at')):
            latest = payload
    return latest


def _attach_watchdog_run_runtime_fields(payload: dict[str, Any], *, root_dir: str | Path | None = None) -> dict[str, Any]:
    run_id = _safe_text(payload.get('run_id'))
    generated_at = _safe_text(payload.get('generated_at'))
    alerts_raw = payload.get('alerts') if isinstance(payload.get('alerts'), list) else []
    alerts = _enrich_watchdog_alerts(run_id=run_id, generated_at=generated_at, alerts=alerts_raw, root_dir=root_dir)
    state_summary = _build_alert_state_summary(alerts)

    result = dict(payload)
    result['alerts'] = alerts
    result['alert_count'] = len(alerts)
    result['alert_state_summary'] = state_summary
    result['open_alert_count'] = state_summary['open_count']
    result['critical_open_alert_count'] = state_summary['critical_open_count']
    result['warning_open_alert_count'] = state_summary['warning_open_count']

    ticket = result.get('ticket') if isinstance(result.get('ticket'), dict) else None
    if ticket is None:
        ticket = _find_latest_ticket_for_run(run_id, root_dir=root_dir)
    if isinstance(ticket, dict):
        result['ticket'] = {
            'ticket_id': _safe_text(ticket.get('ticket_id')),
            'status': _safe_text(ticket.get('status')) or _TICKET_STATUS_OPEN,
            'severity': _normalize_severity(ticket.get('severity')),
            'title': _safe_text(ticket.get('title')),
            'created_at': _safe_text(ticket.get('created_at')),
        }

    return result


def _collect_watchdog_alert_rows(*, root_dir: str | Path | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in _load_watchdog_run_payloads(root_dir=root_dir):
        run_id = _safe_text(payload.get('run_id'))
        generated_at = _safe_text(payload.get('generated_at'))
        overall_status = _safe_text(payload.get('overall_status'))
        alerts_raw = payload.get('alerts') if isinstance(payload.get('alerts'), list) else []
        alerts = _enrich_watchdog_alerts(run_id=run_id, generated_at=generated_at, alerts=alerts_raw, root_dir=root_dir)
        for item in alerts:
            rows.append(
                {
                    'alert_id': _safe_text(item.get('alert_id')),
                    'run_id': run_id,
                    'generated_at': generated_at,
                    'overall_status': overall_status,
                    'severity': _normalize_severity(item.get('severity')),
                    'code': _safe_text(item.get('code')),
                    'message': _safe_text(item.get('message')),
                    'status': _normalize_alert_status(item.get('status')),
                    'acknowledged_at': _safe_text(item.get('acknowledged_at')),
                    'acknowledged_by': _safe_text(item.get('acknowledged_by')),
                    'ack_note': _safe_text(item.get('ack_note')),
                    'closed_at': _safe_text(item.get('closed_at')),
                    'closed_by': _safe_text(item.get('closed_by')),
                    'close_note': _safe_text(item.get('close_note')),
                    'updated_at': _safe_text(item.get('updated_at')),
                }
            )
    rows.sort(
        key=lambda item: (
            _safe_text(item.get('generated_at')),
            _severity_rank(item.get('severity')),
            _safe_text(item.get('alert_id')),
        ),
        reverse=True,
    )
    return rows


def get_champion_watchdog_run(run_id: str, *, root_dir: str | Path | None = None) -> dict[str, Any] | None:
    normalized = _safe_text(run_id)
    if not normalized:
        return None
    payload = _read_json(_watchdog_root(root_dir) / f'{normalized}.json')
    if payload is None:
        return None
    return _attach_watchdog_run_runtime_fields(payload, root_dir=root_dir)


def list_champion_watchdog_runs(
    *,
    limit: int = 50,
    offset: int = 0,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_limit = max(1, min(500, _safe_int(limit, 50)))
    normalized_offset = max(0, _safe_int(offset, 0))

    rows: list[dict[str, Any]] = []
    for payload in _load_watchdog_run_payloads(root_dir=root_dir):
        enriched = _attach_watchdog_run_runtime_fields(payload, root_dir=root_dir)
        summary = enriched.get('summary') if isinstance(enriched.get('summary'), dict) else {}
        recommendation = enriched.get('rollback_recommendation') if isinstance(enriched.get('rollback_recommendation'), dict) else {}
        rollback_execution = enriched.get('rollback_execution') if isinstance(enriched.get('rollback_execution'), dict) else {}
        ticket = enriched.get('ticket') if isinstance(enriched.get('ticket'), dict) else {}
        rows.append(
            {
                'run_id': _safe_text(enriched.get('run_id')),
                'generated_at': _safe_text(enriched.get('generated_at')),
                'overall_status': _safe_text(enriched.get('overall_status')),
                'alert_count': _safe_int(enriched.get('alert_count'), 0),
                'open_alert_count': _safe_int(enriched.get('open_alert_count'), 0),
                'critical_open_alert_count': _safe_int(enriched.get('critical_open_alert_count'), 0),
                'warning_open_alert_count': _safe_int(enriched.get('warning_open_alert_count'), 0),
                'latest_health_status': _safe_text(summary.get('latest_health_status')),
                'latest_decision': _safe_text(summary.get('latest_decision')),
                'should_rollback': bool(recommendation.get('should_rollback', False)),
                'rollback_target_version': _safe_text(recommendation.get('target_version')),
                'rollback_executed': bool(rollback_execution.get('executed', False)),
                'rollback_release_event_id': _safe_text(rollback_execution.get('release_event_id')),
                'ticket_id': _safe_text(ticket.get('ticket_id')),
            }
        )

    total = len(rows)
    paged = rows[normalized_offset : normalized_offset + normalized_limit]
    return {
        'total': total,
        'limit': normalized_limit,
        'offset': normalized_offset,
        'items': paged,
    }


def list_champion_watchdog_alerts(
    *,
    limit: int = 50,
    offset: int = 0,
    status: str = '',
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_limit = max(1, min(1000, _safe_int(limit, 50)))
    normalized_offset = max(0, _safe_int(offset, 0))
    status_filter = _safe_text(status).upper()
    rows = _collect_watchdog_alert_rows(root_dir=root_dir)

    if status_filter in _ALERT_STATUS_SET:
        rows = [item for item in rows if _normalize_alert_status(item.get('status')) == status_filter]

    total = len(rows)
    paged = rows[normalized_offset : normalized_offset + normalized_limit]

    summary = {
        'open_count': sum(1 for item in rows if _normalize_alert_status(item.get('status')) == _ALERT_STATUS_OPEN),
        'acked_count': sum(1 for item in rows if _normalize_alert_status(item.get('status')) == _ALERT_STATUS_ACKED),
        'closed_count': sum(1 for item in rows if _normalize_alert_status(item.get('status')) == _ALERT_STATUS_CLOSED),
        'critical_open_count': sum(
            1
            for item in rows
            if _normalize_alert_status(item.get('status')) == _ALERT_STATUS_OPEN and _normalize_severity(item.get('severity')) == 'critical'
        ),
        'warning_open_count': sum(
            1
            for item in rows
            if _normalize_alert_status(item.get('status')) == _ALERT_STATUS_OPEN and _normalize_severity(item.get('severity')) == 'warning'
        ),
    }

    return {
        'total': total,
        'limit': normalized_limit,
        'offset': normalized_offset,
        'status_filter': status_filter if status_filter in _ALERT_STATUS_SET else '',
        'summary': summary,
        'items': paged,
    }


def get_champion_watchdog_alert(alert_id: str, *, root_dir: str | Path | None = None) -> dict[str, Any] | None:
    normalized = _safe_text(alert_id)
    if not normalized:
        return None
    for item in _collect_watchdog_alert_rows(root_dir=root_dir):
        if _safe_text(item.get('alert_id')) == normalized:
            return item
    return None


def acknowledge_champion_watchdog_alert(
    alert_id: str,
    *,
    operator: str = '',
    note: str = '',
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    existing = get_champion_watchdog_alert(alert_id, root_dir=root_dir)
    if existing is None:
        raise ValueError('champion watchdog alert not found')

    current_status = _normalize_alert_status(existing.get('status'))
    if current_status != _ALERT_STATUS_CLOSED:
        _save_watchdog_alert_state(
            alert_id=_safe_text(alert_id),
            status=_ALERT_STATUS_ACKED,
            operator=operator,
            note=note,
            action='ACK',
            root_dir=root_dir,
        )
    updated = get_champion_watchdog_alert(alert_id, root_dir=root_dir)
    if updated is None:
        raise ValueError('champion watchdog alert not found')
    return updated


def close_champion_watchdog_alert(
    alert_id: str,
    *,
    operator: str = '',
    note: str = '',
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    existing = get_champion_watchdog_alert(alert_id, root_dir=root_dir)
    if existing is None:
        raise ValueError('champion watchdog alert not found')

    _save_watchdog_alert_state(
        alert_id=_safe_text(alert_id),
        status=_ALERT_STATUS_CLOSED,
        operator=operator,
        note=note,
        action='CLOSE',
        root_dir=root_dir,
    )
    updated = get_champion_watchdog_alert(alert_id, root_dir=root_dir)
    if updated is None:
        raise ValueError('champion watchdog alert not found')
    return updated


def get_champion_watchdog_ticket(ticket_id: str, *, root_dir: str | Path | None = None) -> dict[str, Any] | None:
    normalized = _safe_text(ticket_id)
    if not normalized:
        return None
    payload = _read_json(_watchdog_ticket_root(root_dir) / f'{normalized}.json')
    if payload is None:
        return None
    return payload


def list_champion_watchdog_tickets(
    *,
    limit: int = 50,
    offset: int = 0,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_limit = max(1, min(500, _safe_int(limit, 50)))
    normalized_offset = max(0, _safe_int(offset, 0))

    root = _watchdog_ticket_root(root_dir)
    if not root.exists() or not root.is_dir():
        return {'total': 0, 'limit': normalized_limit, 'offset': normalized_offset, 'items': []}

    rows: list[dict[str, Any]] = []
    for path in root.glob('*.json'):
        payload = _read_json(path)
        if payload is None:
            continue
        rows.append(
            {
                'ticket_id': _safe_text(payload.get('ticket_id')) or path.stem,
                'created_at': _safe_text(payload.get('created_at')),
                'status': _safe_text(payload.get('status')) or _TICKET_STATUS_OPEN,
                'severity': _normalize_severity(payload.get('severity')),
                'title': _safe_text(payload.get('title')),
                'run_id': _safe_text(payload.get('run_id')),
                'alert_count': _safe_int(payload.get('alert_count'), 0),
                'alert_ids': payload.get('alert_ids') if isinstance(payload.get('alert_ids'), list) else [],
            }
        )
    rows.sort(key=lambda item: (_safe_text(item.get('created_at')), _safe_text(item.get('ticket_id'))), reverse=True)
    total = len(rows)
    paged = rows[normalized_offset : normalized_offset + normalized_limit]
    return {
        'total': total,
        'limit': normalized_limit,
        'offset': normalized_offset,
        'items': paged,
    }


def _create_watchdog_ticket(
    *,
    run_id: str,
    generated_at: str,
    alerts: list[dict[str, Any]],
    root_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    if not alerts:
        return None

    severity = _highest_alert_severity(alerts)
    ticket_id = f'wdt_{uuid.uuid4().hex[:10]}'
    title = f'Champion Watchdog {severity.upper() if severity else "WARN"} alert ({run_id})'

    payload = {
        'ticket_id': ticket_id,
        'created_at': _now_iso(),
        'status': _TICKET_STATUS_OPEN,
        'severity': severity or 'warning',
        'title': title,
        'run_id': run_id,
        'generated_at': generated_at,
        'alert_count': len(alerts),
        'alert_ids': [
            _safe_text(item.get('alert_id'))
            for item in alerts
            if _safe_text(item.get('alert_id'))
        ],
        'summary': {
            'critical_alert_count': sum(1 for item in alerts if _normalize_severity(item.get('severity')) == 'critical'),
            'warning_alert_count': sum(1 for item in alerts if _normalize_severity(item.get('severity')) == 'warning'),
        },
    }
    _persist_watchdog_ticket(payload, root_dir=root_dir)

    return {
        'ticket_id': payload['ticket_id'],
        'created_at': payload['created_at'],
        'status': payload['status'],
        'severity': payload['severity'],
        'title': payload['title'],
    }


def _consecutive_failures(items: list[dict[str, Any]]) -> int:
    count = 0
    for item in items:
        status = _safe_text(item.get('health_status')).upper()
        if status != 'FAIL':
            break
        count += 1
    return count


def _recent_auto_rollback_count(release_items: list[dict[str, Any]], *, window_hours: int = 24) -> int:
    cutoff = _now_utc() - timedelta(hours=max(1, int(window_hours)))
    count = 0
    for item in release_items:
        generated = _parse_iso_datetime(item.get('generated_at'))
        if generated is None or generated < cutoff:
            continue
        switch_mode = _safe_text(item.get('switch_mode')).lower()
        executed = bool(item.get('executed', False))
        if switch_mode == 'auto_rollback' and executed:
            count += 1
    return count


def build_champion_watchdog_report(
    *,
    health_check_items: list[dict[str, Any]],
    release_items: list[dict[str, Any]],
    lookback_runs: int = 20,
    consecutive_fail_critical: int = 2,
    fail_rate_warn: float = 0.25,
    fail_rate_critical: float = 0.50,
    rollback_storm_critical: int = 2,
) -> dict[str, Any]:
    sample_size = max(1, int(lookback_runs))
    sample = list(health_check_items[:sample_size])
    alerts: list[dict[str, Any]] = []

    if not sample:
        alerts.append(
            {
                'severity': 'warning',
                'code': 'WATCHDOG_NO_HEALTH_DATA',
                'message': 'No champion health check data found. Schedule may not be running.',
            }
        )
        summary = {
            'sample_size': 0,
            'fail_count': 0,
            'fail_rate': 0.0,
            'consecutive_failures': 0,
            'latest_health_status': '',
            'latest_decision': '',
            'latest_champion_version': '',
            'latest_baseline_version': '',
            'recent_auto_rollback_count_24h': _recent_auto_rollback_count(release_items, window_hours=24),
        }
        return {
            'overall_status': 'WARN',
            'alerts': alerts,
            'summary': summary,
            'rollback_recommendation': {
                'should_rollback': False,
                'target_version': '',
                'reason': 'insufficient_health_data',
                'confidence': 'LOW',
                'suggested_action': 'Run champion health check first.',
            },
        }

    fail_count = sum(1 for item in sample if _safe_text(item.get('health_status')).upper() == 'FAIL')
    fail_rate = fail_count / len(sample)
    consecutive_failures = _consecutive_failures(sample)
    latest = sample[0]
    latest_status = _safe_text(latest.get('health_status')).upper()
    latest_decision = _safe_text(latest.get('decision')).upper()
    latest_champion = _safe_text(latest.get('champion_version'))
    latest_baseline = _safe_text(latest.get('baseline_version'))
    latest_rollback_executed = bool(latest.get('rollback_executed', False))
    recent_auto_rollback_count = _recent_auto_rollback_count(release_items, window_hours=24)

    if latest_status == 'FAIL':
        alerts.append(
            {
                'severity': 'warning',
                'code': 'CHAMPION_HEALTH_LATEST_FAIL',
                'message': f'Latest health check failed (decision={latest_decision or "UNKNOWN"}).',
            }
        )
    if consecutive_failures >= max(1, int(consecutive_fail_critical)):
        alerts.append(
            {
                'severity': 'critical',
                'code': 'CHAMPION_HEALTH_CONSECUTIVE_FAIL',
                'message': f'Consecutive failures reached {consecutive_failures}.',
            }
        )

    if len(sample) >= 5:
        if fail_rate >= fail_rate_critical:
            alerts.append(
                {
                    'severity': 'critical',
                    'code': 'CHAMPION_HEALTH_FAIL_RATE_CRITICAL',
                    'message': f'Fail rate {round(fail_rate, 6)} exceeded critical threshold {round(fail_rate_critical, 6)}.',
                }
            )
        elif fail_rate >= fail_rate_warn:
            alerts.append(
                {
                    'severity': 'warning',
                    'code': 'CHAMPION_HEALTH_FAIL_RATE_WARN',
                    'message': f'Fail rate {round(fail_rate, 6)} exceeded warning threshold {round(fail_rate_warn, 6)}.',
                }
            )

    if recent_auto_rollback_count >= max(1, int(rollback_storm_critical)):
        alerts.append(
            {
                'severity': 'critical',
                'code': 'AUTO_ROLLBACK_STORM',
                'message': f'Auto rollback count in last 24h reached {recent_auto_rollback_count}.',
            }
        )

    recommendation = {
        'should_rollback': False,
        'target_version': '',
        'reason': 'no_action',
        'confidence': 'LOW',
        'suggested_action': 'Keep monitoring.',
    }
    if latest_status == 'FAIL' and latest_baseline and latest_baseline != latest_champion:
        if latest_rollback_executed:
            recommendation = {
                'should_rollback': False,
                'target_version': latest_baseline,
                'reason': 'rollback_already_executed',
                'confidence': 'LOW',
                'suggested_action': 'Rollback already executed, continue monitoring post-rollback stability.',
            }
        else:
            confidence = 'MEDIUM'
            if consecutive_failures >= max(1, int(consecutive_fail_critical)) or fail_rate >= fail_rate_critical:
                confidence = 'HIGH'
            recommendation = {
                'should_rollback': True,
                'target_version': latest_baseline,
                'reason': 'latest_health_fail_without_executed_rollback',
                'confidence': confidence,
                'suggested_action': 'Trigger rollback to baseline and open incident review.',
            }

    overall_status = 'PASS'
    if any(str(item.get('severity', '')).lower() == 'critical' for item in alerts):
        overall_status = 'CRITICAL'
    elif alerts:
        overall_status = 'WARN'

    summary = {
        'sample_size': len(sample),
        'fail_count': fail_count,
        'fail_rate': round(fail_rate, 6),
        'consecutive_failures': consecutive_failures,
        'latest_health_status': latest_status,
        'latest_decision': latest_decision,
        'latest_champion_version': latest_champion,
        'latest_baseline_version': latest_baseline,
        'recent_auto_rollback_count_24h': recent_auto_rollback_count,
    }
    return {
        'overall_status': overall_status,
        'alerts': alerts,
        'summary': summary,
        'rollback_recommendation': recommendation,
    }


def run_champion_watchdog(
    *,
    run_health_check: bool = False,
    health_check_request: dict[str, Any] | None = None,
    runner: Runner | None = None,
    snapshot_loader: SnapshotLoader | None = None,
    benchmark_loader: SnapshotLoader | None = None,
    lookback_runs: int = 20,
    consecutive_fail_critical: int = 2,
    fail_rate_warn: float = 0.25,
    fail_rate_critical: float = 0.50,
    rollback_storm_critical: int = 2,
    execute_rollback_on_recommendation: bool = False,
    rollback_dry_run: bool = True,
    rollback_reason: str = 'watchdog_recommendation',
    rollback_operator: str = 'watchdog_engine',
    auto_create_ticket: bool = True,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    executed_health_check = None
    request_payload = health_check_request if isinstance(health_check_request, dict) else {}
    if run_health_check:
        if runner is None or snapshot_loader is None or benchmark_loader is None:
            raise ValueError('runner/snapshot_loader/benchmark_loader are required when run_health_check=true')
        executed_health_check = run_champion_health_check(
            backtest_payload=request_payload.get('backtest') if isinstance(request_payload.get('backtest'), dict) else {},
            runner=runner,
            snapshot_loader=snapshot_loader,
            benchmark_loader=benchmark_loader,
            skill_pack_id=_safe_text(request_payload.get('skill_pack_id')) or 'cn_a_core',
            champion_version=_safe_text(request_payload.get('champion_version')) or None,
            baseline_version=_safe_text(request_payload.get('baseline_version')) or None,
            auto_rollback=bool(request_payload.get('auto_rollback', False)),
            rollback_dry_run=bool(request_payload.get('rollback_dry_run', True)),
            rollback_reason=_safe_text(request_payload.get('rollback_reason')) or 'monitoring_gate_block',
            operator=_safe_text(request_payload.get('operator')) or 'monitor_engine',
            manual_approved=bool(request_payload.get('manual_approved', True)),
            anti_overfit_evidence=request_payload.get('anti_overfit_evidence')
            if isinstance(request_payload.get('anti_overfit_evidence'), dict)
            else {},
            root_dir=root_dir,
        )

    history = list_champion_health_checks(limit=max(1, int(lookback_runs)), offset=0, root_dir=root_dir)
    release_history = list_release_events(limit=200, offset=0, root_dir=root_dir)
    items = history.get('items', []) if isinstance(history, dict) else []
    release_items = release_history.get('items', []) if isinstance(release_history, dict) else []
    if not isinstance(items, list):
        items = []
    if not isinstance(release_items, list):
        release_items = []

    evaluated = build_champion_watchdog_report(
        health_check_items=items,
        release_items=release_items,
        lookback_runs=lookback_runs,
        consecutive_fail_critical=consecutive_fail_critical,
        fail_rate_warn=fail_rate_warn,
        fail_rate_critical=fail_rate_critical,
        rollback_storm_critical=rollback_storm_critical,
    )
    recommendation = (
        evaluated.get('rollback_recommendation')
        if isinstance(evaluated.get('rollback_recommendation'), dict)
        else {}
    )
    rollback_execution = None
    if execute_rollback_on_recommendation and bool(recommendation.get('should_rollback', False)):
        target_version = _safe_text(recommendation.get('target_version'))
        latest = items[0] if items and isinstance(items[0], dict) else {}
        latest_champion_version = _safe_text(latest.get('champion_version'))
        resolved_skill_pack_id = (
            _safe_text(latest.get('skill_pack_id'))
            or _safe_text(request_payload.get('skill_pack_id'))
            or 'cn_a_core'
        )
        if not target_version:
            rollback_execution = {
                'executed': False,
                'dry_run': bool(rollback_dry_run),
                'skill_pack_id': resolved_skill_pack_id,
                'target_version': '',
                'reason': 'watchdog recommendation missing target_version',
                'operator': _safe_text(rollback_operator) or 'watchdog_engine',
                'switch_mode': 'watchdog_auto_rollback',
            }
        else:
            try:
                rollback_execution = switch_skill_pack_champion(
                    skill_pack_id=resolved_skill_pack_id,
                    target_version=target_version,
                    reason=_safe_text(rollback_reason) or 'watchdog_recommendation',
                    operator=_safe_text(rollback_operator) or 'watchdog_engine',
                    switch_mode='watchdog_auto_rollback',
                    champion_version_hint=latest_champion_version or None,
                    dry_run=bool(rollback_dry_run),
                    root_dir=root_dir,
                )
            except ValueError as exc:
                rollback_execution = {
                    'executed': False,
                    'dry_run': bool(rollback_dry_run),
                    'skill_pack_id': resolved_skill_pack_id,
                    'target_version': target_version,
                    'reason': str(exc),
                    'operator': _safe_text(rollback_operator) or 'watchdog_engine',
                    'switch_mode': 'watchdog_auto_rollback',
                }

    run_id = f'wd_{uuid.uuid4().hex[:10]}'
    generated_at = _now_iso()
    raw_alerts = evaluated.get('alerts') if isinstance(evaluated.get('alerts'), list) else []
    alerts = _enrich_watchdog_alerts(run_id=run_id, generated_at=generated_at, alerts=raw_alerts, root_dir=root_dir)
    ticket = _create_watchdog_ticket(run_id=run_id, generated_at=generated_at, alerts=alerts, root_dir=root_dir) if auto_create_ticket else None

    result = {
        'run_id': run_id,
        'generated_at': generated_at,
        'overall_status': evaluated.get('overall_status', 'WARN'),
        'alert_count': len(alerts),
        'thresholds': {
            'lookback_runs': int(lookback_runs),
            'consecutive_fail_critical': int(consecutive_fail_critical),
            'fail_rate_warn': round(_safe_float(fail_rate_warn, 0.25), 6),
            'fail_rate_critical': round(_safe_float(fail_rate_critical, 0.50), 6),
            'rollback_storm_critical': int(rollback_storm_critical),
            'execute_rollback_on_recommendation': bool(execute_rollback_on_recommendation),
            'rollback_dry_run': bool(rollback_dry_run),
        },
        'summary': evaluated.get('summary', {}),
        'alerts': alerts,
        'rollback_recommendation': recommendation,
        'rollback_execution': rollback_execution,
        'ticket': ticket,
        'executed_health_check': (
            {
                'run_id': _safe_text(executed_health_check.get('run_id')),
                'health_status': _safe_text(executed_health_check.get('health_status')),
                'champion_version': _safe_text(executed_health_check.get('champion_version')),
                'baseline_version': _safe_text(executed_health_check.get('baseline_version')),
                'rollback_execution': executed_health_check.get('rollback_execution'),
            }
            if isinstance(executed_health_check, dict)
            else None
        ),
        'recent_health_checks': items[: min(20, len(items))],
        'recent_release_events': release_items[: min(20, len(release_items))],
    }
    _persist_watchdog_run(result, root_dir=root_dir)
    return _attach_watchdog_run_runtime_fields(result, root_dir=root_dir)


def render_champion_watchdog_markdown(report: dict[str, Any]) -> str:
    summary = report.get('summary') if isinstance(report.get('summary'), dict) else {}
    recommendation = report.get('rollback_recommendation') if isinstance(report.get('rollback_recommendation'), dict) else {}
    rollback_execution = report.get('rollback_execution') if isinstance(report.get('rollback_execution'), dict) else {}
    lines = [
        '# Champion Watchdog',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Overall Status: `{report.get('overall_status', '')}`",
        f"- Alert Count: `{report.get('alert_count', 0)}`",
        '',
        '## Summary',
        '',
        f"- Sample Size: `{summary.get('sample_size', 0)}`",
        f"- Fail Count: `{summary.get('fail_count', 0)}`",
        f"- Fail Rate: `{summary.get('fail_rate', 0)}`",
        f"- Consecutive Failures: `{summary.get('consecutive_failures', 0)}`",
        f"- Latest Health: `{summary.get('latest_health_status', '')}`",
        f"- Latest Decision: `{summary.get('latest_decision', '')}`",
        f"- Latest Champion: `{summary.get('latest_champion_version', '')}`",
        f"- Latest Baseline: `{summary.get('latest_baseline_version', '')}`",
        '',
        '## Alerts',
        '',
    ]
    alerts = report.get('alerts', [])
    if not alerts:
        lines.append('- No alerts.')
    else:
        for item in alerts:
            lines.append(
                (
                    f"- [{str(item.get('severity', '')).upper()}] `{item.get('code', '')}` "
                    f"(id={item.get('alert_id', '')}, status={item.get('status', '')}): {item.get('message', '')}"
                )
            )

    ticket = report.get('ticket') if isinstance(report.get('ticket'), dict) else None
    if ticket:
        lines.extend(
            [
                '',
                '## Auto Ticket',
                '',
                f"- ticket_id: `{ticket.get('ticket_id', '')}`",
                f"- severity: `{ticket.get('severity', '')}`",
                f"- status: `{ticket.get('status', '')}`",
            ]
        )

    lines.extend(
        [
            '',
            '## Rollback Recommendation',
            '',
            f"- should_rollback: `{recommendation.get('should_rollback', False)}`",
            f"- target_version: `{recommendation.get('target_version', '')}`",
            f"- reason: `{recommendation.get('reason', '')}`",
            f"- confidence: `{recommendation.get('confidence', '')}`",
            f"- suggested_action: {recommendation.get('suggested_action', '')}",
        ]
    )
    if rollback_execution:
        lines.extend(
            [
                '',
                '## Rollback Execution',
                '',
                f"- executed: `{rollback_execution.get('executed', False)}`",
                f"- dry_run: `{rollback_execution.get('dry_run', False)}`",
                f"- target_version: `{rollback_execution.get('target_version', '')}`",
                f"- release_event_id: `{rollback_execution.get('release_event_id', '')}`",
                f"- reason: `{rollback_execution.get('reason', '')}`",
            ]
        )
    return '\n'.join(lines) + '\n'
