from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _to_jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=True, sort_keys=True, default=str))


def summarize_m6_load_results(
    rows: list[dict[str, Any]],
    *,
    requested_count: int,
    concurrency: int,
    wall_time_seconds: float,
) -> dict[str, Any]:
    total = len(rows)
    success = sum(1 for row in rows if bool(row.get('ok', False)))
    failed = total - success
    latencies = [_safe_float(row.get('latency_ms', 0.0), 0.0) for row in rows]
    costs = [_safe_float(row.get('cost_usd', 0.0), 0.0) for row in rows]
    degraded = sum(1 for row in rows if str(row.get('data_quality_status', 'OK')).upper() != 'OK')

    throughput_rps = 0.0
    if wall_time_seconds > 0:
        throughput_rps = round(total / wall_time_seconds, 6)

    return _to_jsonable(
        {
            'generated_at': _now_iso(),
            'config': {
                'requested_count': int(requested_count),
                'actual_count': total,
                'concurrency': int(concurrency),
                'wall_time_seconds': round(max(0.0, float(wall_time_seconds)), 6),
            },
            'summary': {
                'success_count': success,
                'failed_count': failed,
                'success_rate': _rate(success, total),
                'failure_rate': _rate(failed, total),
                'degraded_report_rate': _rate(degraded, total),
                'throughput_rps': throughput_rps,
                'latency_avg_ms': round(sum(latencies) / max(1, total), 6),
                'latency_p95_ms': _percentile(latencies, 0.95),
                'latency_p99_ms': _percentile(latencies, 0.99),
                'cost_avg_usd': round(sum(costs) / max(1, total), 6),
                'cost_p95_usd': _percentile(costs, 0.95),
            },
            'samples': rows[:200],
            'capacity_recommendation': {
                'recommended_concurrency': max(1, int(concurrency)),
                'notes': (
                    'Increase concurrency only when failure_rate remains <=0.03 and '
                    'latency_p95_ms <= 12000 under the same workload.'
                ),
            },
        }
    )


def render_m6_load_markdown(report: dict[str, Any]) -> str:
    summary = report.get('summary', {})
    cfg = report.get('config', {})
    lines = [
        '# M6 Load/Soak Baseline',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Requested Count: `{cfg.get('requested_count', 0)}`",
        f"- Actual Count: `{cfg.get('actual_count', 0)}`",
        f"- Concurrency: `{cfg.get('concurrency', 0)}`",
        f"- Wall Time Seconds: `{cfg.get('wall_time_seconds', 0)}`",
        '',
        '## Summary',
        '',
        f"- success_rate: `{summary.get('success_rate', 0.0)}`",
        f"- failure_rate: `{summary.get('failure_rate', 0.0)}`",
        f"- degraded_report_rate: `{summary.get('degraded_report_rate', 0.0)}`",
        f"- throughput_rps: `{summary.get('throughput_rps', 0.0)}`",
        f"- latency_avg_ms: `{summary.get('latency_avg_ms', 0.0)}`",
        f"- latency_p95_ms: `{summary.get('latency_p95_ms', 0.0)}`",
        f"- latency_p99_ms: `{summary.get('latency_p99_ms', 0.0)}`",
        f"- cost_avg_usd: `{summary.get('cost_avg_usd', 0.0)}`",
        f"- cost_p95_usd: `{summary.get('cost_p95_usd', 0.0)}`",
        '',
        '## Capacity Recommendation',
        '',
        f"- recommended_concurrency: `{report.get('capacity_recommendation', {}).get('recommended_concurrency', 0)}`",
        f"- notes: {report.get('capacity_recommendation', {}).get('notes', '')}",
    ]
    return '\n'.join(lines) + '\n'
