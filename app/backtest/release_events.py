from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid


_MAX_STORED_RELEASE_EVENTS = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_text(value: Any) -> str:
    return str(value or '').strip()


def _events_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / '.run' / 'release_events'
    return Path(root_dir) / '.run' / 'release_events'


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


def _prune_old_events(root: Path) -> None:
    try:
        files = [item for item in root.glob('*.json') if item.is_file()]
    except FileNotFoundError:
        return
    if len(files) <= _MAX_STORED_RELEASE_EVENTS:
        return
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in files[_MAX_STORED_RELEASE_EVENTS:]:
        try:
            stale.unlink()
        except FileNotFoundError:
            continue


def record_release_event(
    payload: dict[str, Any],
    *,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    event_id = _safe_text(payload.get('event_id')) or f'release_{uuid.uuid4().hex[:10]}'
    root = _events_root(root_dir)
    root.mkdir(parents=True, exist_ok=True)

    event_payload = {
        'event_id': event_id,
        'generated_at': _safe_text(payload.get('generated_at')) or _now_iso(),
        'skill_pack_id': _safe_text(payload.get('skill_pack_id')),
        'target_version': _safe_text(payload.get('target_version')),
        'champion_version_before': _safe_text(payload.get('champion_version_before')),
        'champion_version_after': _safe_text(payload.get('champion_version_after')),
        'switch_mode': _safe_text(payload.get('switch_mode')),
        'reason': _safe_text(payload.get('reason')),
        'operator': _safe_text(payload.get('operator')),
        'dry_run': bool(payload.get('dry_run', False)),
        'executed': bool(payload.get('executed', False)),
        'metadata': payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {},
    }
    _write_json(root / f'{event_id}.json', event_payload)
    _prune_old_events(root)
    return event_payload


def get_release_event(event_id: str, *, root_dir: str | Path | None = None) -> dict[str, Any] | None:
    normalized = _safe_text(event_id)
    if not normalized:
        return None
    path = _events_root(root_dir) / f'{normalized}.json'
    return _read_json(path)


def list_release_events(
    *,
    limit: int = 50,
    offset: int = 0,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_limit = max(1, min(500, _safe_int(limit, 50)))
    normalized_offset = max(0, _safe_int(offset, 0))
    root = _events_root(root_dir)
    if not root.exists() or not root.is_dir():
        return {'total': 0, 'limit': normalized_limit, 'offset': normalized_offset, 'items': []}

    rows: list[dict[str, Any]] = []
    for path in root.glob('*.json'):
        payload = _read_json(path)
        if payload is None:
            continue
        rows.append(
            {
                'event_id': _safe_text(payload.get('event_id')) or path.stem,
                'generated_at': _safe_text(payload.get('generated_at')),
                'skill_pack_id': _safe_text(payload.get('skill_pack_id')),
                'target_version': _safe_text(payload.get('target_version')),
                'champion_version_before': _safe_text(payload.get('champion_version_before')),
                'champion_version_after': _safe_text(payload.get('champion_version_after')),
                'switch_mode': _safe_text(payload.get('switch_mode')),
                'reason': _safe_text(payload.get('reason')),
                'operator': _safe_text(payload.get('operator')),
                'dry_run': bool(payload.get('dry_run', False)),
                'executed': bool(payload.get('executed', False)),
            }
        )
    rows.sort(key=lambda item: (_safe_text(item.get('generated_at')), _safe_text(item.get('event_id'))), reverse=True)
    total = len(rows)
    paged = rows[normalized_offset : normalized_offset + normalized_limit]
    return {
        'total': total,
        'limit': normalized_limit,
        'offset': normalized_offset,
        'items': paged,
    }
