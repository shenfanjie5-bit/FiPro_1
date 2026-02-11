from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from app.eval.m4_baseline import ReportSample, load_report_samples


INDUSTRY_BUCKETS: tuple[str, ...] = (
    'CONSUMER',
    'TECH',
    'FINANCE',
    'INDUSTRIAL',
    'ENERGY',
    'HEALTHCARE',
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stable_bucket(seed: str, buckets: tuple[str, ...]) -> str:
    if not buckets:
        return 'UNKNOWN'
    digest = int(hashlib.sha256(seed.encode('utf-8')).hexdigest()[:8], 16)
    return buckets[digest % len(buckets)]


def _extract_run_mode(report_json: dict[str, Any]) -> str:
    provenance = report_json.get('provenance', {})
    if isinstance(provenance, dict):
        mode = str(provenance.get('run_mode', '')).strip().upper()
        if mode:
            return mode
    return str(report_json.get('run_mode', 'LIVE')).strip().upper() or 'LIVE'


def _extract_model_primary(report_json: dict[str, Any]) -> str:
    model = report_json.get('provenance', {}).get('model', {})
    if isinstance(model, dict):
        value = str(model.get('primary', '')).strip()
        if value:
            return value
    return 'unknown-model'


def _infer_industry(report_json: dict[str, Any]) -> str:
    ticker = str(report_json.get('ticker', '')).strip()
    explicit = str(report_json.get('industry', '')).strip()
    if explicit:
        return explicit.upper()
    return _stable_bucket(ticker or 'unknown', INDUSTRY_BUCKETS)


def _infer_market_regime(report_json: dict[str, Any]) -> str:
    data_quality = str(report_json.get('data_quality', {}).get('status', 'OK')).upper()
    if data_quality == 'DEGRADED':
        return 'STRESSED'

    decision = report_json.get('decision', {})
    overall_score = _safe_int(decision.get('overall_score', 50), 50)
    high_risk = any(
        isinstance(item, dict) and str(item.get('severity', '')).upper() == 'HIGH'
        for item in report_json.get('risk_flags', [])
    )
    if high_risk:
        return 'VOLATILE'
    if overall_score >= 70:
        return 'UP'
    if overall_score <= 40:
        return 'DOWN'
    return 'RANGE'


def _infer_event_density(report_json: dict[str, Any]) -> tuple[int, str]:
    refs = report_json.get('evidence_refs', [])
    if not isinstance(refs, list):
        refs = []
    event_count = 0
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        ref_type = str(ref.get('type', '')).upper()
        if ref_type in {'NEWS_DOC', 'FILINGS', 'GRAPH_QUERY'}:
            event_count += 1
    if event_count <= 1:
        bucket = 'LOW'
    elif event_count <= 4:
        bucket = 'MEDIUM'
    else:
        bucket = 'HIGH'
    return event_count, bucket


def _scenario_key(sample: ReportSample) -> str:
    report = sample.report_json
    parts = (
        str(report.get('ticker', '')).strip(),
        str(report.get('asof', '')).strip(),
        str(report.get('strategy_version_id', '')).strip(),
        str(report.get('tier', sample.tier)).strip(),
        _extract_run_mode(report),
    )
    return '|'.join(parts)


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


def _dedupe_latest_samples(samples: list[ReportSample], *, enabled: bool) -> list[ReportSample]:
    if not enabled:
        return list(samples)
    ordered = sorted(samples, key=lambda item: _parse_iso_utc_safe(item.created_at), reverse=True)
    latest: dict[str, ReportSample] = {}
    passthrough: list[ReportSample] = []
    for sample in ordered:
        key = _scenario_key(sample)
        if key.strip('|'):
            if key not in latest:
                latest[key] = sample
        else:
            passthrough.append(sample)
    merged = list(latest.values()) + passthrough
    merged.sort(key=lambda item: _parse_iso_utc_safe(item.created_at), reverse=True)
    return merged


def _strata_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_industry: dict[str, int] = {}
    by_regime: dict[str, int] = {}
    by_event_density: dict[str, int] = {}
    by_cross: dict[str, int] = {}

    for row in rows:
        industry = str(row.get('industry', 'UNKNOWN'))
        regime = str(row.get('market_regime', 'UNKNOWN'))
        density = str(row.get('event_density', 'UNKNOWN'))
        by_industry[industry] = by_industry.get(industry, 0) + 1
        by_regime[regime] = by_regime.get(regime, 0) + 1
        by_event_density[density] = by_event_density.get(density, 0) + 1
        cross_key = f'{industry}|{regime}|{density}'
        by_cross[cross_key] = by_cross.get(cross_key, 0) + 1

    return {
        'industry': dict(sorted(by_industry.items())),
        'market_regime': dict(sorted(by_regime.items())),
        'event_density': dict(sorted(by_event_density.items())),
        'industry_regime_density': dict(sorted(by_cross.items())),
    }


def _dataset_version(rows: list[dict[str, Any]]) -> str:
    canonical = [
        {
            'scenario_key': row.get('scenario_key', ''),
            'report_id': row.get('report_id', ''),
            'run_mode': row.get('run_mode', ''),
            'model_primary': row.get('model_primary', ''),
            'industry': row.get('industry', ''),
            'market_regime': row.get('market_regime', ''),
            'event_density': row.get('event_density', ''),
        }
        for row in rows
    ]
    payload = json.dumps(sorted(canonical, key=lambda item: (item['scenario_key'], item['report_id'])), ensure_ascii=True, sort_keys=True)
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]
    return f'm7ds_{digest}'


