from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


M7_MODEL_GATE_THRESHOLDS: dict[str, float] = {
    'min_shadow_pairs': 20,
    'challenger_schema_pass_rate_min': 0.99,
    'challenger_coverage_pass_rate_min': 0.90,
    'challenger_win_rate_min': 0.50,
    'abs_avg_cost_delta_usd_max': 0.10,
    'abs_avg_latency_delta_ms_max': 800.0,
    'max_critical_drift_dimensions': 0.0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_m7_model_gate(
    offline_eval_report: dict[str, Any],
    shadow_compare_report: dict[str, Any],
    *,
    drift_report: dict[str, Any] | None = None,
    candidate_model: str = 'challenger',
    current_model: str = 'primary',
) -> dict[str, Any]:
    challenger_track = dict((offline_eval_report.get('tracks') or {}).get('CHALLENGER', {}))
    pair_summary = dict(shadow_compare_report.get('summary', {}))

    critical_drift = 0
    if drift_report:
        for item in drift_report.get('dimensions', []):
            if str(item.get('status', '')).upper() == 'CRITICAL':
                critical_drift += 1

    checks: list[dict[str, Any]] = []

    def add(metric: str, actual: float, operator: str, threshold: float, passed: bool) -> None:
        checks.append(
            {
                'metric': metric,
                'actual': round(actual, 6),
                'operator': operator,
                'threshold': round(threshold, 6),
                'pass': bool(passed),
            }
        )

    pair_size = _safe_float(pair_summary.get('paired_sample_size', 0), 0.0)
    add(
        'shadow_pairs',
        pair_size,
        '>=',
        M7_MODEL_GATE_THRESHOLDS['min_shadow_pairs'],
        pair_size >= M7_MODEL_GATE_THRESHOLDS['min_shadow_pairs'],
    )

    schema_pass = _safe_float(challenger_track.get('schema_pass_rate', 0.0), 0.0)
    add(
        'challenger_schema_pass_rate',
        schema_pass,
        '>=',
        M7_MODEL_GATE_THRESHOLDS['challenger_schema_pass_rate_min'],
        schema_pass >= M7_MODEL_GATE_THRESHOLDS['challenger_schema_pass_rate_min'],
    )

    coverage_pass = _safe_float(challenger_track.get('evidence_coverage_pass_rate', 0.0), 0.0)
    add(
        'challenger_coverage_pass_rate',
        coverage_pass,
        '>=',
        M7_MODEL_GATE_THRESHOLDS['challenger_coverage_pass_rate_min'],
        coverage_pass >= M7_MODEL_GATE_THRESHOLDS['challenger_coverage_pass_rate_min'],
    )

    win_rate = _safe_float(pair_summary.get('challenger_quality_win_rate', 0.0), 0.0)
    add(
        'challenger_quality_win_rate',
        win_rate,
        '>=',
        M7_MODEL_GATE_THRESHOLDS['challenger_win_rate_min'],
        win_rate >= M7_MODEL_GATE_THRESHOLDS['challenger_win_rate_min'],
    )

    abs_cost_delta = abs(_safe_float(pair_summary.get('avg_cost_delta_usd', 0.0), 0.0))
    add(
        'abs_avg_cost_delta_usd',
        abs_cost_delta,
        '<=',
        M7_MODEL_GATE_THRESHOLDS['abs_avg_cost_delta_usd_max'],
        abs_cost_delta <= M7_MODEL_GATE_THRESHOLDS['abs_avg_cost_delta_usd_max'],
    )

    abs_latency_delta = abs(_safe_float(pair_summary.get('avg_latency_delta_ms', 0.0), 0.0))
    add(
        'abs_avg_latency_delta_ms',
        abs_latency_delta,
        '<=',
        M7_MODEL_GATE_THRESHOLDS['abs_avg_latency_delta_ms_max'],
        abs_latency_delta <= M7_MODEL_GATE_THRESHOLDS['abs_avg_latency_delta_ms_max'],
    )

    add(
        'critical_drift_dimensions',
        float(critical_drift),
        '<=',
        M7_MODEL_GATE_THRESHOLDS['max_critical_drift_dimensions'],
        float(critical_drift) <= M7_MODEL_GATE_THRESHOLDS['max_critical_drift_dimensions'],
    )

    allow = all(bool(item.get('pass')) for item in checks)
    decision = 'ALLOW' if allow else 'BLOCK'

    reasons = [item['metric'] for item in checks if not item['pass']]
    return {
        'generated_at': _now_iso(),
        'decision': decision,
        'candidate_model': candidate_model,
        'current_model': current_model,
        'thresholds': dict(M7_MODEL_GATE_THRESHOLDS),
        'checks': checks,
        'failed_checks': reasons,
        'rollout_strategy': {
            'step_1': 'Start with 5% traffic shadow-only verification window for 24h.',
            'step_2': 'Promote to 10% live canary with automatic rollback.',
            'step_3': 'Rollback immediately when failure_rate or schema_pass_rate crosses M6 thresholds.',
            'rollback_model': current_model,
        },
    }


def render_m7_model_gate_markdown(report: dict[str, Any]) -> str:
    lines = [
        '# M7 Model Switch Gate',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Decision: `{report.get('decision', 'UNKNOWN')}`",
        f"- Candidate Model: `{report.get('candidate_model', '')}`",
        f"- Current Model: `{report.get('current_model', '')}`",
        '',
        '## Gate Checks',
        '',
        '| Metric | Actual | Rule | Pass |',
        '|---|---:|---|---|',
    ]
    for check in report.get('checks', []):
        lines.append(
            f"| {check.get('metric', '')} | {check.get('actual', 0)} | "
            f"`{check.get('operator', '')} {check.get('threshold', 0)}` | "
            f"{'YES' if check.get('pass') else 'NO'} |"
        )

    failed = list(report.get('failed_checks', []))
    lines.extend(['', '## Failed Checks', ''])
    if not failed:
        lines.append('- None')
    else:
        for item in failed:
            lines.append(f'- `{item}`')

    strategy = report.get('rollout_strategy', {})
    lines.extend(
        [
            '',
            '## Rollout / Rollback',
            '',
            f"- step_1: {strategy.get('step_1', '')}",
            f"- step_2: {strategy.get('step_2', '')}",
            f"- step_3: {strategy.get('step_3', '')}",
            f"- rollback_model: `{strategy.get('rollback_model', '')}`",
        ]
    )

    return '\n'.join(lines) + '\n'
