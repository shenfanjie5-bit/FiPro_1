from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from app.eval.m4_baseline import ReportSample, load_report_samples


M5_TIER2_THRESHOLDS: dict[str, float | int] = {
    'min_sample_size': 8,
    'latency_p95_ms_max': 12000,
    'max_violation_rate': 0.05,
}


def _now_utc_iso() -> str:
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


def _parse_iso_utc_safe(value: str | datetime | None) -> datetime:
    try:
        if value is None:
            return datetime.fromtimestamp(0, timezone.utc)
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.fromtimestamp(0, timezone.utc)


def _extract_run_mode(report_json: dict[str, Any]) -> str:
    provenance = report_json.get('provenance', {})
    if isinstance(provenance, dict):
        mode = str(provenance.get('run_mode', '')).strip()
        if mode:
            return mode
    return str(report_json.get('run_mode', 'LIVE')).strip() or 'LIVE'


def _scenario_key(sample: ReportSample) -> tuple[str, str, str, str, str]:
    report = sample.report_json
    ticker = str(report.get('ticker', '')).strip()
    asof = str(report.get('asof', '')).strip()
    strategy_version_id = str(report.get('strategy_version_id', '')).strip()
    tier = str(report.get('tier', sample.tier)).strip() or sample.tier
    run_mode = _extract_run_mode(report)
    return (ticker, asof, strategy_version_id, tier, run_mode)


def _dedupe_latest_samples(samples: list[ReportSample]) -> list[ReportSample]:
    ordered = sorted(samples, key=lambda item: _parse_iso_utc_safe(item.created_at), reverse=True)
    deduped: dict[tuple[str, str, str, str, str], ReportSample] = {}
    passthrough: list[ReportSample] = []
    for sample in ordered:
        key = _scenario_key(sample)
        if any(part for part in key):
            if key not in deduped:
                deduped[key] = sample
        else:
            passthrough.append(sample)
    merged = list(deduped.values()) + passthrough
    merged.sort(key=lambda item: _parse_iso_utc_safe(item.created_at), reverse=True)
    return merged


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return round(sorted_values[0], 6)
    q = max(0.0, min(1.0, quantile))
    pos = (len(sorted_values) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = pos - lower
    result = sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
    return round(result, 6)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _resolve_tier2_budget() -> dict[str, float]:
    defaults = {'max_tool_calls': 90.0, 'max_cost_usd': 2.5}
    try:
        from app.workflows.nodes import _default_tier_policy  # noqa: PLC0415

        policy = _default_tier_policy()
        tier_cfg = dict(policy.get('TIER2', {}))
        budget = dict(tier_cfg.get('budget', {}))
        max_tool_calls = _safe_float(budget.get('max_tool_calls', defaults['max_tool_calls']), defaults['max_tool_calls'])
        max_cost_usd = _safe_float(budget.get('max_cost_usd', defaults['max_cost_usd']), defaults['max_cost_usd'])
        return {'max_tool_calls': max_tool_calls, 'max_cost_usd': max_cost_usd}
    except Exception:  # noqa: BLE001
        return defaults


def _build_thresholds() -> dict[str, float]:
    budget = _resolve_tier2_budget()
    return {
        'max_tool_calls': budget['max_tool_calls'],
        'max_cost_usd': budget['max_cost_usd'],
        'latency_p95_ms_max': _safe_float(M5_TIER2_THRESHOLDS['latency_p95_ms_max'], 12000.0),
        'max_violation_rate': _safe_float(M5_TIER2_THRESHOLDS['max_violation_rate'], 0.05),
        'min_sample_size': _safe_float(M5_TIER2_THRESHOLDS['min_sample_size'], 8.0),
    }


def _to_jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=True, sort_keys=True, default=str))


