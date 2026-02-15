from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable
import uuid

from app.backtest.batch import run_batch_backtest
from app.backtest.promotion import (
    evaluate_skill_pack_promotion,
    list_skill_pack_versions,
    load_promotion_gate,
    resolve_champion_version,
    switch_skill_pack_champion,
)


Runner = Callable[[dict[str, Any], str], dict[str, Any]]
SnapshotLoader = Callable[[str, str], dict[str, Any]]

_MAX_STORED_HEALTH_CHECK_RUNS = 1000


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


def _skill_pack_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / 'skill_packs'
    return Path(root_dir) / 'skill_packs'


def _manifest_path(
    skill_pack_id: str,
    version: str,
    *,
    root_dir: str | Path | None = None,
) -> Path:
    return _skill_pack_root(root_dir) / skill_pack_id / version / 'manifest.json'


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


def _health_check_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / '.run' / 'champion_health_checks'
    return Path(root_dir) / '.run' / 'champion_health_checks'


def _prune_old_runs(root: Path) -> None:
    try:
        files = [item for item in root.glob('*.json') if item.is_file()]
    except FileNotFoundError:
        return
    if len(files) <= _MAX_STORED_HEALTH_CHECK_RUNS:
        return
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in files[_MAX_STORED_HEALTH_CHECK_RUNS:]:
        try:
            stale.unlink()
        except FileNotFoundError:
            continue


