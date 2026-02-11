from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from app.validation.schema_validator import validate_report_schema


M6_RELIABILITY_THRESHOLDS: dict[str, float] = {
    'min_sample_size': 10,
    'success_rate_min': 0.97,
    'failure_rate_max': 0.03,
    'schema_pass_rate_min': 0.99,
    'latency_p95_ms_max': 12000,
    'cost_p95_usd_max': 2.5,
}


@dataclass
class ReliabilitySample:
    report_id: str
    tier: str
    status: str
    created_at: str
    report_json: dict[str, Any]
    cost_usd: float
    latency_ms: int


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_utc_safe(value: str | None) -> datetime:
    try:
        if not value:
            return datetime.fromtimestamp(0, timezone.utc)
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.fromtimestamp(0, timezone.utc)


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


def _database_url() -> str:
    return os.getenv('DATABASE_URL', '').strip()


def _use_postgres_primary() -> bool:
    return _database_url().startswith('postgresql')


def _runtime_db_path() -> Path:
    return Path(os.getenv('WORKFLOW_RUNTIME_DB', os.getenv('WORKFLOW_CHECKPOINT_DB', 'checkpoint.db')))


def _extract_cost_latency(report_json: dict[str, Any]) -> tuple[float, int]:
    provenance = report_json.get('provenance', {})
    tool_stats = provenance.get('tool_call_stats', {}) if isinstance(provenance, dict) else {}
    return _safe_float(tool_stats.get('cost_usd_est', 0.0), 0.0), _safe_int(tool_stats.get('latency_ms', 0), 0)


def _extract_run_mode(report_json: dict[str, Any]) -> str:
    provenance = report_json.get('provenance', {})
    if isinstance(provenance, dict):
        mode = str(provenance.get('run_mode', '')).strip()
        if mode:
            return mode
    return str(report_json.get('run_mode', 'LIVE')).strip() or 'LIVE'


def _scenario_key(sample: ReliabilitySample) -> tuple[str, str, str, str, str]:
    report = sample.report_json
    ticker = str(report.get('ticker', '')).strip()
    asof = str(report.get('asof', '')).strip()
    strategy_version_id = str(report.get('strategy_version_id', '')).strip()
    tier = str(report.get('tier', sample.tier)).strip() or sample.tier
    run_mode = _extract_run_mode(report)
    return (ticker, asof, strategy_version_id, tier, run_mode)


def _dedupe_latest_samples(samples: list[ReliabilitySample], *, enabled: bool = True) -> list[ReliabilitySample]:
    if not enabled:
        return list(samples)
    ordered = sorted(samples, key=lambda item: _parse_iso_utc_safe(item.created_at), reverse=True)
    deduped: dict[tuple[str, str, str, str, str], ReliabilitySample] = {}
    passthrough: list[ReliabilitySample] = []
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


def load_reliability_samples(lookback_days: int = 7) -> list[ReliabilitySample]:
    if _use_postgres_primary():
        return _load_reliability_samples_postgres(lookback_days)
    return _load_reliability_samples_sqlite(lookback_days)


