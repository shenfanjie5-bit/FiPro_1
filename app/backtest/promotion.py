from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
from typing import Any

from app.backtest.skill_pack import load_skill_pack


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _skill_pack_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / 'skill_packs'
    return Path(root_dir)


def _manifest_path(skill_pack_id: str, version: str, *, root_dir: str | Path | None = None) -> Path:
    return _skill_pack_root(root_dir) / skill_pack_id / version / 'manifest.json'


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ValueError(f'manifest not found: {path}') from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f'invalid manifest json: {path}: {exc}') from exc
    if not isinstance(payload, dict):
        raise ValueError(f'manifest must be object json: {path}')
    return payload


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=False) + '\n', encoding='utf-8')


def _semver_key(version: str) -> tuple[int, int, int, str]:
    parts = str(version or '').split('.')
    if len(parts) >= 3:
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]), '')
        except ValueError:
            return (0, 0, 0, str(version or ''))
    return (0, 0, 0, str(version or ''))


def list_skill_pack_versions(skill_pack_id: str, *, root_dir: str | Path | None = None) -> list[dict[str, Any]]:
    root = _skill_pack_root(root_dir) / skill_pack_id
    if not root.exists() or not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        manifest_file = child / 'manifest.json'
        if not manifest_file.exists():
            continue
        try:
            manifest = _read_manifest(manifest_file)
        except ValueError:
            continue
        items.append(
            {
                'version': str(manifest.get('version', child.name)),
                'status': str(manifest.get('status', 'draft')),
                'market': str(manifest.get('market', '')),
                'manifest_path': str(manifest_file),
                'updated_at': str(manifest.get('updated_at', manifest.get('created_at', ''))),
            }
        )
    items.sort(key=lambda row: (_semver_key(str(row.get('version', ''))), str(row.get('version', ''))), reverse=True)
    return items


def resolve_champion_version(skill_pack_id: str, *, root_dir: str | Path | None = None) -> str | None:
    versions = list_skill_pack_versions(skill_pack_id, root_dir=root_dir)
    for item in versions:
        if str(item.get('status', '')).strip().lower() == 'champion':
            version = str(item.get('version', '')).strip()
            if version:
                return version
    return None


def _max_drawdown_pct(strategy_curve: list[dict[str, Any]]) -> float:
    peak = 1.0
    max_drawdown = 0.0
    for point in strategy_curve:
        nav = _safe_float(point.get('nav'), 1.0)
        if nav <= 0:
            continue
        peak = max(peak, nav)
        drawdown = ((peak - nav) / peak) * 100.0 if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)
    return round(max_drawdown, 6)


def _annualized_volatility_pct(strategy_curve: list[dict[str, Any]]) -> float:
    step_returns: list[float] = []
    for idx, point in enumerate(strategy_curve):
        if idx == 0:
            continue
        pct = _safe_float(point.get('step_return_pct'), 0.0)
        step_returns.append(pct / 100.0)
    if len(step_returns) < 2:
        return 0.0
    std = statistics.pstdev(step_returns)
    annualized = std * math.sqrt(252.0) * 100.0
    return round(annualized, 6)


def _turnover_pct_from_actions(summary: dict[str, Any]) -> float:
    total_runs = max(1, _safe_int(summary.get('total_runs'), 0))
    action_counts = summary.get('action_counts', {})
    if not isinstance(action_counts, dict):
        action_counts = {}
    turnover_ops = (
        _safe_int(action_counts.get('BUY'), 0)
        + _safe_int(action_counts.get('ADD'), 0)
        + _safe_int(action_counts.get('REDUCE'), 0)
        + _safe_int(action_counts.get('SELL'), 0)
    )
    return round((turnover_ops / total_runs) * 100.0, 6)


