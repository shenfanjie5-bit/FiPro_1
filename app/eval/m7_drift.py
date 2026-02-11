from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from app.eval.m4_baseline import ReportSample, load_report_samples
from app.eval.m7_dataset import build_m7_offline_dataset


M7_DRIFT_THRESHOLDS: dict[str, float] = {
    'psi_warning': 0.20,
    'psi_critical': 0.30,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _confidence_bucket(value: float) -> str:
    if value < 0.4:
        return '[0.0,0.4)'
    if value < 0.6:
        return '[0.4,0.6)'
    if value < 0.8:
        return '[0.6,0.8)'
    return '[0.8,1.0]'


def _distribution(values: list[str]) -> dict[str, float]:
    total = max(1, len(values))
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or 'UNKNOWN')
        counts[key] = counts.get(key, 0) + 1
    return {key: round(count / total, 6) for key, count in sorted(counts.items())}


def _psi(baseline: dict[str, float], current: dict[str, float]) -> float:
    eps = 1e-6
    keys = set(baseline.keys()) | set(current.keys())
    value = 0.0
    for key in keys:
        b = max(eps, _safe_float(baseline.get(key, 0.0), 0.0))
        c = max(eps, _safe_float(current.get(key, 0.0), 0.0))
        value += (c - b) * math.log(c / b)
    return round(value, 6)


def _dimension_report(name: str, baseline_values: list[str], current_values: list[str]) -> dict[str, Any]:
    baseline_dist = _distribution(baseline_values)
    current_dist = _distribution(current_values)
    psi = _psi(baseline_dist, current_dist)
    status = 'OK'
    if psi >= M7_DRIFT_THRESHOLDS['psi_critical']:
        status = 'CRITICAL'
    elif psi >= M7_DRIFT_THRESHOLDS['psi_warning']:
        status = 'WARN'
    return {
        'dimension': name,
        'psi': psi,
        'status': status,
        'baseline_distribution': baseline_dist,
        'current_distribution': current_dist,
    }


def build_m7_drift_report(
    baseline_rows: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    *,
    baseline_label: str = 'baseline',
    current_label: str = 'current',
) -> dict[str, Any]:
    if not baseline_rows or not current_rows:
        return {
            'generated_at': _now_iso(),
            'overall_status': 'INSUFFICIENT_DATA',
            'thresholds': dict(M7_DRIFT_THRESHOLDS),
            'baseline_label': baseline_label,
            'current_label': current_label,
            'baseline_sample_size': len(baseline_rows),
            'current_sample_size': len(current_rows),
            'dimensions': [],
            'alerts': [],
        }

    dimensions = [
        _dimension_report(
            'data.industry',
            [str(item.get('industry', 'UNKNOWN')) for item in baseline_rows],
            [str(item.get('industry', 'UNKNOWN')) for item in current_rows],
        ),
        _dimension_report(
            'data.market_regime',
            [str(item.get('market_regime', 'UNKNOWN')) for item in baseline_rows],
            [str(item.get('market_regime', 'UNKNOWN')) for item in current_rows],
        ),
        _dimension_report(
            'data.event_density',
            [str(item.get('event_density', 'UNKNOWN')) for item in baseline_rows],
            [str(item.get('event_density', 'UNKNOWN')) for item in current_rows],
        ),
        _dimension_report(
            'behavior.action',
            [str(item.get('decision_action', 'WATCH')) for item in baseline_rows],
            [str(item.get('decision_action', 'WATCH')) for item in current_rows],
        ),
        _dimension_report(
            'behavior.confidence_bucket',
            [_confidence_bucket(_safe_float(item.get('decision_confidence', 0.0), 0.0)) for item in baseline_rows],
            [_confidence_bucket(_safe_float(item.get('decision_confidence', 0.0), 0.0)) for item in current_rows],
        ),
    ]

    critical = [item for item in dimensions if item.get('status') == 'CRITICAL']
    warning = [item for item in dimensions if item.get('status') == 'WARN']
    alerts = []
    for item in critical + warning:
        alerts.append(
            {
                'severity': 'critical' if item.get('status') == 'CRITICAL' else 'warning',
                'dimension': item.get('dimension', ''),
                'psi': item.get('psi', 0.0),
                'message': f"{item.get('dimension', '')} PSI={item.get('psi', 0.0)} exceeds threshold",
            }
        )

    overall_status = 'PASS'
    if critical:
        overall_status = 'FAIL'
    elif warning:
        overall_status = 'WARN'

    return {
        'generated_at': _now_iso(),
        'overall_status': overall_status,
        'thresholds': dict(M7_DRIFT_THRESHOLDS),
        'baseline_label': baseline_label,
        'current_label': current_label,
        'baseline_sample_size': len(baseline_rows),
        'current_sample_size': len(current_rows),
        'dimensions': dimensions,
        'alerts': alerts,
    }


def render_m7_drift_markdown(report: dict[str, Any]) -> str:
    lines = [
        '# M7 Drift Monitor',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Overall Status: `{report.get('overall_status', 'UNKNOWN')}`",
        f"- Baseline Label: `{report.get('baseline_label', '')}`",
        f"- Current Label: `{report.get('current_label', '')}`",
        f"- Baseline Sample Size: `{report.get('baseline_sample_size', 0)}`",
        f"- Current Sample Size: `{report.get('current_sample_size', 0)}`",
        '',
        '## PSI by Dimension',
        '',
        '| Dimension | PSI | Status |',
        '|---|---:|---|',
    ]
    for item in report.get('dimensions', []):
        lines.append(f"| {item.get('dimension', '')} | {item.get('psi', 0)} | {item.get('status', 'OK')} |")

    lines.extend(['', '## Alerts', ''])
    alerts = report.get('alerts', [])
    if not alerts:
        lines.append('- No drift alerts.')
    else:
        for alert in alerts:
            lines.append(
                f"- [{str(alert.get('severity', '')).upper()}] `{alert.get('dimension', '')}`: "
                f"{alert.get('message', '')}"
            )

    return '\n'.join(lines) + '\n'


def dataset_rows_from_samples(samples: list[ReportSample], *, lookback_days: int) -> list[dict[str, Any]]:
    dataset = build_m7_offline_dataset(samples, lookback_days=max(1, int(lookback_days)), dedupe_latest=True)
    return list(dataset.get('rows', []))


def load_rows_for_lookback(lookback_days: int) -> list[dict[str, Any]]:
    samples = load_report_samples(lookback_days=max(1, int(lookback_days)))
    return dataset_rows_from_samples(samples, lookback_days=max(1, int(lookback_days)))
