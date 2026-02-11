from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from app.validation.consistency import check_consistency
from app.validation.schema_validator import validate_report_schema


TIER_COVERAGE_RULES: dict[str, dict[str, Any]] = {
    'TIER0': {'min_total_refs': 1, 'min_type_count': 1, 'required_types': {'SNAPSHOT_FIELD'}},
    'TIER1': {'min_total_refs': 4, 'min_type_count': 2, 'required_types': {'SNAPSHOT_FIELD', 'NEWS_DOC'}},
    'TIER2': {'min_total_refs': 6, 'min_type_count': 2, 'required_types': {'SNAPSHOT_FIELD', 'NEWS_DOC'}},
}

M4_BASELINE_THRESHOLDS: dict[str, Any] = {
    'schema_pass_rate_min': 1.0,
    'citation_consistency_rate_min': 0.98,
    'evidence_coverage_pass_rate_min': 0.9,
    'latency_p95_ms_max': 7000,
    'avg_cost_usd_max': 0.9,
    'min_sample_size': 10,
}

TIER_COST_BUDGET: dict[str, float] = {'TIER0': 0.2, 'TIER1': 0.8, 'TIER2': 2.5}


@dataclass
class ReportSample:
    report_id: str
    tier: str
    created_at: str
    report_json: dict[str, Any]
    cost_usd: float
    latency_ms: int
    tool_calls: int


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso_utc_safe(value: str | datetime | None) -> datetime:
    try:
        if value is None:
            return datetime.fromtimestamp(0, timezone.utc)
        return _parse_iso_utc(value)
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


def _to_jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=True, sort_keys=True, default=str))


def _extract_tool_calls(report_json: dict[str, Any]) -> int:
    provenance = report_json.get('provenance', {})
    tool_stats = provenance.get('tool_call_stats', {}) if isinstance(provenance, dict) else {}
    return _safe_int(tool_stats.get('tool_calls', 0), 0)


def _extract_run_mode(report_json: dict[str, Any]) -> str:
    provenance = report_json.get('provenance', {})
    if isinstance(provenance, dict):
        run_mode = str(provenance.get('run_mode', '')).strip()
        if run_mode:
            return run_mode
    return str(report_json.get('run_mode', 'LIVE')).strip() or 'LIVE'


def _scenario_key(sample: ReportSample) -> tuple[str, str, str, str, str]:
    report = sample.report_json
    ticker = str(report.get('ticker', '')).strip()
    asof = str(report.get('asof', '')).strip()
    strategy_version_id = str(report.get('strategy_version_id', '')).strip()
    tier = str(report.get('tier', sample.tier)).strip() or sample.tier
    run_mode = _extract_run_mode(report)
    return (ticker, asof, strategy_version_id, tier, run_mode)


def _dedupe_latest_samples(samples: list[ReportSample], *, enabled: bool = True) -> list[ReportSample]:
    if not enabled:
        return list(samples)
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


def _extract_cost_latency(report_json: dict[str, Any]) -> tuple[float, int]:
    provenance = report_json.get('provenance', {})
    tool_stats = provenance.get('tool_call_stats', {}) if isinstance(provenance, dict) else {}
    return _safe_float(tool_stats.get('cost_usd_est', 0.0), 0.0), _safe_int(tool_stats.get('latency_ms', 0), 0)


def load_report_samples(lookback_days: int = 14) -> list[ReportSample]:
    if _use_postgres_primary():
        return _load_report_samples_postgres(lookback_days)
    return _load_report_samples_sqlite(lookback_days)


