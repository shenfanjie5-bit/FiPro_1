from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.eval.m4_baseline import ReportSample, TIER_COVERAGE_RULES, load_report_samples
from app.validation.consistency import check_consistency
from app.validation.schema_validator import validate_report_schema


M7_SHADOW_THRESHOLDS: dict[str, float] = {
    'min_paired_sample_size': 10,
    'decision_change_rate_max': 0.40,
    'avg_cost_delta_usd_max': 0.15,
    'avg_latency_delta_ms_max': 1200.0,
    'challenger_quality_win_rate_min': 0.45,
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


def _extract_run_mode(report_json: dict[str, Any]) -> str:
    provenance = report_json.get('provenance', {})
    if isinstance(provenance, dict):
        run_mode = str(provenance.get('run_mode', '')).strip().upper()
        if run_mode:
            return run_mode
    return str(report_json.get('run_mode', 'LIVE')).strip().upper() or 'LIVE'


def _extract_model_primary(report_json: dict[str, Any]) -> str:
    model = report_json.get('provenance', {}).get('model', {})
    if isinstance(model, dict):
        value = str(model.get('primary', '')).strip()
        if value:
            return value
    return 'unknown-model'


def _scenario_key(sample: ReportSample) -> tuple[str, str, str, str]:
    report = sample.report_json
    return (
        str(report.get('ticker', '')).strip(),
        str(report.get('asof', '')).strip(),
        str(report.get('strategy_version_id', '')).strip(),
        str(report.get('tier', sample.tier)).strip() or sample.tier,
    )


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


def _coverage_ok(report_json: dict[str, Any], tier: str) -> bool:
    refs = report_json.get('evidence_refs', [])
    if not isinstance(refs, list):
        refs = []
    types = {str(ref.get('type', '')) for ref in refs if isinstance(ref, dict) and str(ref.get('type', '')).strip()}
    expanded = set(types)
    if 'FILINGS' in types:
        expanded.add('NEWS_DOC')
    if 'NEWS_DOC' in types:
        expanded.add('FILINGS')

    rules = TIER_COVERAGE_RULES.get(tier, TIER_COVERAGE_RULES['TIER0'])
    if len(refs) < int(rules.get('min_total_refs', 1)):
        return False
    if len(types) < int(rules.get('min_type_count', 1)):
        return False
    return set(rules.get('required_types', [])).issubset(expanded)


def _quality_score(sample: ReportSample) -> int:
    report = sample.report_json
    schema_ok, _ = validate_report_schema(report)
    if not schema_ok:
        return 0
    consistency_errors = check_consistency(report)
    citation_ok = not any('references missing evidence_id=' in err for err in consistency_errors)
    consistency_ok = not consistency_errors
    coverage_ok = _coverage_ok(report, sample.tier)
    return int(schema_ok) + int(consistency_ok) + int(citation_ok) + int(coverage_ok)


def _latest_by_scenario(samples: list[ReportSample]) -> dict[tuple[str, str, str, str], ReportSample]:
    ordered = sorted(samples, key=lambda item: _parse_iso_utc_safe(item.created_at), reverse=True)
    latest: dict[tuple[str, str, str, str], ReportSample] = {}
    for item in ordered:
        key = _scenario_key(item)
        if key not in latest:
            latest[key] = item
    return latest


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _build_checks(summary: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []

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

    paired_size = _safe_float(summary.get('paired_sample_size', 0), 0.0)
    add(
        'paired_sample_size',
        paired_size,
        '>=',
        M7_SHADOW_THRESHOLDS['min_paired_sample_size'],
        paired_size >= M7_SHADOW_THRESHOLDS['min_paired_sample_size'],
    )

    decision_change = _safe_float(summary.get('decision_change_rate', 0.0), 0.0)
    add(
        'decision_change_rate',
        decision_change,
        '<=',
        M7_SHADOW_THRESHOLDS['decision_change_rate_max'],
        decision_change <= M7_SHADOW_THRESHOLDS['decision_change_rate_max'],
    )

    cost_delta = abs(_safe_float(summary.get('avg_cost_delta_usd', 0.0), 0.0))
    add(
        'abs_avg_cost_delta_usd',
        cost_delta,
        '<=',
        M7_SHADOW_THRESHOLDS['avg_cost_delta_usd_max'],
        cost_delta <= M7_SHADOW_THRESHOLDS['avg_cost_delta_usd_max'],
    )

    latency_delta = abs(_safe_float(summary.get('avg_latency_delta_ms', 0.0), 0.0))
    add(
        'abs_avg_latency_delta_ms',
        latency_delta,
        '<=',
        M7_SHADOW_THRESHOLDS['avg_latency_delta_ms_max'],
        latency_delta <= M7_SHADOW_THRESHOLDS['avg_latency_delta_ms_max'],
    )

    win_rate = _safe_float(summary.get('challenger_quality_win_rate', 0.0), 0.0)
    add(
        'challenger_quality_win_rate',
        win_rate,
        '>=',
        M7_SHADOW_THRESHOLDS['challenger_quality_win_rate_min'],
        win_rate >= M7_SHADOW_THRESHOLDS['challenger_quality_win_rate_min'],
    )

    return checks


def build_m7_shadow_compare(samples: list[ReportSample], *, lookback_days: int) -> dict[str, Any]:
    primary = [item for item in samples if _extract_run_mode(item.report_json) in {'LIVE', 'BACKTEST'}]
    challenger = [item for item in samples if _extract_run_mode(item.report_json) == 'SHADOW']

    primary_latest = _latest_by_scenario(primary)
    challenger_latest = _latest_by_scenario(challenger)
    keys = sorted(set(primary_latest.keys()) & set(challenger_latest.keys()))

    paired_rows: list[dict[str, Any]] = []
    action_flip = 0
    challenger_win = 0
    cost_deltas: list[float] = []
    latency_deltas: list[float] = []
    by_model: dict[str, dict[str, Any]] = {}

    for key in keys:
        p = primary_latest[key]
        c = challenger_latest[key]
        p_action = str(p.report_json.get('decision', {}).get('action', 'WATCH')).upper()
        c_action = str(c.report_json.get('decision', {}).get('action', 'WATCH')).upper()
        p_quality = _quality_score(p)
        c_quality = _quality_score(c)

        cost_delta = round(_safe_float(c.cost_usd, 0.0) - _safe_float(p.cost_usd, 0.0), 6)
        latency_delta = _safe_int(c.latency_ms, 0) - _safe_int(p.latency_ms, 0)
        cost_deltas.append(cost_delta)
        latency_deltas.append(float(latency_delta))

        action_changed = p_action != c_action
        if action_changed:
            action_flip += 1

        win = c_quality > p_quality
        if c_quality == p_quality:
            win = (cost_delta <= 0) and (latency_delta <= 0)
        if win:
            challenger_win += 1

        challenger_model = _extract_model_primary(c.report_json)
        model_entry = by_model.setdefault(
            challenger_model,
            {
                'model': challenger_model,
                'paired_sample_size': 0,
                'action_change_count': 0,
                'challenger_win_count': 0,
                'cost_delta_sum': 0.0,
                'latency_delta_sum': 0.0,
            },
        )
        model_entry['paired_sample_size'] += 1
        model_entry['action_change_count'] += int(action_changed)
        model_entry['challenger_win_count'] += int(win)
        model_entry['cost_delta_sum'] += cost_delta
        model_entry['latency_delta_sum'] += float(latency_delta)

        paired_rows.append(
            {
                'scenario_key': '|'.join(key),
                'primary_report_id': p.report_id,
                'challenger_report_id': c.report_id,
                'primary_model': _extract_model_primary(p.report_json),
                'challenger_model': challenger_model,
                'primary_action': p_action,
                'challenger_action': c_action,
                'action_changed': action_changed,
                'primary_quality_score': p_quality,
                'challenger_quality_score': c_quality,
                'cost_delta_usd': cost_delta,
                'latency_delta_ms': latency_delta,
            }
        )

    paired_size = len(keys)
    summary = {
        'paired_sample_size': paired_size,
        'decision_change_rate': _rate(action_flip, paired_size),
        'challenger_quality_win_rate': _rate(challenger_win, paired_size),
        'avg_cost_delta_usd': round(sum(cost_deltas) / paired_size, 6) if paired_size else 0.0,
        'avg_latency_delta_ms': round(sum(latency_deltas) / paired_size, 6) if paired_size else 0.0,
    }

    by_model_rows: list[dict[str, Any]] = []
    for row in by_model.values():
        n = max(1, int(row['paired_sample_size']))
        by_model_rows.append(
            {
                'model': row['model'],
                'paired_sample_size': int(row['paired_sample_size']),
                'decision_change_rate': _rate(int(row['action_change_count']), n),
                'challenger_quality_win_rate': _rate(int(row['challenger_win_count']), n),
                'avg_cost_delta_usd': round(_safe_float(row['cost_delta_sum']) / n, 6),
                'avg_latency_delta_ms': round(_safe_float(row['latency_delta_sum']) / n, 6),
            }
        )
    by_model_rows.sort(key=lambda item: item['paired_sample_size'], reverse=True)

    paired_rows.sort(key=lambda item: (abs(_safe_float(item['cost_delta_usd'])), abs(_safe_float(item['latency_delta_ms']))), reverse=True)
    checks = _build_checks(summary)
    status = 'PASS' if all(bool(item.get('pass')) for item in checks) else 'FAIL'
    if paired_size == 0:
        status = 'INSUFFICIENT_DATA'

    return {
        'generated_at': _now_iso(),
        'window': {'lookback_days': int(lookback_days)},
        'overall_status': status,
        'thresholds': dict(M7_SHADOW_THRESHOLDS),
        'summary': summary,
        'threshold_checks': checks,
        'by_model': by_model_rows,
        'top_differences': paired_rows[:50],
    }


def render_m7_shadow_compare_markdown(report: dict[str, Any]) -> str:
    summary = report.get('summary', {})
    lines = [
        '# M7 Shadow Compare',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Lookback Days: `{report.get('window', {}).get('lookback_days', 0)}`",
        f"- Overall Status: `{report.get('overall_status', 'UNKNOWN')}`",
        '',
        '## Summary',
        '',
        f"- paired_sample_size: `{summary.get('paired_sample_size', 0)}`",
        f"- decision_change_rate: `{summary.get('decision_change_rate', 0)}`",
        f"- challenger_quality_win_rate: `{summary.get('challenger_quality_win_rate', 0)}`",
        f"- avg_cost_delta_usd: `{summary.get('avg_cost_delta_usd', 0)}`",
        f"- avg_latency_delta_ms: `{summary.get('avg_latency_delta_ms', 0)}`",
        '',
        '## Threshold Checks',
        '',
        '| Metric | Actual | Rule | Pass |',
        '|---|---:|---|---|',
    ]

    for check in report.get('threshold_checks', []):
        lines.append(
            f"| {check.get('metric', '')} | {check.get('actual', 0)} | "
            f"`{check.get('operator', '')} {check.get('threshold', 0)}` | "
            f"{'YES' if check.get('pass') else 'NO'} |"
        )

    lines.extend(['', '## Model Breakdown', '', '| Challenger Model | Pair Count | Win Rate | Decision Change | Avg Cost Delta | Avg Latency Delta |', '|---|---:|---:|---:|---:|---:|'])
    for row in report.get('by_model', []):
        lines.append(
            f"| {row.get('model', '')} | {row.get('paired_sample_size', 0)} | {row.get('challenger_quality_win_rate', 0):.4f} | "
            f"{row.get('decision_change_rate', 0):.4f} | {row.get('avg_cost_delta_usd', 0):.4f} | {row.get('avg_latency_delta_ms', 0):.2f} |"
        )

    lines.extend(['', '## Top Differences', '', '| Scenario | Primary | Challenger | Cost Delta | Latency Delta |', '|---|---|---|---:|---:|'])
    for row in report.get('top_differences', [])[:20]:
        lines.append(
            f"| {row.get('scenario_key', '')} | {row.get('primary_action', '')} | {row.get('challenger_action', '')} | "
            f"{row.get('cost_delta_usd', 0)} | {row.get('latency_delta_ms', 0)} |"
        )

    return '\n'.join(lines) + '\n'


def load_and_build_m7_shadow_compare(lookback_days: int = 30) -> dict[str, Any]:
    samples = load_report_samples(lookback_days=max(1, int(lookback_days)))
    return build_m7_shadow_compare(samples, lookback_days=max(1, int(lookback_days)))
