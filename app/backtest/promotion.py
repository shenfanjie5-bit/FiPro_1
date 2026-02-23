from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics
from typing import Any

from app.backtest.release_events import record_release_event
from app.backtest.skill_pack import clear_skill_pack_cache, load_skill_pack


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


def _extract_strategy_curve(backtest_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(backtest_result, dict):
        return []
    curve = ((backtest_result.get('equity_curve') or {}).get('strategy') or [])
    if not isinstance(curve, list):
        return []
    normalized: list[dict[str, Any]] = []
    for point in curve:
        if isinstance(point, dict):
            normalized.append(point)
    return normalized


def _safe_nav(point: dict[str, Any]) -> float:
    nav = _safe_float(point.get('nav'), 0.0)
    return nav if nav > 0 else 0.0


def _rolling_window_win_rate(
    candidate_curve: list[dict[str, Any]],
    champion_curve: list[dict[str, Any]],
    *,
    window_points: int,
) -> dict[str, Any]:
    usable = min(len(candidate_curve), len(champion_curve))
    if usable < 2:
        return {
            'window_points': max(2, int(window_points)),
            'window_count': 0,
            'win_count': 0,
            'pass_rate': 0.0,
            'evidence_sufficient': False,
        }

    window = max(2, int(window_points))
    starts: list[int]
    if usable <= window:
        starts = [0]
    else:
        starts = list(range(0, usable - window + 1))

    win_count = 0
    valid_count = 0
    for start in starts:
        end = min(start + window - 1, usable - 1)
        if end <= start:
            continue
        c_start = _safe_nav(candidate_curve[start])
        c_end = _safe_nav(candidate_curve[end])
        b_start = _safe_nav(champion_curve[start])
        b_end = _safe_nav(champion_curve[end])
        if c_start <= 0 or b_start <= 0:
            continue
        candidate_ret = (c_end / c_start) - 1.0
        champion_ret = (b_end / b_start) - 1.0
        if candidate_ret >= champion_ret:
            win_count += 1
        valid_count += 1

    pass_rate = (win_count / valid_count) if valid_count > 0 else 0.0
    return {
        'window_points': window,
        'window_count': valid_count,
        'win_count': win_count,
        'pass_rate': round(pass_rate, 6),
        'evidence_sufficient': valid_count > 0,
    }


def _bootstrap_outperformance(
    candidate_curve: list[dict[str, Any]],
    champion_curve: list[dict[str, Any]],
    *,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    usable = min(len(candidate_curve), len(champion_curve))
    if usable < 2:
        return {
            'sample_count': 0,
            'bootstrap_samples': max(100, int(samples)),
            'outperformance_prob': 0.0,
            'observed_mean_excess_step_return': 0.0,
            'evidence_sufficient': False,
        }

    excess_step_returns: list[float] = []
    for idx in range(1, usable):
        c_prev = _safe_nav(candidate_curve[idx - 1])
        c_now = _safe_nav(candidate_curve[idx])
        b_prev = _safe_nav(champion_curve[idx - 1])
        b_now = _safe_nav(champion_curve[idx])
        if c_prev <= 0 or b_prev <= 0:
            continue
        candidate_ret = (c_now / c_prev) - 1.0
        champion_ret = (b_now / b_prev) - 1.0
        excess_step_returns.append(candidate_ret - champion_ret)

    sample_count = len(excess_step_returns)
    bootstrap_samples = max(100, int(samples))
    if sample_count == 0:
        return {
            'sample_count': 0,
            'bootstrap_samples': bootstrap_samples,
            'outperformance_prob': 0.0,
            'observed_mean_excess_step_return': 0.0,
            'evidence_sufficient': False,
        }

    rng = random.Random(int(seed))
    win_count = 0
    for _ in range(bootstrap_samples):
        picked = [excess_step_returns[rng.randrange(sample_count)] for _ in range(sample_count)]
        if statistics.fmean(picked) > 0:
            win_count += 1
    outperformance_prob = win_count / bootstrap_samples
    observed_mean = statistics.fmean(excess_step_returns)
    return {
        'sample_count': sample_count,
        'bootstrap_samples': bootstrap_samples,
        'outperformance_prob': round(outperformance_prob, 6),
        'observed_mean_excess_step_return': round(observed_mean, 8),
        'evidence_sufficient': True,
    }


def _segment_win_rate(
    candidate_curve: list[dict[str, Any]],
    champion_curve: list[dict[str, Any]],
    *,
    segment_window_points: int = 20,
) -> float:
    usable = min(len(candidate_curve), len(champion_curve))
    if usable < 2:
        return 0.0

    window = max(2, int(segment_window_points))
    wins = 0
    total = 0

    for start in range(0, usable - 1, window):
        end = min(start + window - 1, usable - 1)
        if end <= start:
            continue
        c_start = _safe_float(candidate_curve[start].get('nav'), 1.0)
        c_end = _safe_float(candidate_curve[end].get('nav'), c_start)
        b_start = _safe_float(champion_curve[start].get('nav'), 1.0)
        b_end = _safe_float(champion_curve[end].get('nav'), b_start)
        if c_start <= 0 or b_start <= 0:
            continue
        candidate_ret = (c_end / c_start) - 1.0
        champion_ret = (b_end / b_start) - 1.0
        if candidate_ret > champion_ret:
            wins += 1
        total += 1

    # For short backtests, window-level win rate is too coarse (often a single segment).
    # Fallback to step-level comparison when available windows are fewer than 3.
    if total >= 3:
        return round(wins / total, 6)

    step_wins = 0
    step_total = 0
    for idx in range(1, usable):
        c_prev = _safe_float(candidate_curve[idx - 1].get('nav'), 1.0)
        c_now = _safe_float(candidate_curve[idx].get('nav'), c_prev)
        b_prev = _safe_float(champion_curve[idx - 1].get('nav'), 1.0)
        b_now = _safe_float(champion_curve[idx].get('nav'), b_prev)
        if c_prev <= 0 or b_prev <= 0:
            continue
        c_step_ret = (c_now / c_prev) - 1.0
        b_step_ret = (b_now / b_prev) - 1.0
        if c_step_ret > b_step_ret:
            step_wins += 1
        step_total += 1
    if step_total == 0:
        return 0.0
    return round(step_wins / step_total, 6)


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
    if metric == 'excess_return_delta_pct':
        return _safe_float(candidate.get('excess_return_pct'), 0.0) - _safe_float(champion.get('excess_return_pct'), 0.0)
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


def _parse_iso_date(value: Any) -> date | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _evaluate_anti_overfit(
    *,
    gate_config: dict[str, Any],
    anti_overfit_evidence: dict[str, Any] | None,
    candidate_backtest_result: dict[str, Any] | None = None,
    champion_backtest_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    anti_overfit_cfg = gate_config.get('anti_overfit', {}) if isinstance(gate_config, dict) else {}
    if not isinstance(anti_overfit_cfg, dict):
        anti_overfit_cfg = {}
    evidence = anti_overfit_evidence if isinstance(anti_overfit_evidence, dict) else {}

    checks: list[dict[str, Any]] = []

    require_split = bool(anti_overfit_cfg.get('require_train_validation_split', False))
    train_window = evidence.get('train_window', {}) if isinstance(evidence.get('train_window'), dict) else {}
    validation_window = (
        evidence.get('validation_window', {}) if isinstance(evidence.get('validation_window'), dict) else {}
    )
    train_start = _parse_iso_date(train_window.get('start_date'))
    train_end = _parse_iso_date(train_window.get('end_date'))
    validation_start = _parse_iso_date(validation_window.get('start_date'))
    validation_end = _parse_iso_date(validation_window.get('end_date'))
    split_ok = bool(
        train_start
        and train_end
        and validation_start
        and validation_end
        and train_start <= train_end
        and validation_start <= validation_end
        and train_end < validation_start
    )
    checks.append(
        {
            'rule': 'train_validation_split',
            'required': require_split,
            'pass': (not require_split) or split_ok,
            'reason': '' if ((not require_split) or split_ok) else 'missing/invalid non-overlapping train/validation windows',
            'detail': {
                'train_window': train_window,
                'validation_window': validation_window,
            },
        }
    )

    require_sensitivity = bool(anti_overfit_cfg.get('require_threshold_sensitivity_check', False))
    sensitivity = evidence.get('sensitivity', {}) if isinstance(evidence.get('sensitivity'), dict) else {}
    scenario_count = _safe_int(sensitivity.get('scenario_count'), 0)
    pass_rate = _safe_float(sensitivity.get('pass_rate'), 0.0)
    min_pass_rate = _safe_float(sensitivity.get('min_pass_rate'), 0.7)
    sensitivity_ok = scenario_count >= 3 and pass_rate >= min_pass_rate
    checks.append(
        {
            'rule': 'threshold_sensitivity',
            'required': require_sensitivity,
            'pass': (not require_sensitivity) or sensitivity_ok,
            'reason': (
                ''
                if ((not require_sensitivity) or sensitivity_ok)
                else (
                    'sensitivity check failed '
                    f'(scenario_count={scenario_count}, pass_rate={round(pass_rate, 6)}, '
                    f'min_pass_rate={round(min_pass_rate, 6)})'
                )
            ),
            'detail': {
                'scenario_count': scenario_count,
                'pass_rate': round(pass_rate, 6),
                'min_pass_rate': round(min_pass_rate, 6),
            },
        }
    )

    max_param_changes = _safe_int(anti_overfit_cfg.get('max_param_changes_per_iteration'), 0)
    param_change_count = _safe_int(evidence.get('param_change_count'), 0)
    param_change_ok = True if max_param_changes <= 0 else param_change_count <= max_param_changes
    checks.append(
        {
            'rule': 'max_param_changes_per_iteration',
            'required': max_param_changes > 0,
            'pass': param_change_ok,
            'reason': (
                ''
                if param_change_ok
                else (
                    'param change count exceeded limit '
                    f'(param_change_count={param_change_count}, max={max_param_changes})'
                )
            ),
            'detail': {
                'param_change_count': param_change_count,
                'max_param_changes_per_iteration': max_param_changes,
            },
        }
    )

    candidate_curve = _extract_strategy_curve(candidate_backtest_result)
    champion_curve = _extract_strategy_curve(champion_backtest_result)

    require_walk_forward = bool(anti_overfit_cfg.get('require_walk_forward_check', False))
    walk_forward_window_points = max(2, _safe_int(anti_overfit_cfg.get('walk_forward_window_points'), 20))
    walk_forward_min_pass_rate = _safe_float(anti_overfit_cfg.get('walk_forward_min_pass_rate'), 0.7)
    walk_forward = _rolling_window_win_rate(
        candidate_curve,
        champion_curve,
        window_points=walk_forward_window_points,
    )
    walk_forward_ok = bool(walk_forward.get('evidence_sufficient')) and _safe_float(
        walk_forward.get('pass_rate'),
        0.0,
    ) >= walk_forward_min_pass_rate
    checks.append(
        {
            'rule': 'walk_forward_stability',
            'required': require_walk_forward,
            'pass': (not require_walk_forward) or walk_forward_ok,
            'reason': (
                ''
                if ((not require_walk_forward) or walk_forward_ok)
                else (
                    'walk-forward check failed '
                    f"(pass_rate={round(_safe_float(walk_forward.get('pass_rate'), 0.0), 6)}, "
                    f'min={round(walk_forward_min_pass_rate, 6)}, '
                    f"windows={_safe_int(walk_forward.get('window_count'), 0)})"
                )
            ),
            'detail': {
                **walk_forward,
                'min_pass_rate': round(walk_forward_min_pass_rate, 6),
            },
        }
    )

    require_bootstrap = bool(anti_overfit_cfg.get('require_bootstrap_significance_check', False))
    bootstrap_samples = max(100, _safe_int(anti_overfit_cfg.get('bootstrap_samples'), 1000))
    bootstrap_seed = _safe_int(anti_overfit_cfg.get('bootstrap_seed'), 7)
    bootstrap_min_confidence = _safe_float(anti_overfit_cfg.get('bootstrap_min_confidence'), 0.7)
    bootstrap = _bootstrap_outperformance(
        candidate_curve,
        champion_curve,
        samples=bootstrap_samples,
        seed=bootstrap_seed,
    )
    bootstrap_ok = bool(bootstrap.get('evidence_sufficient')) and _safe_float(
        bootstrap.get('outperformance_prob'),
        0.0,
    ) >= bootstrap_min_confidence
    checks.append(
        {
            'rule': 'bootstrap_significance',
            'required': require_bootstrap,
            'pass': (not require_bootstrap) or bootstrap_ok,
            'reason': (
                ''
                if ((not require_bootstrap) or bootstrap_ok)
                else (
                    'bootstrap significance check failed '
                    f"(outperformance_prob={round(_safe_float(bootstrap.get('outperformance_prob'), 0.0), 6)}, "
                    f'min={round(bootstrap_min_confidence, 6)}, '
                    f"samples={_safe_int(bootstrap.get('sample_count'), 0)})"
                )
            ),
            'detail': {
                **bootstrap,
                'min_confidence': round(bootstrap_min_confidence, 6),
            },
        }
    )

    passed = all(bool(item.get('pass')) for item in checks)
    failed_rules = [str(item.get('rule', '')) for item in checks if not bool(item.get('pass'))]
    return {
        'pass': passed,
        'failed_rules': failed_rules,
        'checks': checks,
        'config': anti_overfit_cfg,
        'robustness': {
            'walk_forward': walk_forward,
            'bootstrap': bootstrap,
        },
    }


def evaluate_skill_pack_promotion(
    *,
    candidate_backtest_result: dict[str, Any],
    champion_backtest_result: dict[str, Any] | None,
    gate_config: dict[str, Any],
    candidate_version: str,
    champion_version: str | None = None,
    manual_approved: bool = False,
    anti_overfit_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_metrics = extract_backtest_gate_metrics(candidate_backtest_result)
    champion_metrics = extract_backtest_gate_metrics(champion_backtest_result) if champion_backtest_result else None
    if champion_metrics is not None:
        candidate_metrics['excess_return_delta_pct'] = round(
            _safe_float(candidate_metrics.get('excess_return_pct'), 0.0)
            - _safe_float(champion_metrics.get('excess_return_pct'), 0.0),
            6,
        )
        candidate_metrics['max_drawdown_delta_pct'] = round(
            _safe_float(candidate_metrics.get('max_drawdown'), 0.0)
            - _safe_float(champion_metrics.get('max_drawdown'), 0.0),
            6,
        )
        candidate_metrics['turnover_delta_pct'] = round(
            _safe_float(candidate_metrics.get('turnover'), 0.0)
            - _safe_float(champion_metrics.get('turnover'), 0.0),
            6,
        )
        candidate_metrics['data_quality_degraded_rate_delta_pct'] = round(
            _safe_float(candidate_metrics.get('data_quality_degraded_rate'), 0.0)
            - _safe_float(champion_metrics.get('data_quality_degraded_rate'), 0.0),
            6,
        )
        segment_window_points = max(2, _safe_int(gate_config.get('segment_window_points'), 20))
        candidate_metrics['segment_win_rate'] = _segment_win_rate(
            _extract_strategy_curve(candidate_backtest_result),
            _extract_strategy_curve(champion_backtest_result),
            segment_window_points=segment_window_points,
        )
    else:
        candidate_metrics['excess_return_delta_pct'] = round(_safe_float(candidate_metrics.get('excess_return_pct'), 0.0), 6)
        candidate_metrics['max_drawdown_delta_pct'] = 0.0
        candidate_metrics['turnover_delta_pct'] = 0.0
        candidate_metrics['data_quality_degraded_rate_delta_pct'] = 0.0

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
    anti_overfit = _evaluate_anti_overfit(
        gate_config=gate_config,
        anti_overfit_evidence=anti_overfit_evidence,
        candidate_backtest_result=candidate_backtest_result,
        champion_backtest_result=champion_backtest_result,
    )
    gate_pass = checks_pass and bool(anti_overfit.get('pass'))
    manual_required = bool(gate_config.get('manual_approval_required', False))
    if gate_pass and manual_required and not manual_approved:
        decision = 'PENDING_MANUAL_APPROVAL'
    elif gate_pass:
        decision = 'ALLOW'
    else:
        decision = 'BLOCK'

    failed_checks = [item.get('metric', '') for item in checks if not bool(item.get('pass'))]
    for rule in anti_overfit.get('failed_rules', []):
        failed_checks.append(f'anti_overfit.{rule}')
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
        'anti_overfit': anti_overfit,
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
    return switch_skill_pack_champion(
        skill_pack_id=skill_pack_id,
        target_version=candidate_version,
        reason='promotion_gate_allow',
        operator='promotion_engine',
        switch_mode='promotion',
        champion_version_hint=champion_version,
        dry_run=False,
        root_dir=root_dir,
    )


def switch_skill_pack_champion(
    *,
    skill_pack_id: str,
    target_version: str,
    reason: str = 'manual_switch',
    operator: str = 'manual_operator',
    switch_mode: str = 'manual',
    champion_version_hint: str | None = None,
    dry_run: bool = False,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_skill_pack_id = str(skill_pack_id or '').strip()
    normalized_target_version = str(target_version or '').strip()
    normalized_reason = str(reason or '').strip() or 'manual_switch'
    normalized_operator = str(operator or '').strip() or 'manual_operator'
    normalized_mode = str(switch_mode or '').strip() or 'manual'
    if not normalized_skill_pack_id:
        raise ValueError('skill_pack_id is required')
    if not normalized_target_version:
        raise ValueError('target_version is required')

    target_manifest_file = _manifest_path(normalized_skill_pack_id, normalized_target_version, root_dir=root_dir)
    target_manifest = _read_manifest(target_manifest_file)
    current_champion = (
        str(champion_version_hint).strip()
        if str(champion_version_hint or '').strip()
        else resolve_champion_version(normalized_skill_pack_id, root_dir=root_dir)
    )
    previous_status = str(target_manifest.get('status', '')).strip().lower()
    if current_champion and current_champion == normalized_target_version:
        return {
            'executed': False,
            'dry_run': bool(dry_run),
            'skill_pack_id': normalized_skill_pack_id,
            'target_version': normalized_target_version,
            'champion_version_before': current_champion,
            'champion_version_after': current_champion,
            'archived_previous_champion': False,
            'reason': 'target version is already champion',
        }

    if dry_run:
        dry_result = {
            'executed': False,
            'dry_run': True,
            'skill_pack_id': normalized_skill_pack_id,
            'target_version': normalized_target_version,
            'champion_version_before': current_champion or '',
            'champion_version_after': normalized_target_version,
            'archived_previous_champion': bool(current_champion and current_champion != normalized_target_version),
            'reason': normalized_reason,
            'operator': normalized_operator,
            'switch_mode': normalized_mode,
        }
        event = record_release_event(
            {
                **dry_result,
                'generated_at': _now_iso(),
                'champion_version_after': normalized_target_version,
                'metadata': {'archived_previous_champion': dry_result.get('archived_previous_champion', False)},
            },
            root_dir=root_dir,
        )
        dry_result['release_event_id'] = event.get('event_id', '')
        return dry_result

    switched_at = _now_iso()
    target_manifest['status'] = 'champion'
    target_manifest['updated_at'] = switched_at
    if current_champion:
        target_manifest['derived_from_champion_version'] = current_champion
    target_manifest['champion_switch'] = {
        'mode': normalized_mode,
        'reason': normalized_reason,
        'operator': normalized_operator,
        'switched_at': switched_at,
        'from_version': current_champion or '',
        'to_version': normalized_target_version,
        'target_previous_status': previous_status or '',
    }
    _write_manifest(target_manifest_file, target_manifest)

    archived_old = False
    if current_champion and current_champion != normalized_target_version:
        champion_manifest_file = _manifest_path(normalized_skill_pack_id, current_champion, root_dir=root_dir)
        champion_manifest = _read_manifest(champion_manifest_file)
        champion_manifest['status'] = 'archived'
        champion_manifest['updated_at'] = switched_at
        champion_manifest['champion_switch'] = {
            'mode': f'{normalized_mode}_archive_previous',
            'reason': normalized_reason,
            'operator': normalized_operator,
            'switched_at': switched_at,
            'from_version': current_champion,
            'to_version': normalized_target_version,
        }
        _write_manifest(champion_manifest_file, champion_manifest)
        archived_old = True

    clear_skill_pack_cache()
    result = {
        'executed': True,
        'dry_run': False,
        'skill_pack_id': normalized_skill_pack_id,
        'target_version': normalized_target_version,
        'candidate_version': normalized_target_version,
        'champion_version_before': current_champion or '',
        'champion_version_after': normalized_target_version,
        'archived_previous_champion': archived_old,
        'reason': normalized_reason,
        'operator': normalized_operator,
        'switch_mode': normalized_mode,
    }
    event = record_release_event(
        {
            **result,
            'generated_at': switched_at,
            'metadata': {'archived_previous_champion': archived_old},
        },
        root_dir=root_dir,
    )
    result['release_event_id'] = event.get('event_id', '')
    return result


def load_promotion_gate(
    skill_pack_id: str,
    version: str,
    *,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    pack = load_skill_pack(skill_pack_id=skill_pack_id, version=version, root_dir=root_dir)
    gate = pack.get('gate')
    if not isinstance(gate, dict):
        raise ValueError('skill pack gate config is missing or invalid')
    return gate