def build_m7_offline_dataset(
    samples: list[ReportSample],
    *,
    lookback_days: int,
    dedupe_latest: bool = True,
) -> dict[str, Any]:
    effective_samples = _dedupe_latest_samples(samples, enabled=dedupe_latest)

    rows: list[dict[str, Any]] = []
    for sample in effective_samples:
        report = sample.report_json
        event_count, event_density = _infer_event_density(report)
        row = {
            'report_id': sample.report_id,
            'scenario_key': _scenario_key(sample),
            'ticker': str(report.get('ticker', '')).strip(),
            'asof': str(report.get('asof', '')).strip(),
            'strategy_version_id': str(report.get('strategy_version_id', '')).strip(),
            'tier': str(report.get('tier', sample.tier)).strip() or sample.tier,
            'run_mode': _extract_run_mode(report),
            'model_primary': _extract_model_primary(report),
            'industry': _infer_industry(report),
            'market_regime': _infer_market_regime(report),
            'event_count': event_count,
            'event_density': event_density,
            'created_at': sample.created_at,
            'cost_usd': round(_safe_float(sample.cost_usd, 0.0), 6),
            'latency_ms': _safe_int(sample.latency_ms, 0),
            'tool_calls': _safe_int(sample.tool_calls, 0),
            'decision_action': str(report.get('decision', {}).get('action', 'WATCH')).upper(),
            'decision_confidence': round(_safe_float(report.get('decision', {}).get('confidence', 0.0), 0.0), 6),
            'data_quality_status': str(report.get('data_quality', {}).get('status', 'OK')).upper(),
        }
        rows.append(row)

    version = _dataset_version(rows)
    return {
        'generated_at': _now_iso(),
        'dataset_version': version,
        'window': {'lookback_days': int(lookback_days), 'dedupe_latest': bool(dedupe_latest)},
        'raw_sample_size': len(samples),
        'effective_sample_size': len(effective_samples),
        'strata': _strata_counts(rows),
        'rows': rows,
    }


def render_m7_dataset_markdown(report: dict[str, Any]) -> str:
    lines = [
        '# M7 Offline Replay Dataset',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Dataset Version: `{report.get('dataset_version', '')}`",
        f"- Lookback Days: `{report.get('window', {}).get('lookback_days', 0)}`",
        f"- Dedupe Latest Scenario: `{bool(report.get('window', {}).get('dedupe_latest', True))}`",
        f"- Effective Sample Size: `{report.get('effective_sample_size', 0)}`",
        f"- Raw Sample Size: `{report.get('raw_sample_size', 0)}`",
        '',
        '## Stratification Summary',
        '',
        '| Dimension | Bucket | Count |',
        '|---|---|---:|',
    ]

    strata = report.get('strata', {})
    for dim in ('industry', 'market_regime', 'event_density'):
        items = strata.get(dim, {})
        for key, value in items.items():
            lines.append(f'| {dim} | {key} | {value} |')

    lines.extend(['', '## Rows (Top 20)', '', '| Report ID | Tier | Run Mode | Industry | Regime | Event Density |', '|---|---|---|---|---|---|'])
    for row in report.get('rows', [])[:20]:
        lines.append(
            f"| {row.get('report_id', '')} | {row.get('tier', '')} | {row.get('run_mode', '')} | "
            f"{row.get('industry', '')} | {row.get('market_regime', '')} | {row.get('event_density', '')} |"
        )
    return '\n'.join(lines) + '\n'


def load_and_build_m7_offline_dataset(lookback_days: int = 30, *, dedupe_latest: bool = True) -> dict[str, Any]:
    samples = load_report_samples(lookback_days=max(1, int(lookback_days)))
    return build_m7_offline_dataset(samples, lookback_days=max(1, int(lookback_days)), dedupe_latest=dedupe_latest)