def _summarize_tier2(samples: list[ReportSample], thresholds: dict[str, float]) -> dict[str, Any]:
    sample_size = len(samples)
    if sample_size == 0:
        return {
            'sample_size': 0,
            'avg_tool_calls': 0.0,
            'tool_calls_p95': 0.0,
            'avg_cost_usd': 0.0,
            'cost_p95_usd': 0.0,
            'avg_latency_ms': 0.0,
            'latency_p95_ms': 0.0,
            'violation_rates': {'tool_calls': 0.0, 'cost': 0.0, 'latency': 0.0, 'any': 0.0},
            'violations': {'tool_calls': 0, 'cost': 0, 'latency': 0, 'any': 0},
        }

    tool_calls = [_safe_float(item.tool_calls, 0.0) for item in samples]
    costs = [_safe_float(item.cost_usd, 0.0) for item in samples]
    latencies = [_safe_float(item.latency_ms, 0.0) for item in samples]

    tool_call_limit = thresholds['max_tool_calls']
    cost_limit = thresholds['max_cost_usd']
    latency_limit = thresholds['latency_p95_ms_max']

    tool_violations = 0
    cost_violations = 0
    latency_violations = 0
    any_violations = 0
    violation_reports: list[dict[str, Any]] = []
    for item in samples:
        tc = _safe_float(item.tool_calls, 0.0)
        cost = _safe_float(item.cost_usd, 0.0)
        lat = _safe_float(item.latency_ms, 0.0)
        tc_bad = tc > tool_call_limit
        cost_bad = cost > cost_limit
        lat_bad = lat > latency_limit
        if tc_bad:
            tool_violations += 1
        if cost_bad:
            cost_violations += 1
        if lat_bad:
            latency_violations += 1
        if tc_bad or cost_bad or lat_bad:
            any_violations += 1
            violation_reports.append(
                {
                    'report_id': item.report_id,
                    'created_at': item.created_at,
                    'tool_calls': _safe_int(tc, 0),
                    'cost_usd': round(cost, 6),
                    'latency_ms': _safe_int(lat, 0),
                    'violations': {
                        'tool_calls': tc_bad,
                        'cost': cost_bad,
                        'latency': lat_bad,
                    },
                }
            )

    return {
        'sample_size': sample_size,
        'avg_tool_calls': round(sum(tool_calls) / sample_size, 6),
        'tool_calls_p95': _percentile(tool_calls, 0.95),
        'avg_cost_usd': round(sum(costs) / sample_size, 6),
        'cost_p95_usd': _percentile(costs, 0.95),
        'avg_latency_ms': round(sum(latencies) / sample_size, 6),
        'latency_p95_ms': _percentile(latencies, 0.95),
        'violation_rates': {
            'tool_calls': _rate(tool_violations, sample_size),
            'cost': _rate(cost_violations, sample_size),
            'latency': _rate(latency_violations, sample_size),
            'any': _rate(any_violations, sample_size),
        },
        'violations': {
            'tool_calls': tool_violations,
            'cost': cost_violations,
            'latency': latency_violations,
            'any': any_violations,
        },
        'violation_reports': violation_reports[:100],
    }