def _load_report_samples_sqlite(lookback_days: int) -> list[ReportSample]:
    db_path = _runtime_db_path()
    if not db_path.exists():
        return []
    cutoff = (_now_utc() - timedelta(days=max(1, int(lookback_days)))).isoformat()
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rows = conn.execute(
            'select r.report_id, r.tier, r.created_at, r.report_json, '
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

    samples: list[ReportSample] = []
    for row in rows:
        report_json = json.loads(str(row[3])) if row[3] else {}
        fallback_cost, fallback_latency = _extract_cost_latency(report_json)
        samples.append(
            ReportSample(
                report_id=str(row[0]),
                tier=str(row[1] or report_json.get('tier', 'TIER0')),
                created_at=str(row[2]),
                report_json=report_json,
                cost_usd=_safe_float(row[4], fallback_cost),
                latency_ms=_safe_int(row[5], fallback_latency),
                tool_calls=_extract_tool_calls(report_json),
            )
        )
    return samples


def _load_report_samples_postgres(lookback_days: int) -> list[ReportSample]:
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

    samples: list[ReportSample] = []
    for report in reports:
        report_json = dict(report.report_json or {})
        fallback_cost, fallback_latency = _extract_cost_latency(report_json)
        dlog = latest_dlog_by_report.get(str(report.id))
        samples.append(
            ReportSample(
                report_id=str(report.id),
                tier=str(report.tier or report_json.get('tier', 'TIER0')),
                created_at=report.created_at.isoformat(),
                report_json=report_json,
                cost_usd=_safe_float(getattr(dlog, 'cost_usd', fallback_cost), fallback_cost),
                latency_ms=_safe_int(getattr(dlog, 'latency_ms', fallback_latency), fallback_latency),
                tool_calls=_extract_tool_calls(report_json),
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


def _evidence_coverage_details(report_json: dict[str, Any], tier: str) -> dict[str, Any]:
    refs = report_json.get('evidence_refs', [])
    if not isinstance(refs, list):
        refs = []
    types = {str(ref.get('type', '')) for ref in refs if isinstance(ref, dict) and str(ref.get('type', '')).strip()}
    expanded_types = set(types)
    if 'FILINGS' in types:
        expanded_types.add('NEWS_DOC')
    if 'NEWS_DOC' in types:
        expanded_types.add('FILINGS')
    rules = TIER_COVERAGE_RULES.get(tier, TIER_COVERAGE_RULES['TIER0'])
    missing_rules: list[str] = []
    if len(refs) < int(rules['min_total_refs']):
        missing_rules.append(f"min_total_refs<{int(rules['min_total_refs'])}")
    if len(types) < int(rules['min_type_count']):
        missing_rules.append(f"min_type_count<{int(rules['min_type_count'])}")
    required_types = set(rules['required_types'])
    for required in sorted(required_types):
        if required not in expanded_types:
            missing_rules.append(f'missing_type={required}')
    return {
        'ok': not missing_rules,
        'total_refs': len(refs),
        'types': sorted(types),
        'missing_rules': missing_rules,
    }


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _summarize_samples(samples: list[ReportSample]) -> dict[str, Any]:
    total = len(samples)
    if total == 0:
        return {
            'sample_size': 0,
            'schema_pass_rate': 0.0,
            'citation_consistency_rate': 0.0,
            'consistency_pass_rate': 0.0,
            'evidence_coverage_pass_rate': 0.0,
            'avg_evidence_refs': 0.0,
            'avg_latency_ms': 0.0,
            'latency_p95_ms': 0.0,
            'avg_cost_usd': 0.0,
            'cost_p95_usd': 0.0,
            'avg_tool_calls': 0.0,
            'failures': {
                'schema_failed': 0,
                'citation_inconsistent': 0,
                'consistency_failed': 0,
                'evidence_coverage_failed': 0,
            },
        }

    schema_pass = 0
    citation_pass = 0
    consistency_pass = 0
    coverage_pass = 0
    evidence_counts: list[float] = []
    latencies: list[float] = []
    costs: list[float] = []
    tool_calls: list[float] = []
    low_coverage_reports: list[dict[str, Any]] = []

    for sample in samples:
        report = sample.report_json
        ok, _ = validate_report_schema(report)
        if ok:
            schema_pass += 1

        consistency_errors = check_consistency(report)
        citation_errors = [err for err in consistency_errors if 'references missing evidence_id=' in err]
        if not citation_errors:
            citation_pass += 1
        if not consistency_errors:
            consistency_pass += 1

        coverage = _evidence_coverage_details(report, sample.tier)
        if coverage.get('ok', False):
            coverage_pass += 1
        else:
            low_coverage_reports.append(
                {
                    'report_id': sample.report_id,
                    'tier': sample.tier,
                    'created_at': sample.created_at,
                    'evidence_refs': int(coverage.get('total_refs', 0)),
                    'present_types': list(coverage.get('types', [])),
                    'missing_rules': list(coverage.get('missing_rules', [])),
                }
            )

        refs = report.get('evidence_refs', [])
        evidence_counts.append(float(len(refs)) if isinstance(refs, list) else 0.0)
        latencies.append(float(sample.latency_ms))
        costs.append(float(sample.cost_usd))
        tool_calls.append(float(sample.tool_calls))

    summary = {
        'sample_size': total,
        'schema_pass_rate': _rate(schema_pass, total),
        'citation_consistency_rate': _rate(citation_pass, total),
        'consistency_pass_rate': _rate(consistency_pass, total),
        'evidence_coverage_pass_rate': _rate(coverage_pass, total),
        'avg_evidence_refs': round(sum(evidence_counts) / total, 6),
        'avg_latency_ms': round(sum(latencies) / total, 6),
        'latency_p95_ms': _percentile(latencies, 0.95),
        'avg_cost_usd': round(sum(costs) / total, 6),
        'cost_p95_usd': _percentile(costs, 0.95),
        'avg_tool_calls': round(sum(tool_calls) / total, 6),
        'failures': {
            'schema_failed': total - schema_pass,
            'citation_inconsistent': total - citation_pass,
            'consistency_failed': total - consistency_pass,
            'evidence_coverage_failed': total - coverage_pass,
        },
        'low_coverage_reports': low_coverage_reports[:50],
    }
    return summary


def _collect_low_coverage_reports(samples: list[ReportSample], *, tier_filter: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for sample in samples:
        if tier_filter and sample.tier != tier_filter:
            continue
        coverage = _evidence_coverage_details(sample.report_json, sample.tier)
        if coverage.get('ok', False):
            continue
        items.append(
            {
                'report_id': sample.report_id,
                'tier': sample.tier,
                'created_at': sample.created_at,
                'evidence_refs': int(coverage.get('total_refs', 0)),
                'present_types': list(coverage.get('types', [])),
                'missing_rules': list(coverage.get('missing_rules', [])),
            }
        )
    return items[: max(1, int(limit))]


def _evaluate_thresholds(summary: dict[str, Any], sample_size: int) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add_min_check(name: str, actual: float, threshold: float) -> None:
        checks.append(
            {
                'metric': name,
                'actual': round(actual, 6),
                'operator': '>=',
                'threshold': round(threshold, 6),
                'pass': bool(actual >= threshold),
            }
        )

    def add_max_check(name: str, actual: float, threshold: float) -> None:
        checks.append(
            {
                'metric': name,
                'actual': round(actual, 6),
                'operator': '<=',
                'threshold': round(threshold, 6),
                'pass': bool(actual <= threshold),
            }
        )

    add_min_check('schema_pass_rate', summary['schema_pass_rate'], float(M4_BASELINE_THRESHOLDS['schema_pass_rate_min']))
    add_min_check(
        'citation_consistency_rate',
        summary['citation_consistency_rate'],
        float(M4_BASELINE_THRESHOLDS['citation_consistency_rate_min']),
    )
    add_min_check(
        'evidence_coverage_pass_rate',
        summary['evidence_coverage_pass_rate'],
        float(M4_BASELINE_THRESHOLDS['evidence_coverage_pass_rate_min']),
    )
    add_max_check('latency_p95_ms', summary['latency_p95_ms'], float(M4_BASELINE_THRESHOLDS['latency_p95_ms_max']))
    add_max_check('avg_cost_usd', summary['avg_cost_usd'], float(M4_BASELINE_THRESHOLDS['avg_cost_usd_max']))
    add_min_check('sample_size', float(sample_size), float(M4_BASELINE_THRESHOLDS['min_sample_size']))
    return checks


def build_m4_quality_baseline(samples: list[ReportSample], *, lookback_days: int, dedupe_latest: bool = True) -> dict[str, Any]:
    generated_at = _now_utc().isoformat()
    raw_samples = list(samples)
    effective_samples = _dedupe_latest_samples(raw_samples, enabled=dedupe_latest)
    overall = _summarize_samples(effective_samples)
    by_tier: dict[str, Any] = {}
    for tier in ('TIER0', 'TIER1', 'TIER2'):
        tier_samples = [sample for sample in effective_samples if sample.tier == tier]
        tier_summary = _summarize_samples(tier_samples)
        tier_summary['cost_budget_max_usd'] = TIER_COST_BUDGET[tier]
        tier_summary['cost_budget_ok'] = tier_summary['avg_cost_usd'] <= TIER_COST_BUDGET[tier] if tier_samples else True
        by_tier[tier] = tier_summary

    threshold_checks = _evaluate_thresholds(overall, len(effective_samples))
    threshold_pass = all(bool(item['pass']) for item in threshold_checks)
    overall_status = 'PASS' if threshold_pass else 'FAIL'
    if len(effective_samples) == 0:
        overall_status = 'INSUFFICIENT_DATA'
    tier1_low_coverage_reports = _collect_low_coverage_reports(effective_samples, tier_filter='TIER1', limit=100)
    raw_tier1_low_coverage_reports = _collect_low_coverage_reports(raw_samples, tier_filter='TIER1', limit=200)

    return {
        'generated_at': generated_at,
        'window': {'lookback_days': int(lookback_days), 'dedupe_latest': bool(dedupe_latest)},
        'source': {
            'mode': 'postgres_primary' if _use_postgres_primary() else 'sqlite_runtime',
            'database_url': _database_url() if _use_postgres_primary() else '',
            'runtime_db': str(_runtime_db_path()),
        },
        'thresholds': _to_jsonable(M4_BASELINE_THRESHOLDS),
        'overall_status': overall_status,
        'raw_sample_size': len(raw_samples),
        'effective_sample_size': len(effective_samples),
        'overall': overall,
        'by_tier': by_tier,
        'threshold_checks': threshold_checks,
        'tier1_low_coverage_reports': tier1_low_coverage_reports,
        'raw_tier1_low_coverage_reports': raw_tier1_low_coverage_reports,
    }


def render_m4_baseline_markdown(report: dict[str, Any]) -> str:
    generated_at = report.get('generated_at', '')
    status = report.get('overall_status', 'UNKNOWN')
    lookback_days = report.get('window', {}).get('lookback_days', '')
    dedupe_latest = bool(report.get('window', {}).get('dedupe_latest', True))
    overall = report.get('overall', {})
    raw_sample_size = report.get('raw_sample_size', overall.get('sample_size', 0))
    effective_sample_size = report.get('effective_sample_size', overall.get('sample_size', 0))

    lines = [
        '# M4 Quality Baseline',
        '',
        f'- Generated At: `{generated_at}`',
        f'- Lookback Days: `{lookback_days}`',
        f'- Dedupe Latest Scenario: `{dedupe_latest}`',
        f'- Overall Status: `{status}`',
        f"- Effective Sample Size: `{effective_sample_size}`",
        f"- Raw Sample Size: `{raw_sample_size}`",
        '',
        '## Core Metrics',
        '',
        '| Metric | Value |',
        '|---|---:|',
        f"| schema_pass_rate | {overall.get('schema_pass_rate', 0):.4f} |",
        f"| citation_consistency_rate | {overall.get('citation_consistency_rate', 0):.4f} |",
        f"| evidence_coverage_pass_rate | {overall.get('evidence_coverage_pass_rate', 0):.4f} |",
        f"| avg_evidence_refs | {overall.get('avg_evidence_refs', 0):.2f} |",
        f"| avg_latency_ms | {overall.get('avg_latency_ms', 0):.2f} |",
        f"| latency_p95_ms | {overall.get('latency_p95_ms', 0):.2f} |",
        f"| avg_cost_usd | {overall.get('avg_cost_usd', 0):.4f} |",
        f"| cost_p95_usd | {overall.get('cost_p95_usd', 0):.4f} |",
        '',
        '## Threshold Checks',
        '',
        '| Metric | Actual | Rule | Pass |',
        '|---|---:|---|---|',
    ]

    for check in report.get('threshold_checks', []):
        metric = check.get('metric', '')
        actual = check.get('actual', 0)
        operator = check.get('operator', '')
        threshold = check.get('threshold', 0)
        ok = 'YES' if check.get('pass') else 'NO'
        lines.append(f'| {metric} | {actual} | `{operator} {threshold}` | {ok} |')

    lines.extend(['', '## Tier Breakdown', '', '| Tier | Sample | Coverage Rate | Avg Cost | Budget Max | Budget OK |'])
    lines.append('|---|---:|---:|---:|---:|---|')
    by_tier = report.get('by_tier', {})
    for tier in ('TIER0', 'TIER1', 'TIER2'):
        summary = by_tier.get(tier, {})
        lines.append(
            f"| {tier} | {summary.get('sample_size', 0)} | {summary.get('evidence_coverage_pass_rate', 0):.4f} | "
            f"{summary.get('avg_cost_usd', 0):.4f} | {summary.get('cost_budget_max_usd', 0):.4f} | "
            f"{'YES' if summary.get('cost_budget_ok') else 'NO'} |"
        )

    tier1_low_coverage = report.get('tier1_low_coverage_reports', [])
    raw_tier1_low_coverage = report.get('raw_tier1_low_coverage_reports', [])
    lines.extend(['', '## TIER1 Low Coverage Reports', ''])
    lines.append(f"- Effective Low Coverage Count: `{len(tier1_low_coverage)}`")
    lines.append(f"- Raw Low Coverage Count: `{len(raw_tier1_low_coverage)}`")
    lines.append('')
    if not tier1_low_coverage:
        lines.append('- No low-coverage TIER1 reports in the selected window.')
    else:
        lines.extend(['| Report ID | Created At | Evidence Refs | Missing Rules |', '|---|---|---:|---|'])
        for item in tier1_low_coverage[:20]:
            lines.append(
                f"| {item.get('report_id', '')} | {item.get('created_at', '')} | "
                f"{item.get('evidence_refs', 0)} | {','.join(item.get('missing_rules', []))} |"
            )

    return '\n'.join(lines) + '\n'