def extract_backtest_gate_metrics(backtest_result: dict[str, Any]) -> dict[str, float]:
    summary = backtest_result.get('summary', {}) if isinstance(backtest_result, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    strategy_curve = (
        ((backtest_result.get('equity_curve') or {}).get('strategy') or [])
        if isinstance(backtest_result, dict)
        else []
    )
    if not isinstance(strategy_curve, list):
        strategy_curve = []
    data_quality_counts = summary.get('data_quality_counts', {})
    if not isinstance(data_quality_counts, dict):
        data_quality_counts = {}

    completed_runs = max(1, _safe_int(summary.get('completed_runs'), 0))
    degraded_runs = _safe_int(data_quality_counts.get('DEGRADED'), 0)
    data_quality_degraded_rate = (degraded_runs / completed_runs) * 100.0

    metrics = {
        'strategy_total_return_pct': round(_safe_float(summary.get('strategy_total_return_pct'), 0.0), 6),
        'excess_return_pct': round(_safe_float(summary.get('excess_return_pct'), 0.0), 6),
        'max_drawdown': _max_drawdown_pct(strategy_curve),
        'volatility': _annualized_volatility_pct(strategy_curve),
        'turnover': _turnover_pct_from_actions(summary),
        'win_rate': round(_safe_float(summary.get('directional_hit_rate'), 0.0) * 100.0, 6),
        'cost_budget_violation_rate': round(_safe_float(summary.get('cost_budget_violation_rate'), 0.0), 6),
        'data_quality_degraded_rate': round(data_quality_degraded_rate, 6),
        'segment_win_rate': round(_safe_float(summary.get('segment_win_rate'), _safe_float(summary.get('directional_hit_rate'), 0.0)), 6),
    }
    return metrics


def _compute_metric(metric: str, candidate: dict[str, float], champion: dict[str, float] | None) -> float | None:
    if metric in candidate:
        return _safe_float(candidate.get(metric), 0.0)
    if champion is None:
        return None
    if metric == 'max_drawdown_delta_pct':
        return _safe_float(candidate.get('max_drawdown'), 0.0) - _safe_float(champion.get('max_drawdown'), 0.0)
    if metric == 'turnover_delta_pct':
        return _safe_float(candidate.get('turnover'), 0.0) - _safe_float(champion.get('turnover'), 0.0)
    if metric == 'data_quality_degraded_rate_delta_pct':
        return _safe_float(candidate.get('data_quality_degraded_rate'), 0.0) - _safe_float(
            champion.get('data_quality_degraded_rate'),
            0.0,
        )
    return None


def _check_pass(actual: float, op: str, threshold: float) -> bool:
    if op == '>=':
        return actual >= threshold
    if op == '>':
        return actual > threshold
    if op == '<=':
        return actual <= threshold
    if op == '<':
        return actual < threshold
    if op == '==':
        return abs(actual - threshold) <= 1e-9
    return False


def evaluate_skill_pack_promotion(
    *,
    candidate_backtest_result: dict[str, Any],
    champion_backtest_result: dict[str, Any] | None,
    gate_config: dict[str, Any],
    candidate_version: str,
    champion_version: str | None = None,
    manual_approved: bool = False,
) -> dict[str, Any]:
    candidate_metrics = extract_backtest_gate_metrics(candidate_backtest_result)
    champion_metrics = extract_backtest_gate_metrics(champion_backtest_result) if champion_backtest_result else None

    promotion_rule = gate_config.get('promotion_rule', {}) if isinstance(gate_config, dict) else {}
    checks_payload = promotion_rule.get('all_of', []) if isinstance(promotion_rule, dict) else []
    if not isinstance(checks_payload, list):
        checks_payload = []

    checks: list[dict[str, Any]] = []
    for item in checks_payload:
        if not isinstance(item, dict):
            continue
        metric = str(item.get('metric', '')).strip()
        op = str(item.get('op', '')).strip() or '>='
        threshold = _safe_float(item.get('value'), 0.0)
        actual = _compute_metric(metric, candidate_metrics, champion_metrics)
        if actual is None:
            checks.append(
                {
                    'metric': metric,
                    'operator': op,
                    'threshold': round(threshold, 6),
                    'actual': None,
                    'pass': False,
                    'reason': 'metric unavailable (likely missing champion baseline)',
                }
            )
            continue
        passed = _check_pass(actual, op, threshold)
        checks.append(
            {
                'metric': metric,
                'operator': op,
                'threshold': round(threshold, 6),
                'actual': round(actual, 6),
                'pass': passed,
                'reason': '',
            }
        )

    checks_pass = all(bool(item.get('pass')) for item in checks) if checks else False
    manual_required = bool(gate_config.get('manual_approval_required', False))
    if checks_pass and manual_required and not manual_approved:
        decision = 'PENDING_MANUAL_APPROVAL'
    elif checks_pass:
        decision = 'ALLOW'
    else:
        decision = 'BLOCK'

    failed_checks = [item.get('metric', '') for item in checks if not bool(item.get('pass'))]
    return {
        'generated_at': _now_iso(),
        'decision': decision,
        'candidate_version': candidate_version,
        'champion_version': champion_version or '',
        'manual_required': manual_required,
        'manual_approved': bool(manual_approved),
        'candidate_metrics': candidate_metrics,
        'champion_metrics': champion_metrics or {},
        'checks': checks,
        'failed_checks': failed_checks,
        'promotion_rule': promotion_rule,
    }


def execute_skill_pack_promotion(
    *,
    skill_pack_id: str,
    candidate_version: str,
    evaluation: dict[str, Any],
    champion_version: str | None,
    dry_run: bool = False,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    decision = str(evaluation.get('decision', 'BLOCK')).upper()
    if decision != 'ALLOW':
        return {
            'executed': False,
            'dry_run': bool(dry_run),
            'reason': f'evaluation decision is {decision}',
        }
    if dry_run:
        return {
            'executed': False,
            'dry_run': True,
            'reason': 'dry_run enabled',
        }

    candidate_manifest_file = _manifest_path(skill_pack_id, candidate_version, root_dir=root_dir)
    candidate_manifest = _read_manifest(candidate_manifest_file)
    candidate_manifest['status'] = 'champion'
    candidate_manifest['updated_at'] = _now_iso()
    if champion_version:
        candidate_manifest['derived_from_champion_version'] = champion_version
    _write_manifest(candidate_manifest_file, candidate_manifest)

    archived_old = False
    if champion_version and champion_version != candidate_version:
        champion_manifest_file = _manifest_path(skill_pack_id, champion_version, root_dir=root_dir)
        champion_manifest = _read_manifest(champion_manifest_file)
        champion_manifest['status'] = 'archived'
        champion_manifest['updated_at'] = _now_iso()
        _write_manifest(champion_manifest_file, champion_manifest)
        archived_old = True

    return {
        'executed': True,
        'dry_run': False,
        'skill_pack_id': skill_pack_id,
        'candidate_version': candidate_version,
        'archived_previous_champion': archived_old,
    }


def load_promotion_gate(skill_pack_id: str, version: str) -> dict[str, Any]:
    pack = load_skill_pack(skill_pack_id=skill_pack_id, version=version)
    gate = pack.get('gate')
    if not isinstance(gate, dict):
        raise ValueError('skill pack gate config is missing or invalid')
    return gate