def _evaluate_checks(summary: dict[str, Any], thresholds: dict[str, float]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add_check(metric: str, actual: float, operator: str, threshold: float, passed: bool) -> None:
        checks.append(
            {
                'metric': metric,
                'actual': round(actual, 6),
                'operator': operator,
                'threshold': round(threshold, 6),
                'pass': bool(passed),
            }
        )

    add_check(
        'sample_size',
        _safe_float(summary.get('sample_size', 0), 0.0),
        '>=',
        thresholds['min_sample_size'],
        _safe_float(summary.get('sample_size', 0), 0.0) >= thresholds['min_sample_size'],
    )
    add_check(
        'tool_calls_p95',
        _safe_float(summary.get('tool_calls_p95', 0.0), 0.0),
        '<=',
        thresholds['max_tool_calls'],
        _safe_float(summary.get('tool_calls_p95', 0.0), 0.0) <= thresholds['max_tool_calls'],
    )
    add_check(
        'cost_p95_usd',
        _safe_float(summary.get('cost_p95_usd', 0.0), 0.0),
        '<=',
        thresholds['max_cost_usd'],
        _safe_float(summary.get('cost_p95_usd', 0.0), 0.0) <= thresholds['max_cost_usd'],
    )
    add_check(
        'latency_p95_ms',
        _safe_float(summary.get('latency_p95_ms', 0.0), 0.0),
        '<=',
        thresholds['latency_p95_ms_max'],
        _safe_float(summary.get('latency_p95_ms', 0.0), 0.0) <= thresholds['latency_p95_ms_max'],
    )
    any_violation_rate = _safe_float(summary.get('violation_rates', {}).get('any', 0.0), 0.0)
    add_check(
        'budget_violation_rate',
        any_violation_rate,
        '<=',
        thresholds['max_violation_rate'],
        any_violation_rate <= thresholds['max_violation_rate'],
    )
    return checks


def build_m5_tier2_calibration(
    samples: list[ReportSample],
    *,
    lookback_days: int,
    dedupe_latest: bool = True,
) -> dict[str, Any]:
    generated_at = _now_utc_iso()
    raw_samples = [item for item in samples if str(item.tier).upper() == 'TIER2']
    effective_samples = _dedupe_latest_samples(raw_samples) if dedupe_latest else list(raw_samples)
    thresholds = _build_thresholds()
    summary = _summarize_tier2(effective_samples, thresholds)
    checks = _evaluate_checks(summary, thresholds)
    status = 'PASS' if all(bool(item.get('pass')) for item in checks) else 'FAIL'
    if not effective_samples:
        status = 'INSUFFICIENT_DATA'

    alert_lines = {
        'tool_calls': {
            'warn_p95': round(thresholds['max_tool_calls'] * 0.85, 6),
            'critical_p95': round(thresholds['max_tool_calls'], 6),
        },
        'cost_usd': {
            'warn_p95': round(thresholds['max_cost_usd'] * 0.85, 6),
            'critical_p95': round(thresholds['max_cost_usd'], 6),
        },
        'latency_ms': {
            'warn_p95': round(thresholds['latency_p95_ms_max'] * 0.85, 6),
            'critical_p95': round(thresholds['latency_p95_ms_max'], 6),
        },
    }

    return {
        'generated_at': generated_at,
        'window': {'lookback_days': int(lookback_days), 'dedupe_latest': bool(dedupe_latest)},
        'overall_status': status,
        'raw_sample_size': len(raw_samples),
        'effective_sample_size': len(effective_samples),
        'thresholds': _to_jsonable(thresholds),
        'summary': summary,
        'threshold_checks': checks,
        'alert_lines': alert_lines,
    }


def render_m5_tier2_calibration_markdown(report: dict[str, Any]) -> str:
    lines = [
        '# M5 TIER2 Budget Calibration',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Lookback Days: `{report.get('window', {}).get('lookback_days', 0)}`",
        f"- Dedupe Latest Scenario: `{bool(report.get('window', {}).get('dedupe_latest', True))}`",
        f"- Overall Status: `{report.get('overall_status', 'UNKNOWN')}`",
        f"- Effective Sample Size: `{report.get('effective_sample_size', 0)}`",
        f"- Raw Sample Size: `{report.get('raw_sample_size', 0)}`",
        '',
        '## TIER2 Core Metrics',
        '',
        '| Metric | Value |',
        '|---|---:|',
    ]
    summary = report.get('summary', {})
    lines.extend(
        [
            f"| avg_tool_calls | {summary.get('avg_tool_calls', 0):.4f} |",
            f"| tool_calls_p95 | {summary.get('tool_calls_p95', 0):.4f} |",
            f"| avg_cost_usd | {summary.get('avg_cost_usd', 0):.4f} |",
            f"| cost_p95_usd | {summary.get('cost_p95_usd', 0):.4f} |",
            f"| avg_latency_ms | {summary.get('avg_latency_ms', 0):.2f} |",
            f"| latency_p95_ms | {summary.get('latency_p95_ms', 0):.2f} |",
            f"| budget_violation_rate | {summary.get('violation_rates', {}).get('any', 0):.4f} |",
        ]
    )

    lines.extend(['', '## Threshold Checks', '', '| Metric | Actual | Rule | Pass |', '|---|---:|---|---|'])
    for check in report.get('threshold_checks', []):
        lines.append(
            f"| {check.get('metric', '')} | {check.get('actual', 0)} | "
            f"`{check.get('operator', '')} {check.get('threshold', 0)}` | "
            f"{'YES' if check.get('pass') else 'NO'} |"
        )

    lines.extend(['', '## Alert Lines', '', '| Dimension | Warn P95 | Critical P95 |', '|---|---:|---:|'])
    alert_lines = report.get('alert_lines', {})
    lines.append(
        f"| tool_calls | {alert_lines.get('tool_calls', {}).get('warn_p95', 0)} | "
        f"{alert_lines.get('tool_calls', {}).get('critical_p95', 0)} |"
    )
    lines.append(
        f"| cost_usd | {alert_lines.get('cost_usd', {}).get('warn_p95', 0)} | "
        f"{alert_lines.get('cost_usd', {}).get('critical_p95', 0)} |"
    )
    lines.append(
        f"| latency_ms | {alert_lines.get('latency_ms', {}).get('warn_p95', 0)} | "
        f"{alert_lines.get('latency_ms', {}).get('critical_p95', 0)} |"
    )

    violations = summary.get('violation_reports', [])
    lines.extend(['', '## Violating Reports (Top 20)', ''])
    if not violations:
        lines.append('- No budget violations detected in selected TIER2 samples.')
    else:
        lines.extend(
            [
                '| Report ID | Created At | Tool Calls | Cost USD | Latency MS | Violations |',
                '|---|---|---:|---:|---:|---|',
            ]
        )
        for item in violations[:20]:
            flags = [name for name, enabled in (item.get('violations') or {}).items() if bool(enabled)]
            lines.append(
                f"| {item.get('report_id', '')} | {item.get('created_at', '')} | {item.get('tool_calls', 0)} | "
                f"{item.get('cost_usd', 0)} | {item.get('latency_ms', 0)} | {','.join(flags)} |"
            )

    return '\n'.join(lines) + '\n'


def load_and_build_m5_tier2_calibration(lookback_days: int = 14, *, dedupe_latest: bool = True) -> dict[str, Any]:
    samples = load_report_samples(lookback_days=lookback_days)
    return build_m5_tier2_calibration(samples, lookback_days=lookback_days, dedupe_latest=dedupe_latest)

