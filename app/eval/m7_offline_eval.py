from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.eval.m4_baseline import ReportSample, TIER_COVERAGE_RULES, load_report_samples
from app.validation.consistency import check_consistency
from app.validation.schema_validator import validate_report_schema


M7_OFFLINE_THRESHOLDS: dict[str, float] = {
    'min_sample_size': 10,
    'schema_pass_rate_min': 0.99,
    'consistency_pass_rate_min': 0.97,
    'citation_consistency_rate_min': 0.98,
    'evidence_coverage_pass_rate_min': 0.90,
    'min_paired_sample_size': 8,
    'challenger_win_rate_min': 0.45,
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
    required_types = set(rules.get('required_types', []))
    return required_types.issubset(expanded)


def _sample_metrics(sample: ReportSample) -> dict[str, Any]:
    report = sample.report_json
    schema_ok, _ = validate_report_schema(report)
    consistency_errors = check_consistency(report) if schema_ok else ['schema_failed']
    citation_errors = [err for err in consistency_errors if 'references missing evidence_id=' in err]
    consistency_ok = not consistency_errors
    citation_ok = not citation_errors
    coverage_ok = _coverage_ok(report, sample.tier)
    action = str(report.get('decision', {}).get('action', 'WATCH')).upper()

    return {
        'report_id': sample.report_id,
        'scenario_key': _scenario_key(sample),
        'run_mode': _extract_run_mode(report),
        'tier': sample.tier,
        'created_at': sample.created_at,
        'schema_ok': bool(schema_ok),
        'consistency_ok': bool(consistency_ok),
        'citation_ok': bool(citation_ok),
        'coverage_ok': bool(coverage_ok),
        'quality_score': int(bool(schema_ok)) + int(bool(consistency_ok)) + int(bool(citation_ok)) + int(bool(coverage_ok)),
        'cost_usd': round(_safe_float(sample.cost_usd, 0.0), 6),
        'latency_ms': _safe_int(sample.latency_ms, 0),
        'tool_calls': _safe_int(sample.tool_calls, 0),
        'action': action,
    }


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _summarize(track_name: str, metrics: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(metrics)
    if total == 0:
        return {
            'track': track_name,
            'sample_size': 0,
            'schema_pass_rate': 0.0,
            'consistency_pass_rate': 0.0,
            'citation_consistency_rate': 0.0,
            'evidence_coverage_pass_rate': 0.0,
            'avg_cost_usd': 0.0,
            'avg_latency_ms': 0.0,
            'avg_tool_calls': 0.0,
        }

    schema_pass = sum(1 for item in metrics if item['schema_ok'])
    consistency_pass = sum(1 for item in metrics if item['consistency_ok'])
    citation_pass = sum(1 for item in metrics if item['citation_ok'])
    coverage_pass = sum(1 for item in metrics if item['coverage_ok'])
    avg_cost = sum(_safe_float(item['cost_usd'], 0.0) for item in metrics) / total
    avg_latency = sum(_safe_float(item['latency_ms'], 0.0) for item in metrics) / total
    avg_tool_calls = sum(_safe_float(item['tool_calls'], 0.0) for item in metrics) / total

    return {
        'track': track_name,
        'sample_size': total,
        'schema_pass_rate': _rate(schema_pass, total),
        'consistency_pass_rate': _rate(consistency_pass, total),
        'citation_consistency_rate': _rate(citation_pass, total),
        'evidence_coverage_pass_rate': _rate(coverage_pass, total),
        'avg_cost_usd': round(avg_cost, 6),
        'avg_latency_ms': round(avg_latency, 6),
        'avg_tool_calls': round(avg_tool_calls, 6),
    }


def _latest_by_scenario(metrics: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    ordered = sorted(metrics, key=lambda item: _parse_iso_utc_safe(item.get('created_at')), reverse=True)
    latest: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in ordered:
        key = item['scenario_key']
        if key not in latest:
            latest[key] = item
    return latest


def _pair_summary(primary_metrics: list[dict[str, Any]], challenger_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    primary_latest = _latest_by_scenario(primary_metrics)
    challenger_latest = _latest_by_scenario(challenger_metrics)
    common_keys = sorted(set(primary_latest.keys()) & set(challenger_latest.keys()))

    if not common_keys:
        return {
            'paired_sample_size': 0,
            'decision_change_rate': 0.0,
            'challenger_win_rate': 0.0,
            'avg_cost_delta_usd': 0.0,
            'avg_latency_delta_ms': 0.0,
            'top_differences': [],
        }

    action_changed = 0
    challenger_wins = 0
    cost_deltas: list[float] = []
    latency_deltas: list[float] = []
    diffs: list[dict[str, Any]] = []
    for key in common_keys:
        primary = primary_latest[key]
        challenger = challenger_latest[key]

        if primary['action'] != challenger['action']:
            action_changed += 1

        cost_delta = round(_safe_float(challenger['cost_usd']) - _safe_float(primary['cost_usd']), 6)
        latency_delta = _safe_int(challenger['latency_ms']) - _safe_int(primary['latency_ms'])
        cost_deltas.append(cost_delta)
        latency_deltas.append(float(latency_delta))

        primary_quality = _safe_int(primary['quality_score'])
        challenger_quality = _safe_int(challenger['quality_score'])
        challenger_win = challenger_quality > primary_quality
        if challenger_quality == primary_quality:
            challenger_win = (cost_delta <= 0) and (latency_delta <= 0)
        if challenger_win:
            challenger_wins += 1

        diffs.append(
            {
                'scenario_key': '|'.join(key),
                'primary_report_id': primary.get('report_id', ''),
                'challenger_report_id': challenger.get('report_id', ''),
                'primary_action': primary.get('action', ''),
                'challenger_action': challenger.get('action', ''),
                'primary_quality_score': primary_quality,
                'challenger_quality_score': challenger_quality,
                'cost_delta_usd': cost_delta,
                'latency_delta_ms': latency_delta,
            }
        )

    diffs.sort(key=lambda item: (abs(_safe_float(item['cost_delta_usd'])), abs(_safe_float(item['latency_delta_ms']))), reverse=True)
    n = len(common_keys)
    return {
        'paired_sample_size': n,
        'decision_change_rate': _rate(action_changed, n),
        'challenger_win_rate': _rate(challenger_wins, n),
        'avg_cost_delta_usd': round(sum(cost_deltas) / n, 6),
        'avg_latency_delta_ms': round(sum(latency_deltas) / n, 6),
        'top_differences': diffs[:20],
    }


def _threshold_checks(
    primary_summary: dict[str, Any],
    challenger_summary: dict[str, Any],
    pair_summary: dict[str, Any],
) -> list[dict[str, Any]]:
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

    for name, summary in (('PRIMARY', primary_summary), ('CHALLENGER', challenger_summary)):
        sample_size = _safe_float(summary.get('sample_size', 0), 0.0)
        add(
            f'{name}.sample_size',
            sample_size,
            '>=',
            M7_OFFLINE_THRESHOLDS['min_sample_size'],
            sample_size >= M7_OFFLINE_THRESHOLDS['min_sample_size'],
        )
        for metric, threshold_key in (
            ('schema_pass_rate', 'schema_pass_rate_min'),
            ('consistency_pass_rate', 'consistency_pass_rate_min'),
            ('citation_consistency_rate', 'citation_consistency_rate_min'),
            ('evidence_coverage_pass_rate', 'evidence_coverage_pass_rate_min'),
        ):
            actual = _safe_float(summary.get(metric, 0.0), 0.0)
            threshold = M7_OFFLINE_THRESHOLDS[threshold_key]
            add(
                f'{name}.{metric}',
                actual,
                '>=',
                threshold,
                actual >= threshold,
            )

    paired_size = _safe_float(pair_summary.get('paired_sample_size', 0), 0.0)
    add(
        'PAIR.paired_sample_size',
        paired_size,
        '>=',
        M7_OFFLINE_THRESHOLDS['min_paired_sample_size'],
        paired_size >= M7_OFFLINE_THRESHOLDS['min_paired_sample_size'],
    )
    challenger_win_rate = _safe_float(pair_summary.get('challenger_win_rate', 0.0), 0.0)
    add(
        'PAIR.challenger_win_rate',
        challenger_win_rate,
        '>=',
        M7_OFFLINE_THRESHOLDS['challenger_win_rate_min'],
        challenger_win_rate >= M7_OFFLINE_THRESHOLDS['challenger_win_rate_min'],
    )
    return checks


def build_m7_offline_eval(
    samples: list[ReportSample],
    *,
    lookback_days: int,
) -> dict[str, Any]:
    metrics = [_sample_metrics(item) for item in samples]
    primary_metrics = [item for item in metrics if item['run_mode'] in {'LIVE', 'BACKTEST'}]
    challenger_metrics = [item for item in metrics if item['run_mode'] == 'SHADOW']

    primary_summary = _summarize('PRIMARY', primary_metrics)
    challenger_summary = _summarize('CHALLENGER', challenger_metrics)
    pair_summary = _pair_summary(primary_metrics, challenger_metrics)
    checks = _threshold_checks(primary_summary, challenger_summary, pair_summary)

    status = 'PASS' if all(bool(item.get('pass')) for item in checks) else 'FAIL'
    if not primary_metrics:
        status = 'INSUFFICIENT_DATA'

    return {
        'generated_at': _now_iso(),
        'window': {'lookback_days': int(lookback_days)},
        'overall_status': status,
        'thresholds': dict(M7_OFFLINE_THRESHOLDS),
        'tracks': {
            'PRIMARY': primary_summary,
            'CHALLENGER': challenger_summary,
        },
        'pair_summary': pair_summary,
        'threshold_checks': checks,
    }


def render_m7_offline_eval_markdown(report: dict[str, Any]) -> str:
    lines = [
        '# M7 Offline Evaluation',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Lookback Days: `{report.get('window', {}).get('lookback_days', 0)}`",
        f"- Overall Status: `{report.get('overall_status', 'UNKNOWN')}`",
        '',
        '## Track Metrics',
        '',
        '| Track | Sample | Schema | Consistency | Citation | Coverage | Avg Cost | Avg Latency |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    tracks = report.get('tracks', {})
    for name in ('PRIMARY', 'CHALLENGER'):
        summary = tracks.get(name, {})
        lines.append(
            f"| {name} | {summary.get('sample_size', 0)} | {summary.get('schema_pass_rate', 0):.4f} | "
            f"{summary.get('consistency_pass_rate', 0):.4f} | {summary.get('citation_consistency_rate', 0):.4f} | "
            f"{summary.get('evidence_coverage_pass_rate', 0):.4f} | {summary.get('avg_cost_usd', 0):.4f} | "
            f"{summary.get('avg_latency_ms', 0):.2f} |"
        )

    pair = report.get('pair_summary', {})
    lines.extend(
        [
            '',
            '## Pair Summary',
            '',
            f"- paired_sample_size: `{pair.get('paired_sample_size', 0)}`",
            f"- decision_change_rate: `{pair.get('decision_change_rate', 0)}`",
            f"- challenger_win_rate: `{pair.get('challenger_win_rate', 0)}`",
            f"- avg_cost_delta_usd: `{pair.get('avg_cost_delta_usd', 0)}`",
            f"- avg_latency_delta_ms: `{pair.get('avg_latency_delta_ms', 0)}`",
            '',
            '## Threshold Checks',
            '',
            '| Metric | Actual | Rule | Pass |',
            '|---|---:|---|---|',
        ]
    )
    for check in report.get('threshold_checks', []):
        lines.append(
            f"| {check.get('metric', '')} | {check.get('actual', 0)} | "
            f"`{check.get('operator', '')} {check.get('threshold', 0)}` | "
            f"{'YES' if check.get('pass') else 'NO'} |"
        )

    lines.extend(['', '## Top Pair Differences', '', '| Scenario | Primary | Challenger | Cost Delta | Latency Delta |', '|---|---|---|---:|---:|'])
    for row in pair.get('top_differences', [])[:20]:
        lines.append(
            f"| {row.get('scenario_key', '')} | {row.get('primary_action', '')} | {row.get('challenger_action', '')} | "
            f"{row.get('cost_delta_usd', 0)} | {row.get('latency_delta_ms', 0)} |"
        )

    return '\n'.join(lines) + '\n'


def load_and_build_m7_offline_eval(lookback_days: int = 30) -> dict[str, Any]:
    samples = load_report_samples(lookback_days=max(1, int(lookback_days)))
    return build_m7_offline_eval(samples, lookback_days=max(1, int(lookback_days)))