def _persist_champion_health_check(payload: dict[str, Any], *, root_dir: str | Path | None = None) -> None:
    run_id = _safe_text(payload.get('run_id'))
    if not run_id:
        return
    root = _health_check_root(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    to_save = copy.deepcopy(payload)
    to_save['persisted_at'] = _now_iso()
    _write_json(root / f'{run_id}.json', to_save)
    _prune_old_runs(root)


def get_champion_health_check(run_id: str, *, root_dir: str | Path | None = None) -> dict[str, Any] | None:
    normalized = _safe_text(run_id)
    if not normalized:
        return None
    path = _health_check_root(root_dir) / f'{normalized}.json'
    return _read_json(path)


def list_champion_health_checks(
    *,
    limit: int = 50,
    offset: int = 0,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_limit = max(1, min(500, _safe_int(limit, 50)))
    normalized_offset = max(0, _safe_int(offset, 0))
    root = _health_check_root(root_dir)
    if not root.exists() or not root.is_dir():
        return {'total': 0, 'limit': normalized_limit, 'offset': normalized_offset, 'items': []}

    rows: list[dict[str, Any]] = []
    for path in root.glob('*.json'):
        payload = _read_json(path)
        if payload is None:
            continue
        evaluation = payload.get('evaluation') if isinstance(payload.get('evaluation'), dict) else {}
        rollback = payload.get('rollback_execution') if isinstance(payload.get('rollback_execution'), dict) else {}
        rows.append(
            {
                'run_id': _safe_text(payload.get('run_id')) or path.stem,
                'generated_at': _safe_text(payload.get('generated_at')),
                'skill_pack_id': _safe_text(payload.get('skill_pack_id')),
                'champion_version': _safe_text(payload.get('champion_version')),
                'baseline_version': _safe_text(payload.get('baseline_version')),
                'health_status': _safe_text(payload.get('health_status')),
                'decision': _safe_text(evaluation.get('decision')),
                'auto_rollback': bool(payload.get('auto_rollback', False)),
                'rollback_executed': bool(rollback.get('executed', False)),
            }
        )
    rows.sort(key=lambda item: (_safe_text(item.get('generated_at')), _safe_text(item.get('run_id'))), reverse=True)
    total = len(rows)
    paged = rows[normalized_offset : normalized_offset + normalized_limit]
    return {
        'total': total,
        'limit': normalized_limit,
        'offset': normalized_offset,
        'items': paged,
    }


def _resolve_baseline_version(
    *,
    skill_pack_id: str,
    champion_version: str,
    baseline_version: str | None = None,
    root_dir: str | Path | None = None,
) -> str:
    explicit = _safe_text(baseline_version)
    if explicit:
        if explicit == champion_version:
            raise ValueError('baseline_version must be different from champion_version')
        return explicit

    manifest = _read_json(_manifest_path(skill_pack_id, champion_version, root_dir=root_dir))
    if manifest is not None:
        champion_switch = manifest.get('champion_switch')
        if isinstance(champion_switch, dict):
            from_version = _safe_text(champion_switch.get('from_version'))
            if from_version and from_version != champion_version:
                return from_version
        derived_from = _safe_text(manifest.get('derived_from_champion_version'))
        if derived_from and derived_from != champion_version:
            return derived_from

    versions = list_skill_pack_versions(skill_pack_id, root_dir=root_dir)
    for item in versions:
        version = _safe_text(item.get('version'))
        status = _safe_text(item.get('status')).lower()
        if not version or version == champion_version:
            continue
        if status == 'archived':
            return version

    raise ValueError('cannot resolve baseline_version automatically; please provide baseline_version explicitly')


def run_champion_health_check(
    *,
    backtest_payload: dict[str, Any],
    runner: Runner,
    snapshot_loader: SnapshotLoader,
    benchmark_loader: SnapshotLoader,
    skill_pack_id: str = 'cn_a_core',
    champion_version: str | None = None,
    baseline_version: str | None = None,
    auto_rollback: bool = False,
    rollback_dry_run: bool = True,
    rollback_reason: str = 'monitoring_gate_block',
    operator: str = 'monitor_engine',
    manual_approved: bool = True,
    anti_overfit_evidence: dict[str, Any] | None = None,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(backtest_payload, dict):
        raise ValueError('backtest_payload must be object')

    normalized_skill_pack_id = _safe_text(skill_pack_id) or 'cn_a_core'
    resolved_champion = _safe_text(champion_version) or (
        resolve_champion_version(normalized_skill_pack_id, root_dir=root_dir) or ''
    )
    if not resolved_champion:
        raise ValueError('no champion version found')

    resolved_baseline = _resolve_baseline_version(
        skill_pack_id=normalized_skill_pack_id,
        champion_version=resolved_champion,
        baseline_version=baseline_version,
        root_dir=root_dir,
    )

    champion_backtest_payload = {
        **backtest_payload,
        'skill_pack_id': normalized_skill_pack_id,
        'skill_pack_version': resolved_champion,
    }
    baseline_backtest_payload = {
        **backtest_payload,
        'skill_pack_id': normalized_skill_pack_id,
        'skill_pack_version': resolved_baseline,
    }

    try:
        champion_result = run_batch_backtest(
            champion_backtest_payload,
            runner=runner,
            snapshot_loader=snapshot_loader,
            benchmark_loader=benchmark_loader,
        )
    except ValueError as exc:
        raise ValueError(f'champion backtest failed: {exc}') from exc

    try:
        baseline_result = run_batch_backtest(
            baseline_backtest_payload,
            runner=runner,
            snapshot_loader=snapshot_loader,
            benchmark_loader=benchmark_loader,
        )
    except ValueError as exc:
        raise ValueError(f'baseline backtest failed: {exc}') from exc

    gate = load_promotion_gate(
        normalized_skill_pack_id,
        resolved_champion,
        root_dir=root_dir,
    )
    evaluation = evaluate_skill_pack_promotion(
        candidate_backtest_result=champion_result,
        champion_backtest_result=baseline_result,
        gate_config=gate,
        candidate_version=resolved_champion,
        champion_version=resolved_baseline,
        manual_approved=bool(manual_approved),
        anti_overfit_evidence=anti_overfit_evidence,
    )
    decision = _safe_text(evaluation.get('decision')).upper()
    health_status = 'PASS' if decision == 'ALLOW' else 'FAIL'

    rollback_execution = None
    if auto_rollback and decision == 'BLOCK':
        rollback_execution = switch_skill_pack_champion(
            skill_pack_id=normalized_skill_pack_id,
            target_version=resolved_baseline,
            reason=_safe_text(rollback_reason) or 'monitoring_gate_block',
            operator=_safe_text(operator) or 'monitor_engine',
            switch_mode='auto_rollback',
            champion_version_hint=resolved_champion,
            dry_run=bool(rollback_dry_run),
            root_dir=root_dir,
        )

    result = {
        'run_id': f'hc_{uuid.uuid4().hex[:10]}',
        'generated_at': _now_iso(),
        'skill_pack_id': normalized_skill_pack_id,
        'champion_version': resolved_champion,
        'baseline_version': resolved_baseline,
        'health_status': health_status,
        'auto_rollback': bool(auto_rollback),
        'rollback_dry_run': bool(rollback_dry_run),
        'rollback_reason': _safe_text(rollback_reason),
        'operator': _safe_text(operator),
        'evaluation': evaluation,
        'rollback_execution': rollback_execution,
        'champion_backtest': {
            'batch_id': champion_result.get('batch_id', ''),
            'request': champion_result.get('request', {}),
            'summary': champion_result.get('summary', {}),
        },
        'baseline_backtest': {
            'batch_id': baseline_result.get('batch_id', ''),
            'request': baseline_result.get('request', {}),
            'summary': baseline_result.get('summary', {}),
        },
    }
    _persist_champion_health_check(result, root_dir=root_dir)
    return result