def _load_reliability_samples_sqlite(lookback_days: int) -> list[ReliabilitySample]:
    db_path = _runtime_db_path()
    if not db_path.exists():
        return []
    cutoff = (_now_utc() - timedelta(days=max(1, int(lookback_days)))).isoformat()
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rows = conn.execute(
            'select r.report_id, r.tier, r.status, r.created_at, r.report_json, '
            'coalesce(d.cost_usd, 0), coalesce(d.latency_ms, 0) '
            'from reports r '
            'left join ('
            '  select report_id, cost_usd, latency_ms, created_at '
            '  from decision_logs '
            '  where (report_id, created_at) in ('
            '    select report_id, max(created_at) from decision_logs group by report_id'
            '  ) '
            ') d on d.report_id = r.report_id '
            'where r.created_at >= ? '
            'order by r.created_at desc',
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    samples: list[ReliabilitySample] = []
    for row in rows:
        report_json = json.loads(str(row[4])) if row[4] else {}
        fallback_cost, fallback_latency = _extract_cost_latency(report_json)
        samples.append(
            ReliabilitySample(
                report_id=str(row[0]),
                tier=str(row[1] or report_json.get('tier', 'TIER0')),
                status=str(row[2] or 'UNKNOWN'),
                created_at=str(row[3]),
                report_json=report_json,
                cost_usd=_safe_float(row[5], fallback_cost),
                latency_ms=_safe_int(row[6], fallback_latency),
            )
        )
    return samples


def _load_reliability_samples_postgres(lookback_days: int) -> list[ReliabilitySample]:
    from sqlalchemy import desc, select

    from app.db.models import DecisionLog, Report
    from app.db.session import SessionLocal

    cutoff = _now_utc() - timedelta(days=max(1, int(lookback_days)))
    session = SessionLocal()
    try:
        reports = session.execute(
            select(Report).where(Report.created_at >= cutoff).order_by(desc(Report.created_at))
        ).scalars().all()
        dlogs = session.execute(
            select(DecisionLog).where(DecisionLog.created_at >= cutoff).order_by(desc(DecisionLog.created_at))
        ).scalars().all()
    finally:
        session.close()

    latest_dlog_by_report: dict[str, Any] = {}
    for dlog in dlogs:
        key = str(dlog.report_id)
        if key not in latest_dlog_by_report:
            latest_dlog_by_report[key] = dlog

    samples: list[ReliabilitySample] = []
    for report in reports:
        report_json = dict(report.report_json or {})
        fallback_cost, fallback_latency = _extract_cost_latency(report_json)
        dlog = latest_dlog_by_report.get(str(report.id))
        samples.append(
            ReliabilitySample(
                report_id=str(report.id),
                tier=str(report.tier or report_json.get('tier', 'TIER0')),
                status=str(report.status or 'UNKNOWN'),
                created_at=report.created_at.isoformat(),
                report_json=report_json,
                cost_usd=_safe_float(getattr(dlog, 'cost_usd', fallback_cost), fallback_cost),
                latency_ms=_safe_int(getattr(dlog, 'latency_ms', fallback_latency), fallback_latency),
            )
        )
    return samples


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


def _schema_pass(report_json: dict[str, Any]) -> bool:
    ok, _ = validate_report_schema(report_json)
    return bool(ok)


def _daily_summary(samples: list[ReliabilitySample]) -> list[dict[str, Any]]:
    grouped: dict[str, list[ReliabilitySample]] = {}
    for sample in samples:
        day = _parse_iso_utc_safe(sample.created_at).date().isoformat()
        grouped.setdefault(day, []).append(sample)

    output: list[dict[str, Any]] = []
    for day in sorted(grouped.keys()):
        rows = grouped[day]
        total = len(rows)
        success = sum(1 for row in rows if str(row.status).upper() != 'FAILED')
        schema_pass = sum(1 for row in rows if _schema_pass(row.report_json))
        latency_values = [float(row.latency_ms) for row in rows]
        cost_values = [float(row.cost_usd) for row in rows]
        output.append(
            {
                'date': day,
                'sample_size': total,
                'success_rate': _rate(success, total),
                'failure_rate': _rate(total - success, total),
                'schema_pass_rate': _rate(schema_pass, total),
                'latency_p95_ms': _percentile(latency_values, 0.95),
                'cost_p95_usd': _percentile(cost_values, 0.95),
            }
        )
    return output


def _summary(samples: list[ReliabilitySample]) -> dict[str, Any]:
    total = len(samples)
    if total == 0:
        return {
            'sample_size': 0,
            'success_rate': 0.0,
            'failure_rate': 0.0,
            'schema_pass_rate': 0.0,
            'latency_p95_ms': 0.0,
            'avg_latency_ms': 0.0,
            'cost_p95_usd': 0.0,
            'avg_cost_usd': 0.0,
            'degraded_report_rate': 0.0,
            'retry_report_rate': 0.0,
        }

    success_count = 0
    schema_pass_count = 0
    degraded_count = 0
    retry_count = 0
    latency_values: list[float] = []
    cost_values: list[float] = []
    for sample in samples:
        if str(sample.status).upper() != 'FAILED':
            success_count += 1
        if _schema_pass(sample.report_json):
            schema_pass_count += 1
        dq_status = str(sample.report_json.get('data_quality', {}).get('status', 'OK')).upper()
        if dq_status != 'OK':
            degraded_count += 1
        retry_total = _safe_int(sample.report_json.get('provenance', {}).get('tool_call_stats', {}).get('retry_count', 0), 0)
        if retry_total > 0:
            retry_count += 1
        latency_values.append(float(sample.latency_ms))
        cost_values.append(float(sample.cost_usd))

    return {
        'sample_size': total,
        'success_rate': _rate(success_count, total),
        'failure_rate': _rate(total - success_count, total),
        'schema_pass_rate': _rate(schema_pass_count, total),
        'latency_p95_ms': _percentile(latency_values, 0.95),
        'avg_latency_ms': round(sum(latency_values) / total, 6),
        'cost_p95_usd': _percentile(cost_values, 0.95),
        'avg_cost_usd': round(sum(cost_values) / total, 6),
        'degraded_report_rate': _rate(degraded_count, total),
        'retry_report_rate': _rate(retry_count, total),
    }


def _threshold_checks(summary: dict[str, Any]) -> list[dict[str, Any]]:
    checks = [
        {
            'metric': 'sample_size',
            'actual': _safe_float(summary.get('sample_size', 0), 0.0),
            'operator': '>=',
            'threshold': M6_RELIABILITY_THRESHOLDS['min_sample_size'],
            'pass': _safe_float(summary.get('sample_size', 0), 0.0) >= M6_RELIABILITY_THRESHOLDS['min_sample_size'],
        },
        {
            'metric': 'success_rate',
            'actual': _safe_float(summary.get('success_rate', 0.0), 0.0),
            'operator': '>=',
            'threshold': M6_RELIABILITY_THRESHOLDS['success_rate_min'],
            'pass': _safe_float(summary.get('success_rate', 0.0), 0.0) >= M6_RELIABILITY_THRESHOLDS['success_rate_min'],
        },
        {
            'metric': 'failure_rate',
            'actual': _safe_float(summary.get('failure_rate', 0.0), 0.0),
            'operator': '<=',
            'threshold': M6_RELIABILITY_THRESHOLDS['failure_rate_max'],
            'pass': _safe_float(summary.get('failure_rate', 0.0), 0.0) <= M6_RELIABILITY_THRESHOLDS['failure_rate_max'],
        },
        {
            'metric': 'schema_pass_rate',
            'actual': _safe_float(summary.get('schema_pass_rate', 0.0), 0.0),
            'operator': '>=',
            'threshold': M6_RELIABILITY_THRESHOLDS['schema_pass_rate_min'],
            'pass': _safe_float(summary.get('schema_pass_rate', 0.0), 0.0) >= M6_RELIABILITY_THRESHOLDS['schema_pass_rate_min'],
        },
        {
            'metric': 'latency_p95_ms',
            'actual': _safe_float(summary.get('latency_p95_ms', 0.0), 0.0),
            'operator': '<=',
            'threshold': M6_RELIABILITY_THRESHOLDS['latency_p95_ms_max'],
            'pass': _safe_float(summary.get('latency_p95_ms', 0.0), 0.0) <= M6_RELIABILITY_THRESHOLDS['latency_p95_ms_max'],
        },
        {
            'metric': 'cost_p95_usd',
            'actual': _safe_float(summary.get('cost_p95_usd', 0.0), 0.0),
            'operator': '<=',
            'threshold': M6_RELIABILITY_THRESHOLDS['cost_p95_usd_max'],
            'pass': _safe_float(summary.get('cost_p95_usd', 0.0), 0.0) <= M6_RELIABILITY_THRESHOLDS['cost_p95_usd_max'],
        },
    ]
    for item in checks:
        item['actual'] = round(float(item['actual']), 6)
        item['threshold'] = round(float(item['threshold']), 6)
    return checks


def build_m6_reliability_panel(
    samples: list[ReliabilitySample],
    *,
    lookback_days: int,
    dedupe_latest: bool = True,
) -> dict[str, Any]:
    effective_samples = _dedupe_latest_samples(samples, enabled=dedupe_latest)
    summary = _summary(effective_samples)
    checks = _threshold_checks(summary)
    status = 'PASS' if all(bool(item.get('pass')) for item in checks) else 'FAIL'
    if not effective_samples:
        status = 'INSUFFICIENT_DATA'

    return {
        'generated_at': _now_utc().isoformat(),
        'window': {'lookback_days': int(lookback_days), 'dedupe_latest': bool(dedupe_latest)},
        'overall_status': status,
        'raw_sample_size': len(samples),
        'effective_sample_size': len(effective_samples),
        'summary': summary,
        'thresholds': dict(M6_RELIABILITY_THRESHOLDS),
        'threshold_checks': checks,
        'daily_trend': _daily_summary(effective_samples),
        'alert_lines': {
            'failure_rate': {
                'warn': round(M6_RELIABILITY_THRESHOLDS['failure_rate_max'] * 0.8, 6),
                'critical': round(M6_RELIABILITY_THRESHOLDS['failure_rate_max'], 6),
            },
            'latency_p95_ms': {
                'warn': round(M6_RELIABILITY_THRESHOLDS['latency_p95_ms_max'] * 0.85, 6),
                'critical': round(M6_RELIABILITY_THRESHOLDS['latency_p95_ms_max'], 6),
            },
            'cost_p95_usd': {
                'warn': round(M6_RELIABILITY_THRESHOLDS['cost_p95_usd_max'] * 0.85, 6),
                'critical': round(M6_RELIABILITY_THRESHOLDS['cost_p95_usd_max'], 6),
            },
            'schema_pass_rate': {
                'warn': round(M6_RELIABILITY_THRESHOLDS['schema_pass_rate_min'] - 0.01, 6),
                'critical': round(M6_RELIABILITY_THRESHOLDS['schema_pass_rate_min'] - 0.02, 6),
            },
        },
    }


def load_and_build_m6_reliability_panel(lookback_days: int = 7, *, dedupe_latest: bool = True) -> dict[str, Any]:
    samples = load_reliability_samples(lookback_days=max(1, int(lookback_days)))
    return build_m6_reliability_panel(samples, lookback_days=max(1, int(lookback_days)), dedupe_latest=dedupe_latest)


def render_m6_reliability_markdown(report: dict[str, Any]) -> str:
    summary = report.get('summary', {})
    lines = [
        '# M6 Reliability Panel',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Lookback Days: `{report.get('window', {}).get('lookback_days', 0)}`",
        f"- Dedupe Latest Scenario: `{report.get('window', {}).get('dedupe_latest', True)}`",
        f"- Overall Status: `{report.get('overall_status', 'UNKNOWN')}`",
        f"- Raw Sample Size: `{report.get('raw_sample_size', 0)}`",
        f"- Effective Sample Size: `{report.get('effective_sample_size', 0)}`",
        '',
        '## Core Metrics',
        '',
        f"- success_rate: `{summary.get('success_rate', 0.0)}`",
        f"- failure_rate: `{summary.get('failure_rate', 0.0)}`",
        f"- schema_pass_rate: `{summary.get('schema_pass_rate', 0.0)}`",
        f"- latency_p95_ms: `{summary.get('latency_p95_ms', 0.0)}`",
        f"- cost_p95_usd: `{summary.get('cost_p95_usd', 0.0)}`",
        f"- degraded_report_rate: `{summary.get('degraded_report_rate', 0.0)}`",
        f"- retry_report_rate: `{summary.get('retry_report_rate', 0.0)}`",
        '',
        '## Threshold Checks',
        '',
    ]
    for item in report.get('threshold_checks', []):
        lines.append(
            f"- [{ 'PASS' if item.get('pass') else 'FAIL' }] `{item.get('metric')}`: "
            f"{item.get('actual')} {item.get('operator')} {item.get('threshold')}"
        )
    lines.extend(['', '## Daily Trend', ''])
    trend = list(report.get('daily_trend', []))
    if not trend:
        lines.append('- (no samples)')
    else:
        for row in trend:
            lines.append(
                f"- {row.get('date')}: sample={row.get('sample_size', 0)}, "
                f"success={row.get('success_rate', 0.0)}, failure={row.get('failure_rate', 0.0)}, "
                f"schema={row.get('schema_pass_rate', 0.0)}, latency_p95={row.get('latency_p95_ms', 0.0)}, "
                f"cost_p95={row.get('cost_p95_usd', 0.0)}"
            )
    return '\n'.join(lines) + '\n'
